"""The fees ledger: charges, payments, and the clearance derived from them.

Every consumer of a student's financial position calls into this module — the
student portal, the tutor's eligibility screen, the finance dashboard and every
export. That is deliberate. The bug this replaces was four separate
implementations of "what does this student owe", which disagreed with each
other: the portal reported a fully-paid student as owing double, while the
accountant's screen reported something different again for the same student.

Balances are computed, never stored. At this college's scale the aggregation is
cheap, and a derived number cannot go stale.
"""

import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import (
    BankAccount, ChargeType, CollegeProfile, FeeInstallment, FeeStructure,
    FinanceAuditLog, FinanceOverride, Invoice, InvoiceLine, Payment,
    PaymentAllocation, PaymentSchedule, SemesterRegistration, Student, StudentCharge,
    StudentProfile, StudentResidence,
)

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')
CENTS = Decimal('0.01')

# How much must be settled before a student is cleared. Set in settings so the
# college can change policy without a code change; the default is what colleges
# almost always do — pay each installment by the date it falls due.
DUE_BY_DATE = 'due_by_date'
MINIMUM_PERCENT = 'minimum_percent'
FULL_PAYMENT = 'full_payment'


def clearance_rule():
    return getattr(settings, 'FINANCE_CLEARANCE_RULE', DUE_BY_DATE)


def minimum_percent():
    return Decimal(str(getattr(settings, 'FINANCE_MINIMUM_PERCENT', 60)))


def money(value):
    return Decimal(value or 0).quantize(CENTS, rounding=ROUND_HALF_UP)


# ── audit ─────────────────────────────────────────────────────────────────────

def audit(action, entity, *, actor=None, entity_id=None, profile=None,
          summary='', before=None, after=None, ip=None):
    """Record a money-touching action. Never raises — an audit failure must not
    roll back the transaction it is describing, but it must be visible."""
    return FinanceAuditLog.objects.create(
        actor=actor, action=action, entity=entity, entity_id=entity_id,
        profile=profile, summary=summary[:300], before=before, after=after,
        ip_address=ip,
    )


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# ── profiles ──────────────────────────────────────────────────────────────────

def profile_for_student(student):
    """The person behind an enrollment, creating the profile if this enrollment
    predates it."""
    if student.profile_id:
        return student.profile
    profile, _ = StudentProfile.objects.get_or_create(
        nactvet_reg_no=student.nactvet_reg_no.strip().upper(),
        defaults={'name': student.name},
    )
    Student.objects.filter(nactvet_reg_no__iexact=student.nactvet_reg_no).update(profile=profile)
    student.profile = profile
    return profile


def registration_for(profile, academic_year=None):
    """The student's latest semester registration — within this academic year
    when one is given."""
    registrations = profile.registrations.exclude(
        status=SemesterRegistration.CANCELLED,
    ).select_related(
        'class_level', 'programme', 'semester__academic_year',
    )
    if academic_year is not None:
        registrations = registrations.filter(semester__academic_year=academic_year)
    return registrations.order_by('-semester__academic_year__name', '-semester__number').first()


def _latest_enrollment(profile, academic_year=None):
    """The enrollment that best says where a student stands, for students
    registered before semester registrations were kept.

    Latest semester first, then the highest level within it: a level 5 student
    repeating a level 4 module is a level 5 student. Enrollments used to be
    taken in their default order, which sorts by the student's name — the same
    on every row — so which one came first, and so which level was billed, was
    up to the database.
    """
    enrollments = profile.enrollments.select_related(
        'module__class_level', 'module__semester__academic_year', 'module__programme',
    )
    if academic_year is not None:
        scoped = enrollments.filter(module__semester__academic_year=academic_year)
        enrollments = scoped if scoped.exists() else enrollments
    return enrollments.order_by(
        '-module__semester__academic_year__name', '-module__semester__number',
        '-module__class_level__order', 'id',
    ).first()


def class_level_for(profile, academic_year=None):
    """The NTA level a student is studying at, which decides their fees.

    Read from their semester registration. Students enrolled before
    registrations were kept fall back to their enrollments.
    """
    registration = registration_for(profile, academic_year)
    if registration is not None:
        return registration.class_level
    enrollment = _latest_enrollment(profile, academic_year)
    return enrollment.module.class_level if enrollment else None


def programme_for(profile, academic_year=None):
    """The programme a student studies, which decides their fees alongside the
    level. None for a student whose modules are not yet linked to one — they
    are charged the amounts set for every programme."""
    registration = registration_for(profile, academic_year)
    if registration is not None:
        return registration.programme
    enrollment = _latest_enrollment(profile, academic_year)
    return enrollment.module.programme if enrollment else None


# ── the fee structure ─────────────────────────────────────────────────────────

def set_installment_schedule(fee_structure, due_dates):
    """Split a fee structure's amount across its installments and give each one
    a due date. Any rounding remainder lands on the final installment so the
    schedule always sums back to the full amount.
    """
    due_dates = list(due_dates)
    if len(due_dates) != fee_structure.installments:
        raise ValueError(
            f'{fee_structure.charge_type} at {fee_structure.class_level} is set to '
            f'{fee_structure.installments} installment(s); got {len(due_dates)} due date(s).'
        )

    total = money(fee_structure.amount)
    each = money(total / fee_structure.installments)

    fee_structure.installment_schedule.all().delete()
    rows, running = [], ZERO
    for index, due in enumerate(due_dates, start=1):
        amount = each if index < len(due_dates) else money(total - running)
        running += amount
        rows.append(FeeInstallment(
            fee_structure=fee_structure, number=index, amount=amount, due_date=due,
        ))
    return FeeInstallment.objects.bulk_create(rows)


def structures_for(class_level, academic_year, programme=None):
    """The fee structure that applies to each charge type, for a student at this
    level in this programme and year, keyed by charge type id.

    A programme's own row wins over the amount set for every programme. With no
    programme, only the every-programme amounts apply.
    """
    rows = (
        FeeStructure.objects
        .filter(class_level=class_level, academic_year=academic_year, is_active=True,
                charge_type__is_active=True)
        .filter(Q(programme__isnull=True) | Q(programme=programme) if programme is not None
                else Q(programme__isnull=True))
        .select_related('charge_type', 'programme', 'class_level')
        .prefetch_related('installment_schedule')
    )
    chosen = {}
    for structure in rows:
        if structure.programme_id or structure.charge_type_id not in chosen:
            chosen[structure.charge_type_id] = structure
    return chosen


# ── raising charges ───────────────────────────────────────────────────────────

@transaction.atomic
# ── new or continuing, and when things fall due ──────────────────────────────

def entry_for(profile, academic_year, registration=None):
    """Whether the college bills this student as new or as continuing this year.

    New: a first year, a new entrant at level 5 or 6, a readmitted student —
    they pay the one-time charges and follow the new students' instalments.
    Continuing: everybody else. Read from the registration; failing that, from
    whether the student studied here in an earlier year — last year's fees were
    kept outside this system, so earlier charges alone cannot say.
    """
    registration = registration or registration_for(profile, academic_year)
    if registration is not None:
        return (PaymentSchedule.NEW
                if registration.kind in (SemesterRegistration.NEW, SemesterRegistration.READMISSION)
                else PaymentSchedule.CONTINUING)
    earlier = (
        profile.registrations.filter(semester__academic_year__name__lt=academic_year.name)
        .exclude(status=SemesterRegistration.CANCELLED).exists()
        or profile.enrollments.filter(module__semester__academic_year__name__lt=academic_year.name).exists()
        or StudentCharge.objects.filter(profile=profile, academic_year__name__lt=academic_year.name).exists()
    )
    return PaymentSchedule.CONTINUING if earlier else PaymentSchedule.NEW


