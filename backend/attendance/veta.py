"""VETA courses: intakes, admission through the four desks, fees, enrollment.

A skeleton to grow from (see diagrams/design.md, Part 1). What it does now:

    PRINCIPAL / DPARC defines the course: department, name, code, duration, and
        its structure — a short course, or an NVA long course (Level I–III).
    ACCOUNTANT sets the course's fees for the year: which charge (and so which
        bank account), how much, and the instalments, due so many days after
        the intake starts.
    ADMISSION OFFICER opens an intake and sets how the college ID looks.
    THE DESKS — intake → records → finance → admission — admit a student:
        records takes the details and a next of kin (documents are not
        required: these students come from every background); finance states
        day or hostel and raises the fees; admission admits, which issues the
        college ID, the enrollment and a portal password.

PST is untouched: nothing here reads or writes a semester, a module enrollment
or a result.
"""

import logging
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from . import admissions, finance
from .models import (
    AdmissionRequirement, CourseApplication, CourseApplicationStep, CourseEnrollment, CourseFee, Intake,
    Programme, RequirementCheck, SemesterRegistration, StudentCharge, StudentProfile,
)

logger = logging.getLogger(__name__)


class VetaError(ValueError):
    """Something the VETA rules do not allow; the message says what."""


# ── intakes ───────────────────────────────────────────────────────────────────

def add_months(day, months):
    """The same day `months` later, pulled back to the month's end if needed."""
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return day.replace(year=year, month=month, day=min(day.day, last))
        except ValueError:
            continue
    raise VetaError('Could not work out the end date.')


def open_intake(*, programme, academic_year, starts_on, ends_on=None, name='', capacity=None,
                note='', actor=None):
    if not programme.is_veta:
        raise VetaError(f'{programme} is semester-based; it has semesters, not intakes.')
    if ends_on is None:
        if not programme.duration_months:
            raise VetaError(f'Give {programme} a duration first, or say when the intake ends.')
        ends_on = add_months(starts_on, programme.duration_months)
    if ends_on <= starts_on:
        raise VetaError('An intake ends after it starts.')
    return Intake.objects.create(
        programme=programme, academic_year=academic_year, starts_on=starts_on, ends_on=ends_on,
        name=(name or '').strip() or f'{programme.name} — {starts_on:%B %Y}',
        capacity=capacity, note=(note or '')[:300], created_by=actor,
    )


def places_taken(intake):
    return CourseEnrollment.objects.filter(intake=intake).exclude(
        status=CourseEnrollment.WITHDRAWN).count()


# ── one course at a time ─────────────────────────────────────────────────────

def studying_elsewhere(profile, *, leaving_out=None):
    """What the person is already studying, if anything — nobody is on two
    courses at once (questions.md Q3)."""
    current = CourseEnrollment.objects.filter(profile=profile, status=CourseEnrollment.STUDYING)
    if leaving_out is not None:
        current = current.exclude(intake=leaving_out)
    current = current.select_related('intake').first()
    if current:
        return str(current.intake)
    from .models import Semester
    active = Semester.objects.filter(is_active=True).first()
    if active and SemesterRegistration.objects.filter(
            profile=profile, semester=active).exclude(status=SemesterRegistration.CANCELLED).exists():
        return f'a semester programme ({active.label})'
    return None


# ── applications through the desks ────────────────────────────────────────────

@transaction.atomic
def open_application(*, intake, name, phone='', registration_number='', actor=None, note='',
                     profile=None):
    """The admission office takes someone on for an intake."""
    if not intake.is_open:
        raise VetaError(f'{intake} is not taking applications.')
    if profile is None:
        profile = admissions.capture_student(
            nactvet_reg_no=registration_number, name=name, phone=phone, actor=actor)
    elsewhere = studying_elsewhere(profile)
    if elsewhere:
        raise VetaError(f'{profile.name} is already studying {elsewhere}. One course at a time.')
    if CourseApplication.objects.filter(profile=profile, intake=intake,
                                        state__in=CourseApplication.OPEN_STATES).exists():
        raise VetaError(f'{profile.name} already has an application for {intake}.')
    return CourseApplication.objects.create(
        profile=profile, intake=intake, note=(note or '')[:300], created_by=actor)