def schedule_for(academic_year, entry):
    return (PaymentSchedule.objects.filter(academic_year=academic_year, entry=entry)
            .prefetch_related('steps__amounts').first())


def _instalments(structure, schedule):
    """The instalments one fee falls due in: [(number, amount, due date,
    semester number)].

    From the payment schedule when it covers this charge type and adds up to
    the fee; otherwise the fee structure's own instalments. A schedule that
    does not add up is not trusted with the bill — the fee is still charged in
    full, on the structure's dates, and the mismatch is logged.
    """
    if schedule is not None:
        parts = [(step, line.amount) for step in schedule.steps.all() for line in step.amounts.all()
                 if line.charge_type_id == structure.charge_type_id and line.amount > ZERO]
        if parts:
            if money(sum(amount for _, amount in parts)) == money(structure.amount):
                return [(step.number, money(amount), step.due_date, step.semester_number)
                        for step, amount in parts]
            logger.warning('%s: the %s schedule gives %s for %s but the fee is %s; using the fee\'s own dates',
                           schedule.academic_year, schedule.entry,
                           money(sum(amount for _, amount in parts)), structure.charge_type, structure.amount)
    schedule_rows = list(structure.installment_schedule.all())
    if not schedule_rows:
        raise ValueError(
            f'{structure.charge_type} at {structure.class_level} has no due dates set. '
            f'Set the installment schedule before generating charges.'
        )
    return [(row.number, row.amount, row.due_date, 1) for row in schedule_rows]


def generate_charges(profile, academic_year, *, actor=None, class_level=None, programme=None,
                     entry=None, readmitted=None):
    """Raise every automatic charge this student owes for the year, and the
    hostel fee when they live in the hostel.

    Idempotent: running it twice does not double-bill, because a charge is
    unique on (profile, charge type, year, semester, installment). Other
    optional charges are assigned per student.

    `entry` — new or continuing — decides the instalment dates and whether the
    one-time charges are due; an application passes it, since the student has
    no registration yet.
    """
    level = class_level or class_level_for(profile, academic_year)
    if level is None:
        return []
    programme = programme or programme_for(profile, academic_year)
    registration = registration_for(profile, academic_year)
    if readmitted is None:
        readmitted = bool(registration and registration.kind == registration.READMISSION)
    entry = entry or entry_for(profile, academic_year, registration)
    schedule = schedule_for(academic_year, entry)

    # A student whose latest registration is a repeat sits the module(s) they
    # failed and nothing else, and pays the accountant's rate per module
    # instead of a year of programme fees. Once they pass and go back to a full
    # semester their registration says so, and the year's charges are raised
    # then — a fresh start, as the college bills it.
    if registration is not None and registration.kind == registration.REPEATING:
        audit('charges.skip_repeat', 'StudentProfile', actor=actor, profile=profile,
              summary=f'{academic_year}: repeat semester, billed per module only')
        return []

    structures = [
        structure for structure in structures_for(level, academic_year, programme).values()
        if structure.charge_type.applies == ChargeType.AUTOMATIC
    ]

    # Every fee's instalments are worked out before any is charged: a fee with
    # no dates stops the whole bill rather than leaving half of it raised.
    plan = []
    for structure in structures:
        # A "once" charge is billed one time for the whole programme — caution
        # money, admission, ID card, uniforms. It is the only thing separating a
        # first-year's bill from a continuing student's: a continuing student
        # paid it when they joined, whether or not that year's fees were kept
        # in this system. A new student is billed it unless they already have
        # been; a readmitted student starts afresh and is billed it again.
        if structure.billing_period == FeeStructure.ONCE:
            if entry == PaymentSchedule.CONTINUING:
                continue
            previous = StudentCharge.objects.filter(profile=profile, charge_type=structure.charge_type)
            if readmitted and structure.charge_type.charged_again_on_readmission:
                previous = previous.filter(academic_year=academic_year)
            if previous.exists():
                continue

        semesters = [None]
        if structure.billing_period == FeeStructure.SEMESTER:
            semesters = list(academic_year.semesters.all()) or [None]
        plan.append((structure, semesters, _instalments(structure, schedule)))

    created = []
    for structure, semesters, instalments in plan:
        for semester in semesters:
            for number, amount, due_date, _semester_number in instalments:
                charge, made = StudentCharge.objects.get_or_create(
                    profile=profile,
                    charge_type=structure.charge_type,
                    academic_year=academic_year,
                    semester=semester,
                    installment_number=number,
                    defaults={
                        'fee_structure': structure,
                        'amount': amount,
                        'due_date': due_date,
                        'source': StudentCharge.STRUCTURE,
                        'created_by': actor,
                    },
                )
                if made:
                    created.append(charge)

    residence = residence_for(profile, academic_year)
    if residence is not None and residence.residence == StudentResidence.HOSTEL:
        created += bill_hostel(profile, academic_year, from_semester_number=residence.from_semester_number,
                               actor=actor, class_level=level, programme=programme, entry=entry)

    if created:
        audit('charge.generate', 'StudentCharge', actor=actor, profile=profile,
              summary=f'Raised {len(created)} charge(s) for {academic_year}')
    return created


@transaction.atomic
def save_schedule(academic_year, entry, steps, *, actor=None, note=''):
    """Write a payment schedule: its instalments, their dates and semesters,
    and what of each charge type falls due at each. Replaces what was there.

    `steps` is [{'due_date', 'semester_number', 'amounts': {charge_type_id: amount}}],
    in order. Charges already raised keep the dates they were raised with; the
    schedule decides the dates of charges raised from now on.
    """
    if entry not in dict(PaymentSchedule.ENTRY_CHOICES):
        raise ValueError(f'{entry!r} is not new or continuing.')
    from .models import PaymentStep, PaymentStepAmount
    schedule, _ = PaymentSchedule.objects.get_or_create(academic_year=academic_year, entry=entry)
    schedule.steps.all().delete()
    for number, step in enumerate(steps, start=1):
        if not step.get('due_date'):
            raise ValueError(f'Instalment {number} has no date.')
        row = PaymentStep.objects.create(schedule=schedule, number=number, due_date=step['due_date'],
                                         semester_number=int(step.get('semester_number') or 1))
        PaymentStepAmount.objects.bulk_create([
            PaymentStepAmount(step=row, charge_type_id=int(charge_type_id), amount=money(amount))
            for charge_type_id, amount in (step.get('amounts') or {}).items() if money(amount) > ZERO
        ])
    schedule.note = note[:300]
    schedule.updated_by = actor
    schedule.save(update_fields=['note', 'updated_by', 'updated_at'])
    audit('schedule.save', 'PaymentSchedule', actor=actor, entity_id=schedule.id,
          summary=f'{academic_year} {entry}: {len(steps)} instalment(s)')
    return schedule


def _next_year(day):
    try:
        return day.replace(year=day.year + 1)
    except ValueError:              # 29 February
        return day.replace(year=day.year + 1, day=28)


@transaction.atomic
def copy_schedules(source_year, target_year, *, actor=None):
    """Start next year's schedules from this year's: the same instalments and
    amounts, each date a year on. The accountant then edits what changed."""
    copied = []
    for schedule in PaymentSchedule.objects.filter(academic_year=source_year).prefetch_related('steps__amounts'):
        if PaymentSchedule.objects.filter(academic_year=target_year, entry=schedule.entry).exists():
            continue
        copied.append(save_schedule(target_year, schedule.entry, [
            {'due_date': _next_year(step.due_date), 'semester_number': step.semester_number,
             'amounts': {line.charge_type_id: line.amount for line in step.amounts.all()}}
            for step in schedule.steps.all()
        ], actor=actor, note=f'Copied from {source_year}'))
    return copied


def schedule_mismatches(schedule):
    """Charge types the schedule covers whose instalments do not add up to the
    fee at some level — those are charged on the fee's own dates instead."""
    totals = {}
    for step in schedule.steps.all():
        for line in step.amounts.all():
            totals[line.charge_type_id] = totals.get(line.charge_type_id, ZERO) + line.amount
    problems = []
    for structure in (FeeStructure.objects.filter(academic_year=schedule.academic_year, is_active=True,
                                                  charge_type_id__in=totals)
                      .select_related('charge_type', 'class_level', 'programme')):
        if money(totals[structure.charge_type_id]) != money(structure.amount):
            problems.append({
                'charge_type': structure.charge_type.name,
                'class_level': structure.class_level.name,
                'programme': structure.programme.code if structure.programme_id else '',
                'scheduled': str(money(totals[structure.charge_type_id])),
                'fee': str(money(structure.amount)),
            })
    return problems


# ── resetting a period, and credit ────────────────────────────────────────────

def semester_two_dates(academic_year):
    """The instalment dates the year's payment schedules put in semester 2."""
    from .models import PaymentStep
    return set(PaymentStep.objects.filter(schedule__academic_year=academic_year, semester_number=2)
               .values_list('due_date', flat=True))


def charge_semester_number(charge, sem2_dates):
    """Which semester a charge is for: its own semester when it has one (the
    hostel fee, an item, an exam), otherwise the semester of the instalment it
    falls due at. A charge on the fee structure's own dates counts as
    semester 1."""
    if charge.semester_id:
        return charge.semester.number
    return 2 if charge.due_date in sem2_dates else 1


RESET_MARK = 'Fees reset: '


@transaction.atomic
def reset_fees(profile, academic_year, *, from_semester_number=1, reason, actor=None, charges=None):
    """Take a period's fees off a student's bill — a postponed year or
    semester. Never deletes: each charge is waived in full, so what was paid
    against it stands as credit, carried to the next charges raised.

    One-time charges (admission, caution money, ID card…) are for the whole
    programme, not the period, and stay. Returns the amount taken off.
    """
    sem2 = semester_two_dates(academic_year)
    pool = charges if charges is not None else (
        StudentCharge.objects.filter(profile=profile, academic_year=academic_year)
        .select_related('charge_type', 'semester', 'fee_structure'))
    total = ZERO
    for charge in pool:
        if charge.charge_type.frequency == ChargeType.ONCE or (
                charge.fee_structure_id and charge.fee_structure.billing_period == FeeStructure.ONCE):
            continue
        if charge_semester_number(charge, sem2) < from_semester_number:
            continue
        if charge.waived_amount >= charge.amount:
            continue
        total += charge.amount - charge.waived_amount
        waive_charge(charge, charge.amount, (RESET_MARK + reason)[:300], actor=actor)
    if total:
        audit('fees.reset', 'StudentProfile', actor=actor, profile=profile,
              summary=f'{academic_year} from semester {from_semester_number}: {money(total)} reversed — {reason}')
    return money(total)


@transaction.atomic
def apply_credit(profile, *, actor=None):
    """Settle outstanding charges with money the student already paid against
    charges since reversed.

    Written as one "credit carried forward" entry whose allocations take the
    money off the reversed charges and put it on the outstanding ones — they
    sum to nothing, so no income is invented and nothing is edited or deleted.
    Returns the amount carried.
    """
    charges = list(with_balances(StudentCharge.objects.filter(profile=profile)
                                 .select_related('charge_type').order_by('due_date', 'id')))
    credits = [(charge, -charge_balance(charge)) for charge in charges if charge_balance(charge) < ZERO]
    owing = [(charge, charge_balance(charge)) for charge in charges if charge_balance(charge) > ZERO]
    available = sum((amount for _, amount in credits), ZERO)
    if available <= ZERO or not owing:
        return ZERO

    moves = []
    remaining = available
    for charge, owed in owing:
        if remaining <= ZERO:
            break
        take = min(owed, remaining)
        moves.append((charge, take))
        remaining -= take
    carried = available - remaining
    taken, left = [], carried
    for charge, amount in credits:
        if left <= ZERO:
            break
        take = min(amount, left)
        taken.append((charge, -take))
        left -= take

    recorder = actor
    if recorder is None or not getattr(recorder, 'pk', None):
        from django.contrib.auth import get_user_model
        recorder = get_user_model().objects.filter(is_superuser=True).order_by('id').first()
    if recorder is None:
        logger.warning('Credit of %s for %s not carried: nobody to record it', money(carried),
                       profile.nactvet_reg_no)
        return ZERO
    payment = Payment.objects.create(
        profile=profile, amount=ZERO, payment_date=date.today(), channel=Payment.CREDIT,
        note=f'Credit carried forward: {money(carried)}', recorded_by=recorder)
    _write_allocations(payment, taken + moves)
    audit('credit.apply', 'Payment', actor=actor, entity_id=payment.id, profile=profile,
          summary=f'Carried {money(carried)} of credit onto {len(moves)} charge(s)')
    return money(carried)


def full_statement(profile):
    """Every year the student was billed, every payment since they started —
    the statement the accountant signs when they finish."""
    years = []
    charges = list(with_balances(StudentCharge.objects.filter(profile=profile)
                                 .select_related('charge_type', 'academic_year')
                                 .order_by('academic_year__name', 'due_date', 'id')))
    for charge in charges:
        charge.owed = charge_balance(charge)
        charge.paid_amount = money(charge.payable - charge.owed)
        if not years or years[-1]['year'] != charge.academic_year:
            years.append({'year': charge.academic_year, 'charges': []})
        years[-1]['charges'].append(charge)
    for entry in years:
        entry['billed'] = money(sum((c.amount for c in entry['charges']), ZERO))
        entry['waived'] = money(sum((c.waived_amount for c in entry['charges']), ZERO))
        entry['paid'] = money(sum((c.payable - charge_balance(c) for c in entry['charges']), ZERO))
        entry['balance'] = money(entry['billed'] - entry['waived'] - entry['paid'])
    payments = list(Payment.objects.filter(profile=profile).exclude(channel=Payment.CREDIT)
                    .order_by('payment_date', 'id'))
    totals = balance_for(profile)
    return {'years': years, 'payments': payments, 'totals': totals,
            'received': money(sum((p.amount for p in payments), ZERO))}


# ── the hostel ────────────────────────────────────────────────────────────────

def hostel_charge_type():
    return ChargeType.objects.filter(is_hostel=True, is_active=True).order_by('id').first()


def residence_for(profile, academic_year):
    return StudentResidence.objects.filter(profile=profile, academic_year=academic_year).first()


def bill_hostel(profile, academic_year, *, from_semester_number=1, actor=None, class_level=None,
                programme=None, entry=None):
    """Charge the hostel fee's instalments from this semester on.

    A place taken up in semester 2 is charged semester 2's instalment only.
    Each instalment carries its semester, so moving out later can take off the
    ones not yet lived.
    """
    charge_type = hostel_charge_type()
    if charge_type is None:
        return []
    level = class_level or class_level_for(profile, academic_year)
    programme = programme or programme_for(profile, academic_year)
    structure = structures_for(level, academic_year, programme).get(charge_type.id) if level else None
    if structure is None:
        logger.warning('No hostel fee set for %s at %s; %s not charged',
                       academic_year, level, profile.nactvet_reg_no)
        return []
    entry = entry or entry_for(profile, academic_year)
    semesters = {semester.number: semester for semester in academic_year.semesters.all()}
    created = []
    for number, amount, due_date, semester_number in _instalments(structure, schedule_for(academic_year, entry)):
        if semester_number < from_semester_number:
            continue
        charge, made = StudentCharge.objects.get_or_create(
            profile=profile, charge_type=charge_type, academic_year=academic_year,
            semester=semesters.get(semester_number), installment_number=number,
            defaults={'fee_structure': structure, 'amount': amount, 'due_date': due_date,
                      'source': StudentCharge.STRUCTURE, 'created_by': actor,
                      'note': 'Hostel'},
        )
        if not made and charge.waived_amount and charge.waived_reason == NOT_IN_HOSTEL:
            waive_charge(charge, ZERO, '', actor=actor)      # back in the hostel after all
        if made:
            created.append(charge)
    if created:
        audit('charge.hostel', 'StudentCharge', actor=actor, profile=profile,
              summary=f'Hostel fee for {academic_year}: {len(created)} instalment(s)')
    return created