def desk_blockers(application):
    """What stops the current desk from clearing, in words."""
    state = application.state
    profile = application.profile
    if state == CourseApplication.FINANCE and finance.pending_waivers(profile).exists():
        return ['A waiver on this student is waiting for DPFPA to approve or refuse.']
    if state == CourseApplication.INFORMATION:
        missing = []
        if not profile.phone:
            missing.append('a phone number')
        if not profile.gender:
            missing.append('their gender')
        if not profile.next_of_kin.exists():
            missing.append('a next of kin')
        if not profile.fee_payer:
            missing.append('who pays the fees')
        if not missing:
            return []
        listed = missing[0] if len(missing) == 1 else ', '.join(missing[:-1]) + ' and ' + missing[-1]
        return [f'Records still needs {listed}.']
    if state == CourseApplication.FINANCE:
        if not application.residence:
            return ['Finance states day or hostel.']
        if application.fees_raised_at is None:
            return ['Finance raises the course fees.']
    if state == CourseApplication.ADMISSION:
        blockers = []
        unpaid = unpaid_before_admission(application)
        if unpaid:
            blockers.append('To be paid before admission: '
                            + '; '.join(f'{name} {amount:,.2f}' for name, amount in unpaid) + '.')
        items = items_for(application)
        missing = [row['name'] for row in items if row['status'] == RequirementCheck.MISSING]
        unchecked = [row['name'] for row in items if not row['status'] and row['mandatory']]
        if missing:
            blockers.append(f'On hold — missing: {", ".join(missing)}.')
        if unchecked:
            blockers.append(f'Check first: {", ".join(unchecked)}.')
        return blockers
    return []


# ── the items a VETA course asks for (said 19 October 2026) ─────────────────

def requirements_for(application):
    """The checked-only items asked of VETA course students: the same as the
    PST ones, marked for VETA (or both), checked at the admission desk."""
    return list(AdmissionRequirement.objects.filter(
        is_active=True, kind=AdmissionRequirement.CHECK_ONLY,
        applies_to__in=[AdmissionRequirement.FOR_VETA, AdmissionRequirement.FOR_BOTH]))


def items_for(application):
    checks = {c.requirement_id: c for c in RequirementCheck.objects.filter(course_application=application)}
    return [{'requirement': r.id, 'name': r.name, 'mandatory': r.mandatory, 'value_label': r.value_label,
             'status': checks[r.id].status if r.id in checks else '',
             'value': checks[r.id].value if r.id in checks else ''}
            for r in requirements_for(application)]


@transaction.atomic
def record_item(application, requirement, status, *, value='', actor=None):
    """The admission desk marks a VETA item: has it, missing (which holds the
    student — they cannot be admitted), or waived."""
    if application.state != CourseApplication.ADMISSION:
        raise VetaError('Items are checked at the admission desk, before admitting.')
    if requirement not in requirements_for(application):
        raise VetaError(f'{requirement.name} is not asked of VETA course students.')
    if status not in dict(RequirementCheck.STATUS_CHOICES):
        raise VetaError('Has it, missing or waived.')
    value = (value or '').strip()
    if requirement.value_label and status == RequirementCheck.HAS_IT and not value:
        raise VetaError(f'Type the {requirement.value_label}.')
    check, _ = RequirementCheck.objects.update_or_create(
        course_application=application, requirement=requirement,
        defaults={'profile': application.profile, 'status': status, 'checked_by': actor,
                  'value': value[:120] if status == RequirementCheck.HAS_IT else ''})
    return check


def unpaid_before_admission(application):
    """What the accountant marked as due before admission and is still owed:
    [(fee name, amount owing)]."""
    owing = []
    charges = {(c.charge_type_id, c.installment_number): c for c in
               StudentCharge.objects.filter(profile=application.profile, intake=application.intake)}
    for fee in fees_for(application.intake):
        if fee.hostel_only and application.residence != CourseApplication.HOSTEL:
            continue
        for part in fee.instalments.all():
            if not part.before_admission:
                continue
            charge = charges.get((fee.charge_type_id, part.number))
            if charge is not None and finance.charge_balance(charge) > 0:
                owing.append((fee.charge_type.name, finance.charge_balance(charge)))
    return owing


def _step(application, action, actor, note):
    CourseApplicationStep.objects.create(application=application, desk=application.state,
                                         action=action, done_by=actor, note=(note or '')[:300])