NOT_IN_HOSTEL = 'Not living in the hostel'


@transaction.atomic
def set_residence(profile, academic_year, residence, *, actor=None, source=None,
                  from_semester_number=1, class_level=None, programme=None, entry=None):
    """Record where the student lives this year, and let the bill follow: the
    hostel fee is charged from this semester on, or — for a student who turns
    out to be a day student — what is still unpaid of it is taken off."""
    if residence not in dict(StudentResidence.RESIDENCE_CHOICES):
        raise ValueError(f'{residence!r} is not day or hostel.')
    record, _ = StudentResidence.objects.update_or_create(
        profile=profile, academic_year=academic_year,
        defaults={'residence': residence, 'from_semester_number': from_semester_number,
                  'source': source or StudentResidence.FINANCE_DESK, 'set_by': actor},
    )
    if residence == StudentResidence.HOSTEL:
        bill_hostel(profile, academic_year, from_semester_number=from_semester_number, actor=actor,
                    class_level=class_level, programme=programme, entry=entry)
    else:
        for charge in with_balances(StudentCharge.objects.filter(
                profile=profile, academic_year=academic_year, charge_type__is_hostel=True)
                .select_related('semester')):
            if charge.semester is not None and charge.semester.number < from_semester_number:
                continue
            outstanding = charge_balance(charge)
            if outstanding > ZERO and not (charge.waived_amount and charge.waived_reason != NOT_IN_HOSTEL):
                waive_charge(charge, charge.waived_amount + outstanding, NOT_IN_HOSTEL, actor=actor)
    audit('residence.set', 'StudentProfile', actor=actor, profile=profile,
          summary=f'{academic_year}: {residence} from semester {from_semester_number}')
    return record


def carry_residence(profile, academic_year, *, actor=None, class_level=None, programme=None):
    """A continuing student lives where they lived last year until they say
    otherwise."""
    if residence_for(profile, academic_year) is not None:
        return residence_for(profile, academic_year)
    last = (StudentResidence.objects.filter(profile=profile, academic_year__name__lt=academic_year.name)
            .order_by('-academic_year__name').first())
    if last is None:
        return None
    return set_residence(profile, academic_year, last.residence, actor=actor,
                         source=StudentResidence.CARRIED, class_level=class_level,
                         programme=programme, entry=PaymentSchedule.CONTINUING)


@transaction.atomic
def raise_charge(profile, charge_type, academic_year, amount, due_date, *,
                 semester=None, actor=None, note='', source=StudentCharge.ON_REQUEST):
    """Bill one student for something outside the automatic structure — a
    hostel place, a supplementary exam, a repeat module."""
    taken = StudentCharge.objects.filter(
        profile=profile, charge_type=charge_type, academic_year=academic_year, semester=semester,
    ).count()
    charge = StudentCharge.objects.create(
        profile=profile, charge_type=charge_type, academic_year=academic_year,
        semester=semester, installment_number=taken + 1, amount=money(amount),
        due_date=due_date, source=source, note=note, created_by=actor,
    )
    audit('charge.raise', 'StudentCharge', actor=actor, entity_id=charge.id, profile=profile,
          summary=f'{charge_type} {money(amount)} due {due_date}')
    return charge


class DeclarationError(ValueError):
    """A declaration that cannot be charged — no charge type or no rate set."""


def declaration_charge_type(kind):
    """The charge type the accountant linked to this kind of exam declaration."""
    charge_type = ChargeType.objects.filter(declaration=kind, is_active=True).first()
    if charge_type is None:
        label = dict(ChargeType.DECLARATION_CHOICES).get(kind, kind)
        raise DeclarationError(
            f'No charge type is linked to "{label}" declarations. The accountant links one '
            f'under Finance → Charge Types.'
        )
    return charge_type


def declaration_rate(charge_type, module):
    """What one module's declaration costs: the accountant's rate for the
    module's own programme and level, in the module's academic year."""
    year = module.semester.academic_year
    structure = structures_for(module.class_level, year, module.programme).get(charge_type.id)
    if structure is None:
        programme = f'{module.programme.code} ' if module.programme_id else ''
        raise DeclarationError(
            f'The accountant has not set a rate for {charge_type} at {programme}'
            f'{module.class_level} in {year}.'
        )
    return structure


@transaction.atomic
def declare_exam_charge(enrollment, kind, *, actor, reason=''):
    """Charge a student for a supplementary exam, special exam or repeat of one
    module, at the accountant's per-module rate.

    Declaring the same student for the same module twice returns the charge
    already raised rather than billing again. Returns (charge, created).
    """
    return charge_declaration(profile_for_student(enrollment), enrollment.module, kind,
                              actor=actor, reason=reason)


@transaction.atomic
def charge_declaration(profile, module, kind, *, actor, reason=''):
    """The same charge as `declare_exam_charge`, for a student who is not yet
    enrolled in the module — a repeater being billed at the finance desk before
    admission enrolls them. Enrolling them later finds this charge and does not
    raise a second one."""
    charge_type = declaration_charge_type(kind)
    structure = declaration_rate(charge_type, module)
    year = module.semester.academic_year

    existing = StudentCharge.objects.filter(
        profile=profile, charge_type=charge_type, module=module, academic_year=year,
    ).first()
    if existing is not None:
        return existing, False

    # Due on the date the accountant set for it, or straight away when none was.
    schedule = list(structure.installment_schedule.all())
    due_date = schedule[0].due_date if schedule else date.today()
    taken = StudentCharge.objects.filter(
        profile=profile, charge_type=charge_type, academic_year=year, semester=module.semester,
    ).count()
    charge = StudentCharge.objects.create(
        profile=profile, charge_type=charge_type, academic_year=year, semester=module.semester,
        module=module, fee_structure=structure, installment_number=taken + 1,
        amount=money(structure.amount), due_date=due_date, source=StudentCharge.ON_REQUEST,
        note=(reason or f'{charge_type} for {module.code}')[:300], created_by=actor,
    )
    audit('charge.declare', 'StudentCharge', actor=actor, entity_id=charge.id, profile=profile,
          summary=f'{charge_type} for {module.code}: {money(structure.amount)} due {due_date}')
    return charge, True


def declared_charges(module):
    """Every declaration already charged for this module, as
    {profile id: [declaration kinds]}, so the declaration screen can show who is
    already declared rather than inviting a second one."""
    declared = {}
    rows = (
        StudentCharge.objects
        .filter(module=module)
        .exclude(charge_type__declaration='')
        .values_list('profile_id', 'charge_type__declaration')
    )
    for profile_id, kind in rows:
        declared.setdefault(profile_id, []).append(kind)
    return declared


@transaction.atomic
def waive_charge(charge, amount, reason, *, actor=None):
    """Reduce what is owed without pretending money arrived, so a bursary never
    shows up in the collections report as income."""
    before = {'waived_amount': str(charge.waived_amount), 'reason': charge.waived_reason}
    charge.waived_amount = money(amount)
    charge.waived_reason = reason
    charge.waived_by = actor
    charge.save(update_fields=['waived_amount', 'waived_reason', 'waived_by'])
    audit('charge.waive', 'StudentCharge', actor=actor, entity_id=charge.id,
          profile=charge.profile, summary=f'Waived {money(amount)}: {reason}',
          before=before, after={'waived_amount': str(charge.waived_amount), 'reason': reason})
    return charge


# ── balances ──────────────────────────────────────────────────────────────────

ALLOCATED = Coalesce(
    Sum('allocations__amount'), Value(ZERO),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


def with_balances(queryset):
    """Annotate charges with what has been paid against them, so a list of N
    charges costs one query instead of N.

    The eligibility screen asks for this across every student at once; without
    the annotation it was thousands of queries per page.
    """
    return queryset.annotate(allocated=ALLOCATED)


def charge_balance(charge):
    """What is still outstanding on one charge, to two places.

    StudentCharge.balance does the arithmetic (and reuses the with_balances()
    annotation when there is one); this is the rounding boundary.
    """
    return money(charge.balance)


def outstanding_charges(profile, academic_year=None):
    qs = with_balances(StudentCharge.objects.filter(profile=profile).select_related('charge_type'))
    if academic_year is not None:
        qs = qs.filter(academic_year=academic_year)
    return [c for c in qs if charge_balance(c) > ZERO]


def balance_for(profile, academic_year=None):
    """What this person owes in total. One number for one human being — not one
    per module, which is what the old model produced."""
    charges = StudentCharge.objects.filter(profile=profile)
    if academic_year is not None:
        charges = charges.filter(academic_year=academic_year)

    billed = charges.aggregate(t=Sum('amount'))['t'] or ZERO
    waived = charges.aggregate(t=Sum('waived_amount'))['t'] or ZERO
    paid = PaymentAllocation.objects.filter(charge__in=charges).aggregate(t=Sum('amount'))['t'] or ZERO
    return {
        'billed': money(billed),
        'waived': money(waived),
        'paid': money(paid),
        'balance': money(billed - waived - paid),
    }


# ── invoices ──────────────────────────────────────────────────────────────────

def _luhn_check_digit(number):
    """A mistyped reference should fail to match rather than quietly match a
    different student's invoice."""
    total, double = 0, True
    for char in reversed(str(number)):
        digit = int(char)
        if double:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        double = not double
    return (10 - (total % 10)) % 10


def build_reference(sequence):
    base = 4000 + int(sequence)
    return f'BPH-{base}{_luhn_check_digit(base)}'


def reference_is_valid(reference):
    raw = str(reference or '').strip().upper().removeprefix('BPH-')
    if not raw.isdigit() or len(raw) < 2:
        return False
    return _luhn_check_digit(raw[:-1]) == int(raw[-1])


def group_charges_for_invoicing(charges):
    """Split charges into the invoices they belong on.

    The college banks tuition and other charges in different CRDB accounts, so
    a single invoice covering both would be unpayable — the student would have
    to make two deposits against one reference. Charges are grouped by what
    they are for, and an invoice never spans two accounts.

    Returns [(group_label, bank_account, [charges])], ordered by group.
    """
    buckets = {}
    for charge in charges:
        charge_type = charge.charge_type
        key = (charge_type.group_label, charge_type.bank_account_id)
        buckets.setdefault(key, []).append(charge)
    return [
        (label, next(iter(items)).charge_type.bank_account, items)
        for (label, _account_id), items in sorted(buckets.items(), key=lambda kv: kv[0][0])
    ]


def year_charges(profile, academic_year):
    """Every charge on this student's account for the year, paid or not.

    An invoice covers a whole payment — all of its instalments — so building
    one starts from everything billed, not only what is still outstanding.
    """
    return list(with_balances(
        StudentCharge.objects
        .filter(profile=profile, academic_year=academic_year)
        .select_related('charge_type', 'charge_type__bank_account', 'fee_structure', 'semester')
        .order_by('due_date', 'charge_type__name', 'installment_number')
    ))


def charges_in_group(profile, academic_year, group_label, bank_account_id, charges=None):
    """The charges making up one payment. Matched on the account as well as the
    label so a group split across two accounts stays two invoices."""
    return [
        charge for charge in (charges if charges is not None
                              else year_charges(profile, academic_year))
        if charge.charge_type.group_label == group_label
        and charge.charge_type.bank_account_id == bank_account_id
    ]


def invoiceable_payments(profile, academic_year):
    """The payments this student can raise an invoice for.

    The student chooses a payment — school fees, direct costs, or one of the
    other payments such as accommodation or a supplementary exam — rather than
    ticking instalments, so this answers "what can I be invoiced for, what is
    each worth, and do I already have an invoice for it?".

    Returns one entry per payment, ordered school fees → direct costs → other.
    """
    charges = year_charges(profile, academic_year)
    existing = {
        invoice.invoice_group: invoice
        for invoice in Invoice.objects
        .filter(profile=profile, academic_year=academic_year, cancelled=False)
        .select_related('bank_account')
        .prefetch_related('lines')
    }
    family_order = {family: index for index, (family, _label)
                    in enumerate(ChargeType.FAMILY_CHOICES)}

    payments = []
    for label, account, items in group_charges_for_invoicing(charges):
        billed = money(sum((c.payable for c in items), ZERO))
        outstanding = money(sum((charge_balance(c) for c in items), ZERO))
        family = items[0].charge_type.family
        payments.append({
            'family': family,
            'family_display': items[0].charge_type.get_family_display(),
            'group': label,
            'bank_account': account,
            'charges': items,
            'installments': len(items),
            'billed': billed,
            'paid': money(billed - outstanding),
            'outstanding': outstanding,
            'invoice': existing.get(label),
        })
    payments.sort(key=lambda p: (family_order.get(p['family'], 99), p['group']))
    return payments


def _invoice_expiry(academic_year, charges):
    """When an invoice stops being payable: the end of its academic year, or
    the last instalment it bills for if the college has set the year to close
    before then."""
    closes_on = academic_year.closes_on
    latest = max((charge.due_date for charge in charges), default=None)
    if closes_on is None:
        return latest
    if latest is not None and latest > closes_on:
        return latest
    return closes_on


def _sync_invoice_lines(invoice, charges):
    """Make the invoice say exactly what this payment is for, every instalment
    of it.

    Charges the college raises later in the year — a supplementary exam, a
    hostel place taken up in term two — join the invoice that already exists
    instead of starting a second one under a new reference.
    """
    existing = {line.charge_id: line for line in invoice.lines.all()}
    wanted = {charge.id: money(charge.payable) for charge in charges}

    InvoiceLine.objects.bulk_create([
        InvoiceLine(invoice=invoice, charge_id=charge_id, amount=amount)
        for charge_id, amount in wanted.items() if charge_id not in existing
    ])
    for charge_id, amount in wanted.items():
        line = existing.get(charge_id)
        if line is not None and line.amount != amount:
            line.amount = amount
            line.save(update_fields=['amount'])

    # A charge waived down to nothing stops being something to pay, so it stops
    # appearing on the bill.
    stale = [charge_id for charge_id in existing if charge_id not in wanted]
    if stale:
        invoice.lines.filter(charge_id__in=stale).delete()
    return len([charge_id for charge_id in wanted if charge_id not in existing])


@transaction.atomic
def issue_invoice(profile, charges, academic_year, *, source=Invoice.STUDENT, actor=None):
    """Issue — or bring up to date — the one invoice for a payment.

    `charges` names the payment; the invoice always covers every instalment of
    it for the academic year, and it expires when the year does. A student
    paying tuition in five instalments therefore quotes one reference on all
    five bank slips, which is what lets the accountant see the five deposits as
    one bill being worked off.

    Calling this again returns the same invoice, refreshed — it never mints a
    second reference for a payment that already has one.

    Use issue_invoices() unless the caller has already grouped them — this
    refuses a mixed set rather than producing a bill the student cannot pay.
    """
    charges = list(charges)
    if not charges:
        raise ValueError('Nothing outstanding to invoice.')

    groups = group_charges_for_invoicing(charges)
    if len(groups) > 1:
        raise ValueError(
            'Those charges are paid into different accounts and cannot share one '
            'invoice: ' + ', '.join(label for label, _account, _items in groups) + '.'
        )
    label, account, _items = groups[0]
    return _issue_group_invoice(profile, academic_year, label, account,
                                source=source, actor=actor)


@transaction.atomic
def _issue_group_invoice(profile, academic_year, label, account, *,
                         source=Invoice.STUDENT, actor=None, charges=None):
    items = [
        charge for charge in charges_in_group(
            profile, academic_year, label,
            account.id if account is not None else None, charges=charges)
        if money(charge.payable) > ZERO
    ]
    if not items:
        raise ValueError('Nothing outstanding to invoice.')

    invoice = (
        Invoice.objects
        .filter(profile=profile, academic_year=academic_year,
                invoice_group=label, cancelled=False)
        .first()
    )
    issuing = invoice is None
    if issuing:
        invoice = Invoice.objects.create(
            profile=profile, academic_year=academic_year, source=source,
            invoice_group=label, bank_account=account,
            due_date=_invoice_expiry(academic_year, items), reference='',
        )
        invoice.reference = build_reference(invoice.pk)
        invoice.save(update_fields=['reference'])

    added = _sync_invoice_lines(invoice, items)

    # The office can set the year's closing date after invoices have gone out.
    # An invoice can never expire before the last instalment it bills for —
    # that would be a bill the student is told to pay and told is out of date.
    closes_on = _invoice_expiry(academic_year, items)
    if invoice.due_date != closes_on:
        invoice.due_date = closes_on
        invoice.save(update_fields=['due_date'])

    if issuing:
        audit('invoice.issue', 'Invoice', actor=actor, entity_id=invoice.id, profile=profile,
              summary=f'{invoice.reference} · {label} · {money(invoice.total)} '
                      f'· {len(items)} instalment(s)')
    elif added:
        audit('invoice.update', 'Invoice', actor=actor, entity_id=invoice.id, profile=profile,
              summary=f'{invoice.reference} · {label} · added {added} instalment(s)')
    return invoice


@transaction.atomic
def issue_invoices(profile, charges, academic_year, *, source=Invoice.STUDENT, actor=None):
    """Issue every invoice a set of charges needs — one per payment.

    A student settling tuition and their direct costs at the same time gets two
    invoices with two references, and makes two deposits into the two accounts.
    That is what the bank actually requires.
    """
    charges = list(charges)
    if not charges:
        raise ValueError('Nothing outstanding to invoice.')
    year_wide = year_charges(profile, academic_year)
    return [
        _issue_group_invoice(profile, academic_year, label, account,
                             source=source, actor=actor, charges=year_wide)
        for label, account, _items in group_charges_for_invoicing(charges)
    ]


@transaction.atomic
def issue_invoices_for_family(profile, academic_year, family, *,
                              source=Invoice.STUDENT, actor=None):
    """Raise the invoice for one kind of payment: school fees, direct costs, or
    other payments.

    This is what the student picks from. A family is usually one payment banked
    in one account, but where the college has split it — accommodation is
    banked apart from examination charges — they get one invoice per account,
    because a single deposit cannot settle two.
    """
    charges = year_charges(profile, academic_year)
    mine = [c for c in charges if c.charge_type.family == family]
    if not mine:
        raise ValueError('There is nothing billed to you under that payment.')
    if not any(charge_balance(c) > ZERO for c in mine):
        raise ValueError('You have already settled that payment in full.')
    return [
        _issue_group_invoice(profile, academic_year, label, account,
                             source=source, actor=actor, charges=charges)
        for label, account, _items in group_charges_for_invoicing(mine)
    ]


def invoice_paid(invoice):
    """What has been received against this invoice.

    Measured on the invoice's charges rather than on payments tagged with the
    invoice: a deposit that reached the counter without the reference still
    pays those fees off, and the invoice has to say so — otherwise a student
    who paid four of five instalments sees a bill claiming nothing has arrived.
    """
    paid = PaymentAllocation.objects.filter(
        charge__invoice_lines__invoice=invoice,
    ).aggregate(t=Sum('amount'))['t'] or ZERO
    return money(paid)


def invoice_components(invoice):
    """What the invoice is made of — one row per thing being charged for.

    The invoice bills a whole year, so it holds a line per instalment. That is
    the wrong shape to read: a direct-costs bill of fourteen items split over
    two instalments is twenty-eight lines saying the same fourteen things
    twice. Collapse them back to what the student is actually being charged —
    each item, what it costs for the year, and how many instalments it is paid
    in.

    An invoice for one item needs no such table at all; the amount and the
    instalment count say everything, which is why the printed page drops the
    item list when this returns a single row.
    """
    components = {}
    for line in invoice.lines.all():
        charge_type = line.charge.charge_type
        row = components.setdefault(charge_type.id, {
            'charge_type': charge_type,
            'code': charge_type.code,
            'name': charge_type.name,
            'frequency': charge_type.frequency_label,
            'sort_order': charge_type.sort_order,
            'amount': ZERO,
            'installments': 0,
            'due_dates': [],
        })
        row['amount'] += money(line.amount)
        row['installments'] += 1
        row['due_dates'].append(line.charge.due_date)

    # The college's published order, so the bill can be checked against the
    # Other Charges table in the admission form row by row.
    rows = sorted(components.values(), key=lambda row: (row['sort_order'], row['name']))
    for row in rows:
        row['amount'] = money(row['amount'])
        row['due_dates'].sort()
    return rows


def invoice_transactions(invoice):
    """The invoice as an account: what was billed, then every payment worked
    off it, with the balance after each.

    One reference covers every instalment, so the student's question is no
    longer "was this paid" but "how much of it have I paid so far". This is the
    running statement that answers it.
    """
    charge_ids = [line.charge_id for line in invoice.lines.all()]
    total = money(invoice.total)
    rows = [{
        'date': invoice.issued_on,
        'receipt': invoice.reference,
        'bank_reference': '',
        'credit': None,
        'debit': total,
        'balance': total,
    }]

    by_payment = {}
    for allocation in (
        PaymentAllocation.objects
        .filter(charge_id__in=charge_ids)
        .select_related('payment')
    ):
        entry = by_payment.setdefault(allocation.payment_id,
                                      {'payment': allocation.payment, 'amount': ZERO})
        entry['amount'] += allocation.amount

    balance = total
    for entry in sorted(by_payment.values(),
                        key=lambda e: (e['payment'].payment_date, e['payment'].id)):
        payment, amount = entry['payment'], money(entry['amount'])
        balance -= amount
        rows.append({
            'date': payment.payment_date,
            'receipt': payment.efd_receipt_no or payment.bank_reference or '',
            'bank_reference': payment.bank_reference,
            'credit': amount if amount >= ZERO else None,
            'debit': -amount if amount < ZERO else None,
            'balance': money(balance),
        })
    return rows


# ── instalment reminders ──────────────────────────────────────────────────────

OVERDUE = 'overdue'
DUE_SOON = 'due_soon'
UPCOMING = 'upcoming'
DUE_SOON_DAYS = 14


def _days_label(days):
    """How the reminder reads to a student: "3 days overdue", "due today",
    "in 12 days"."""
    if days < 0:
        overdue = -days
        return f'{overdue} day{"" if overdue == 1 else "s"} overdue'
    if days == 0:
        return 'due today'
    return f'in {days} day{"" if days == 1 else "s"}'


def installment_reminders(profile, academic_year, *, today=None):
    """The instalment dates a student needs reminding about.

    Due dates used to be printed down the invoice, a line per instalment. That
    is the wrong place for them: the invoice is the thing you carry to the bank
    and it stands all year, so a list of future dates on it only makes the bill
    harder to read. The dates are a calendar, and a calendar belongs on the
    dashboard, where the student is told before each one arrives.

    A reminder is one deposit, not one charge. Direct costs are fourteen items
    falling due on the same two dates, and the student pays them as two
    payments — so this reminds them twice, for the whole amount each time,
    rather than twenty-eight times for fourteenths of it.

    Returns the unpaid instalments in date order, each marked overdue, due soon
    or upcoming.
    """
    today = today or date.today()
    references = {}
    for invoice in (
        Invoice.objects
        .filter(profile=profile, academic_year=academic_year, cancelled=False)
        .prefetch_related('lines')
    ):
        for line in invoice.lines.all():
            references[line.charge_id] = invoice.reference

    # Group by the payment being made and the day it falls due — that pair is
    # one trip to the bank. The schedule is built from every instalment, paid
    # or not, so a student who has settled the first is reminded about "2 of 5"
    # rather than being told they are back at the start.
    deposits, schedules = {}, {}
    for charge in year_charges(profile, academic_year):
        charge_type = charge.charge_type
        payment_key = (charge_type.group_label, charge_type.bank_account_id)
        schedules.setdefault(payment_key, set()).add(charge.due_date)

        outstanding = charge_balance(charge)
        if outstanding <= ZERO:
            continue
        deposit = deposits.setdefault((*payment_key, charge.due_date), {
            'group': charge_type.group_label,
            'due_date': charge.due_date,
            'amount': ZERO,
            'charges': [],
            'reference': '',
        })
        deposit['amount'] += outstanding
        deposit['charges'].append(charge)
        deposit['reference'] = deposit['reference'] or references.get(charge.id, '')

    positions, totals = {}, {}
    for payment_key, dates in schedules.items():
        for index, due_date in enumerate(sorted(dates), start=1):
            positions[(*payment_key, due_date)] = index
        totals[payment_key] = len(dates)

    reminders = []
    for key, deposit in deposits.items():
        group, account_id, due_date = key
        items = deposit['charges']
        days = (due_date - today).days
        if days < 0:
            urgency = OVERDUE
        elif days <= DUE_SOON_DAYS:
            urgency = DUE_SOON
        else:
            urgency = UPCOMING
        # One item makes its own name the clearer label; several are the group.
        name = items[0].charge_type.name if len({c.charge_type_id for c in items}) == 1 else group
        reminders.append({
            'name': name,
            'group': group,
            'items': len(items),
            'charges': items,
            'installment_number': positions[key],
            'installments_total': totals[(group, account_id)],
            'amount': money(deposit['amount']),
            'due_date': due_date,
            'days': days,
            'days_label': _days_label(days),
            'urgency': urgency,
            'reference': deposit['reference'],
        })
    reminders.sort(key=lambda r: (r['due_date'], r['group']))
    return reminders


def invoice_status(invoice, today=None):
    if invoice.cancelled:
        return 'cancelled'
    total, paid = money(invoice.total), invoice_paid(invoice)
    if paid >= total and total > ZERO:
        return 'paid'
    if paid > ZERO:
        return 'partly_paid'
    if invoice.due_date and invoice.due_date < (today or date.today()):
        return 'overdue'
    return 'issued'


# ── payments ──────────────────────────────────────────────────────────────────

@transaction.atomic
def record_payment(profile, amount, payment_date, *, recorded_by, invoice=None,
                   channel=Payment.CRDB, bank_reference='', efd_receipt_no='',
                   payer_name='', payer_relation=Payment.SELF, proof=None,
                   note='', allocations=None, request=None):
    """Record money the accountant has seen proof of.

    Payments are only ever created here, only by a member of staff, and only
    against physical proof — so everything in the ledger is verified by
    construction. There is no pending state for a student to mistake for
    clearance.

    `allocations` is an optional [(charge, amount)] list. Left out, the payment
    settles the invoice's lines, or the oldest outstanding charges first.
    """
    amount = money(amount)
    if amount <= ZERO:
        raise ValueError('A payment must be greater than zero.')

    payment = Payment.objects.create(
        profile=profile, invoice=invoice, amount=amount, payment_date=payment_date,
        channel=channel, bank_reference=bank_reference.strip(),
        efd_receipt_no=efd_receipt_no.strip(), payer_name=payer_name.strip(),
        payer_relation=payer_relation, proof=proof, note=note, recorded_by=recorded_by,
    )

    if allocations is None:
        allocations = _auto_allocate(profile, amount, invoice)
    _write_allocations(payment, allocations)

    audit('payment.record', 'Payment', actor=recorded_by, entity_id=payment.id, profile=profile,
          summary=f'{amount} on {payment_date} via {channel}'
                  + (f' ref {bank_reference}' if bank_reference else '')
                  + (f' EFD {efd_receipt_no}' if efd_receipt_no else ''),
          after={'amount': str(amount), 'payment_date': str(payment_date),
                 'channel': channel, 'bank_reference': bank_reference,
                 'efd_receipt_no': efd_receipt_no, 'payer_name': payer_name},
          ip=client_ip(request))
    return payment


def _auto_allocate(profile, amount, invoice):
    """Settle the invoice's own lines first, then anything else outstanding,
    oldest due date first."""
    if invoice is not None:
        candidates = [line.charge for line in invoice.lines.select_related('charge__charge_type')]
    else:
        candidates = outstanding_charges(profile)
    candidates = sorted(candidates, key=lambda c: (c.due_date, c.id))

    remaining, plan = amount, []
    for charge in candidates:
        if remaining <= ZERO:
            break
        take = min(remaining, charge_balance(charge))
        if take > ZERO:
            plan.append((charge, take))
            remaining -= take
    return plan


def _write_allocations(payment, allocations):
    rows = [
        PaymentAllocation(payment=payment, charge=charge, amount=money(amount))
        for charge, amount in allocations if money(amount) != ZERO
    ]
    PaymentAllocation.objects.bulk_create(rows)
    return rows


@transaction.atomic
def reverse_payment(payment, reason, *, actor, request=None):
    """Undo a payment without erasing it.

    Nothing in the ledger is ever edited or deleted. A correction is a new row
    carrying the opposite amount and pointing back at the original, so the
    record of what happened survives the correction.
    """
    if payment.is_reversal:
        raise ValueError('A reversal cannot itself be reversed.')
    if hasattr(payment, 'reversal'):
        raise ValueError('That payment has already been reversed.')

    reversal = Payment.objects.create(
        profile=payment.profile, invoice=payment.invoice, amount=-payment.amount,
        payment_date=payment.payment_date, channel=payment.channel,
        bank_reference=payment.bank_reference, efd_receipt_no=payment.efd_receipt_no,
        payer_name=payment.payer_name, payer_relation=payment.payer_relation,
        reverses=payment, reversal_reason=reason, recorded_by=actor,
    )
    _write_allocations(reversal, [
        (allocation.charge, -allocation.amount) for allocation in payment.allocations.all()
    ])

    audit('payment.reverse', 'Payment', actor=actor, entity_id=payment.id,
          profile=payment.profile, summary=f'Reversed {payment.amount}: {reason}',
          before={'amount': str(payment.amount)}, after={'reversal_id': reversal.id},
          ip=client_ip(request))
    return reversal


# ── clearance ─────────────────────────────────────────────────────────────────

def period_as_of(semester, period, today=None):
    """The date clearance for this period is measured at.

    A student is cleared when everything due *by the exam* is settled, so the
    semester's cutoff is the right yardstick when one is set. Falling back to
    today keeps the rule working for colleges that have not set cutoffs.
    """
    today = today or date.today()
    if semester is None:
        return today
    cutoff = {
        ChargeType.CAT1: semester.cat1_cutoff,
        ChargeType.CAT2: semester.cat2_cutoff,
        ChargeType.FINAL: semester.end_cutoff,
        ChargeType.RESULTS: semester.end_cutoff,
    }.get(period)
    return cutoff or today


def active_override(profile, academic_year, period, today=None):
    today = today or date.today()
    return (
        FinanceOverride.objects
        .filter(profile=profile, academic_year=academic_year, period=period, is_active=True)
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))
        .order_by('-created_at')
        .first()
    )