@transaction.atomic
def clear_desk(application, *, actor=None, note=''):
    """The desk is done with them: on to the next, or admitted at the last."""
    application = CourseApplication.objects.select_for_update().get(pk=application.pk)
    if not application.is_open:
        raise VetaError('This application is closed.')
    blockers = desk_blockers(application)
    if blockers:
        raise VetaError(' '.join(blockers))
    _step(application, CourseApplicationStep.CLEARED, actor, note)
    if application.state == CourseApplication.ADMISSION:
        admit(application, actor=actor)       # sets application.issued_pin
        return application
    application.state = application.next_state
    application.save(update_fields=['state', 'updated_at'])
    return application


@transaction.atomic
def send_back(application, *, actor=None, note=''):
    application = CourseApplication.objects.select_for_update().get(pk=application.pk)
    if not application.is_open or application.state == CourseApplication.INTAKE:
        raise VetaError('There is no desk before this one.')
    if not (note or '').strip():
        raise VetaError('Say why it goes back.')
    _step(application, CourseApplicationStep.SENT_BACK, actor, note)
    route = CourseApplication.ROUTE
    application.state = route[route.index(application.state) - 1]
    application.save(update_fields=['state', 'updated_at'])
    return application


@transaction.atomic
def refuse(application, *, actor=None, note=''):
    application = CourseApplication.objects.select_for_update().get(pk=application.pk)
    if not application.is_open:
        raise VetaError('This application is closed.')
    if not (note or '').strip():
        raise VetaError('Say why it is refused.')
    _step(application, CourseApplicationStep.REFUSED, actor, note)
    application.state = CourseApplication.REFUSED
    application.save(update_fields=['state', 'updated_at'])
    # Fees raised for a place never taken are waived, not deleted.
    # What was paid stays paid (a refund is the accountant's to arrange).
    for charge in StudentCharge.objects.filter(profile=application.profile, intake=application.intake):
        unpaid = finance.charge_balance(charge)
        if unpaid > 0:
            finance.waive_charge(charge, charge.waived_amount + unpaid,
                                 f'Application refused: {note}'[:200], actor=actor)
    return application


# ── fees ──────────────────────────────────────────────────────────────────────

def fees_for(intake):
    """The course's fees for the intake's year, with their instalments."""
    return list(CourseFee.objects.filter(programme=intake.programme, academic_year=intake.academic_year)
                .select_related('charge_type__bank_account').prefetch_related('instalments'))


@transaction.atomic
def raise_fees(application, *, residence, actor=None):
    """Finance states day or hostel, and bills the course's fees for the intake:
    each instalment falls due so many days after the intake starts. Raised once;
    anything after that is the accountant's by hand (Extra Charges, waivers)."""
    application = CourseApplication.objects.select_for_update().get(pk=application.pk)
    if application.state != CourseApplication.FINANCE:
        raise VetaError('Fees are raised at the finance desk.')
    if residence not in dict(CourseApplication.RESIDENCE_CHOICES):
        raise VetaError('Day or hostel?')
    if application.fees_raised_at is not None:
        raise VetaError('The fees are already raised. Change them under the student\'s statement.')
    intake = application.intake
    fees = fees_for(intake)
    if not fees:
        raise VetaError(f'No fees are set for {intake.programme} in {intake.academic_year}. '
                        f'The accountant sets them under Fee Setup ▸ Course Fees.')
    raised = []
    for fee in fees:
        if fee.hostel_only and residence != CourseApplication.HOSTEL:
            continue
        parts = list(fee.instalments.all()) or [None]
        for number, part in enumerate(parts, start=1):
            amount = part.amount if part else fee.amount
            due = intake.starts_on + timedelta(days=part.due_after_days if part else 0)
            raised.append(StudentCharge.objects.create(
                profile=application.profile, charge_type=fee.charge_type,
                academic_year=intake.academic_year, intake=intake, installment_number=number,
                amount=finance.money(amount), due_date=due, source=StudentCharge.STRUCTURE,
                note=f'{intake}'[:200], created_by=actor,
            ))
    application.residence = residence
    application.fees_raised_at = timezone.now()
    application.save(update_fields=['residence', 'fees_raised_at', 'updated_at'])
    finance.audit('charge.course', 'StudentCharge', actor=actor, profile=application.profile,
                  summary=f'{len(raised)} charge(s) for {intake} ({residence})')
    return raised