def _overridden_result(override):
    return {
        'status': override.status,
        'cleared': override.status == FinanceOverride.CLEARED,
        'balance': ZERO,
        'reason': f'{override.get_status_display()} by override: {override.reason}',
        'overridden': True,
        'charges': [],
    }


def _evaluate(charges, semester, period, today):
    """Decide clearance from charges already in memory.

    Split out from exam_clearance so the batch path can fetch once for many
    students and reuse exactly the same rule — one definition, no drift.
    """
    due = _charges_due(charges, semester, period, today)
    outstanding = [c for c in due if charge_balance(c) > ZERO]
    balance = money(sum((charge_balance(c) for c in outstanding), ZERO))
    cleared = balance <= ZERO
    if cleared:
        reason = 'Finance cleared'
    else:
        items = ', '.join(sorted({c.charge_type.name for c in outstanding}))
        reason = f'{balance} outstanding on {items}'
    return {
        'status': 'cleared' if cleared else 'blocked',
        'cleared': cleared,
        'balance': balance,
        'reason': reason,
        'overridden': False,
        'charges': outstanding,
    }


def _applies_to_semester(charge, semester):
    # Annual charges carry no semester and apply across the whole year.
    return semester is None or charge.semester_id in (None, semester.id)


def item_charge_type_ids():
    """The charge types the college's required items bill under — the TPH
    book, a calculator, rim paper, insurance's medical fee.

    A student short of one of these owes it as a debt; it never holds them from
    registering, sitting an exam or seeing results, whatever the charge type's
    own flags say. Every clearance leaves them out.
    """
    from .models import AdmissionRequirement
    return set(AdmissionRequirement.objects.filter(charge_type__isnull=False)
               .values_list('charge_type_id', flat=True))


def exam_clearance(profile, academic_year, period, *, semester=None, today=None):
    """Is this student cleared for this period, and if not, why not?

    The single definition of finance clearance. Called by the student portal,
    the tutor's eligibility screen, the finance dashboard and every export, so
    those four can never disagree about the same student.

    Use clearance_map() when you need this for a list of students.
    """
    today = today or date.today()
    field = ChargeType.PERIOD_FIELDS.get(period)
    if field is None:
        raise ValueError(f'Unknown clearance period: {period}')

    override = active_override(profile, academic_year, period, today)
    if override:
        return _overridden_result(override)

    blocking = with_balances(
        StudentCharge.objects
        .filter(profile=profile, academic_year=academic_year, **{f'charge_type__{field}': True})
        .exclude(charge_type_id__in=item_charge_type_ids())
        .select_related('charge_type')
    )
    if semester is not None:
        blocking = blocking.filter(Q(semester=semester) | Q(semester__isnull=True))
    return _evaluate(list(blocking), semester, period, today)


def clearance_map(profiles, academic_year, periods, *, semester=None, today=None):
    """Clearance for many students at once, in a fixed number of queries.

    The per-student call costs two queries; over a class list that became
    thousands. This fetches every charge and every override once and does the
    rest in memory, so the debtors list and the eligibility screen stay flat as
    the college grows.

    Returns {profile_id: {period: result}}.
    """
    today = today or date.today()
    profile_ids = [p.id if hasattr(p, 'id') else p for p in profiles]
    if not profile_ids:
        return {}

    charges = list(with_balances(
        StudentCharge.objects
        .filter(profile_id__in=profile_ids, academic_year=academic_year)
        .select_related('charge_type')
    ))
    items = item_charge_type_ids()
    by_profile = {}
    for charge in charges:
        if charge.charge_type_id in items:
            continue
        if _applies_to_semester(charge, semester):
            by_profile.setdefault(charge.profile_id, []).append(charge)

    overrides = {}
    for override in (
        FinanceOverride.objects
        .filter(profile_id__in=profile_ids, academic_year=academic_year,
                period__in=periods, is_active=True)
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))
        .order_by('created_at')
    ):
        overrides[(override.profile_id, override.period)] = override

    results = {}
    for profile_id in profile_ids:
        mine = by_profile.get(profile_id, [])
        per_period = {}
        for period in periods:
            override = overrides.get((profile_id, period))
            if override:
                per_period[period] = _overridden_result(override)
                continue
            field = ChargeType.PERIOD_FIELDS[period]
            blocking = [c for c in mine if getattr(c.charge_type, field)]
            per_period[period] = _evaluate(blocking, semester, period, today)
        results[profile_id] = per_period
    return results