def owing_by_intake(pairs):
    """What each (profile id, intake id) still owes on that intake's bill —
    one grouped query each for the charges and the payments, for a list."""
    from django.db.models import Sum
    from .models import PaymentAllocation
    pairs = set(pairs)
    owed = {pair: finance.ZERO for pair in pairs}
    profile_ids, intake_ids = {p for p, _ in pairs}, {i for _, i in pairs}
    for row in (StudentCharge.objects.filter(profile_id__in=profile_ids, intake_id__in=intake_ids)
                .order_by().values('profile_id', 'intake_id')
                .annotate(billed=Sum('amount'), waived=Sum('waived_amount'))):
        pair = (row['profile_id'], row['intake_id'])
        if pair in owed:
            owed[pair] += (row['billed'] or finance.ZERO) - (row['waived'] or finance.ZERO)
    for row in (PaymentAllocation.objects.filter(charge__profile_id__in=profile_ids, charge__intake_id__in=intake_ids)
                .order_by().values('charge__profile_id', 'charge__intake_id').annotate(paid=Sum('amount'))):
        pair = (row['charge__profile_id'], row['charge__intake_id'])
        if pair in owed:
            owed[pair] -= row['paid'] or finance.ZERO
    return {pair: finance.money(amount) for pair, amount in owed.items()}


def money_position(application):
    """What the admission desk weighs: billed, paid, owing, and what was due
    on or before the start date."""
    charges = list(StudentCharge.objects.filter(profile=application.profile, intake=application.intake))
    billed = sum((c.payable for c in charges), finance.ZERO)
    owing = sum((finance.charge_balance(c) for c in charges), finance.ZERO)
    due_by_start = sum((finance.charge_balance(c) for c in charges
                        if c.due_date <= application.intake.starts_on), finance.ZERO)
    return {'billed': str(finance.money(billed)), 'paid': str(finance.money(billed - owing)),
            'owing': str(finance.money(owing)), 'owing_by_start': str(finance.money(due_by_start))}


# ── admitting ─────────────────────────────────────────────────────────────────

def set_portal_pin(profile, pin, *, require_change=True):
    profile.portal_pin_hash = make_password(str(pin))
    profile.must_change_portal_password = require_change
    profile.portal_pin_set_at = timezone.now()
    profile.save(update_fields=['portal_pin_hash', 'must_change_portal_password', 'portal_pin_set_at'])


@transaction.atomic
def admit(application, *, actor=None):
    """The last desk: the college ID (the VETA pattern, the college's counter)
    and the enrollment. The first portal password is the student's last name
    in capitals, changed at the first sign-in (said 21 October 2026)."""
    intake = application.intake
    unpaid = unpaid_before_admission(application)
    if unpaid:
        raise VetaError('To be paid before admission: '
                        + '; '.join(f'{name} {amount:,.2f}' for name, amount in unpaid) + '.')
    if intake.capacity and places_taken(intake) >= intake.capacity:
        raise VetaError(f'{intake} is full ({intake.capacity} places).')
    elsewhere = studying_elsewhere(application.profile, leaving_out=intake)
    if elsewhere:
        raise VetaError(f'{application.profile.name} is already studying {elsewhere}.')
    profile = application.profile
    admissions.issue_college_id(profile, intake.academic_year, intake.programme)
    enrollment, _ = CourseEnrollment.objects.get_or_create(
        profile=profile, intake=intake,
        defaults={'application': application, 'residence': application.residence or 'day',
                  'admitted_by': actor,
                  'nva_level': 1 if intake.programme.structure == Programme.NVA else None},
    )
    application.state = CourseApplication.ADMITTED
    application.save(update_fields=['state', 'updated_at'])
    from . import passwords
    passwords.give_first_password(profile, actor=actor)
    application.issued_pin = None
    logger.info('Admitted %s to %s', profile.nactvet_reg_no, intake)
    return enrollment


def reset_portal_pin(profile):
    pin = admissions.generate_portal_pin()
    set_portal_pin(profile, pin)
    return pin


def current_enrollment(profile):
    return (CourseEnrollment.objects.filter(profile=profile)
            .select_related('intake__programme__department', 'intake__academic_year')
            .order_by('-admitted_at').first())


def veta_profile_for_login(identifier):
    """The VETA student signing in with their registration number or college ID."""
    from django.db.models import Q
    reg = (identifier or '').strip()
    if not reg:
        return None
    return (StudentProfile.objects.filter(Q(nactvet_reg_no__iexact=reg) | Q(college_id__iexact=reg))
            .exclude(portal_pin_hash='').filter(course_enrollments__isnull=False).distinct().first())