def balance_map(profiles, academic_year=None):
    """Billed / waived / paid / balance for many students in two queries."""
    profile_ids = [p.id if hasattr(p, 'id') else p for p in profiles]
    if not profile_ids:
        return {}

    charges = StudentCharge.objects.filter(profile_id__in=profile_ids)
    allocations = PaymentAllocation.objects.filter(charge__profile_id__in=profile_ids)
    if academic_year is not None:
        charges = charges.filter(academic_year=academic_year)
        allocations = allocations.filter(charge__academic_year=academic_year)

    billed = {
        row['profile_id']: (row['billed'] or ZERO, row['waived'] or ZERO)
        for row in charges.values('profile_id').annotate(
            billed=Sum('amount'), waived=Sum('waived_amount'))
    }
    paid = {
        row['charge__profile_id']: row['paid'] or ZERO
        for row in allocations.values('charge__profile_id').annotate(paid=Sum('amount'))
    }

    out = {}
    for profile_id in profile_ids:
        charged, waived = billed.get(profile_id, (ZERO, ZERO))
        settled = paid.get(profile_id, ZERO)
        out[profile_id] = {
            'billed': money(charged),
            'waived': money(waived),
            'paid': money(settled),
            'balance': money(charged - waived - settled),
        }
    return out


def _charges_due(charges, semester, period, today):
    rule = clearance_rule()
    if rule == FULL_PAYMENT:
        return charges
    if rule == MINIMUM_PERCENT:
        billed = sum((c.payable for c in charges), ZERO)
        paid = sum((c.payable - charge_balance(c) for c in charges), ZERO)
        if billed <= ZERO:
            return []
        met = (paid / billed) * Decimal('100') >= minimum_percent()
        return [] if met else charges
    as_of = period_as_of(semester, period, today)
    # Registering means paying the first instalment (awamu ya kwanza), even when
    # the student registers before the date it is due by: admission runs in
    # the week before it.
    if period == ChargeType.REGISTRATION and charges:
        as_of = max(as_of, min(c.due_date for c in charges))
    return [c for c in charges if c.due_date <= as_of]
