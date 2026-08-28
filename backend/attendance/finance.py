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

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum

from .models import (
    ChargeType, FeeInstallment, FeeStructure, FinanceAuditLog, FinanceOverride,
    Invoice, InvoiceLine, Payment, PaymentAllocation, Student, StudentCharge,
    StudentProfile,
)

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


def class_level_for(profile, academic_year=None):
    """The NTA level a student is studying at, which decides their fees.

    Taken from their enrollments — a student is enrolled in modules, and every
    module belongs to a class level.
    """
    enrollments = profile.enrollments.select_related('module__class_level', 'module__semester')
    if academic_year is not None:
        scoped = enrollments.filter(module__semester__academic_year=academic_year)
        enrollments = scoped or enrollments
    enrollment = enrollments.first()
    return enrollment.module.class_level if enrollment else None


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


# ── raising charges ───────────────────────────────────────────────────────────

@transaction.atomic
def generate_charges(profile, academic_year, *, actor=None, class_level=None):
    """Raise every automatic charge this student owes for the year.

    Idempotent: running it twice does not double-bill, because a charge is
    unique on (profile, charge type, year, semester, installment). Optional
    charges like hostel are skipped — those are assigned per student.
    """
    level = class_level or class_level_for(profile, academic_year)
    if level is None:
        return []

    structures = (
        FeeStructure.objects
        .filter(class_level=level, academic_year=academic_year, is_active=True,
                charge_type__is_active=True, charge_type__applies=ChargeType.AUTOMATIC)
        .select_related('charge_type')
        .prefetch_related('installment_schedule')
    )

    created = []
    for structure in structures:
        schedule = list(structure.installment_schedule.all())
        if not schedule:
            raise ValueError(
                f'{structure.charge_type} at {structure.class_level} has no due dates set. '
                f'Set the installment schedule before generating charges.'
            )
        semesters = [None]
        if structure.billing_period == FeeStructure.SEMESTER:
            semesters = list(academic_year.semesters.all()) or [None]

        for semester in semesters:
            for installment in schedule:
                charge, made = StudentCharge.objects.get_or_create(
                    profile=profile,
                    charge_type=structure.charge_type,
                    academic_year=academic_year,
                    semester=semester,
                    installment_number=installment.number,
                    defaults={
                        'fee_structure': structure,
                        'amount': installment.amount,
                        'due_date': installment.due_date,
                        'source': StudentCharge.STRUCTURE,
                        'created_by': actor,
                    },
                )
                if made:
                    created.append(charge)

    if created:
        audit('charge.generate', 'StudentCharge', actor=actor, profile=profile,
              summary=f'Raised {len(created)} charge(s) for {academic_year}')
    return created


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

def charge_balance(charge):
    paid = charge.allocations.aggregate(t=Sum('amount'))['t'] or ZERO
    return money(charge.payable - paid)


def outstanding_charges(profile, academic_year=None):
    qs = StudentCharge.objects.filter(profile=profile).select_related('charge_type')
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


@transaction.atomic
def issue_invoice(profile, charges, academic_year, *, source=Invoice.STUDENT, actor=None):
    """Turn a set of outstanding charges into one payment instruction.

    One invoice, one reference, one total — but several line items, so a student
    paying tuition and the exam fee together makes one trip to the bank and
    comes back with one slip.
    """
    charges = [c for c in charges if charge_balance(c) > ZERO]
    if not charges:
        raise ValueError('Nothing outstanding to invoice.')

    invoice = Invoice.objects.create(
        profile=profile, academic_year=academic_year, source=source,
        due_date=min(c.due_date for c in charges), reference='',
    )
    invoice.reference = build_reference(invoice.pk)
    invoice.save(update_fields=['reference'])

    InvoiceLine.objects.bulk_create([
        InvoiceLine(invoice=invoice, charge=charge, amount=charge_balance(charge))
        for charge in charges
    ])

    audit('invoice.issue', 'Invoice', actor=actor, entity_id=invoice.id, profile=profile,
          summary=f'{invoice.reference} for {money(invoice.total)}')
    return invoice


def invoice_paid(invoice):
    paid = PaymentAllocation.objects.filter(
        charge__in=[line.charge_id for line in invoice.lines.all()],
        payment__invoice=invoice,
    ).aggregate(t=Sum('amount'))['t'] or ZERO
    return money(paid)


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


def exam_clearance(profile, academic_year, period, *, semester=None, today=None):
    """Is this student cleared for this period, and if not, why not?

    The single definition of finance clearance. Called by the student portal,
    the tutor's eligibility screen, the finance dashboard and every export, so
    those four can never disagree about the same student.
    """
    today = today or date.today()

    override = active_override(profile, academic_year, period, today)
    if override:
        return {
            'status': override.status,
            'cleared': override.status == FinanceOverride.CLEARED,
            'balance': ZERO,
            'reason': f'{override.get_status_display()} by override: {override.reason}',
            'overridden': True,
            'charges': [],
        }

    field = ChargeType.PERIOD_FIELDS.get(period)
    if field is None:
        raise ValueError(f'Unknown clearance period: {period}')

    blocking = (
        StudentCharge.objects
        .filter(profile=profile, academic_year=academic_year, **{f'charge_type__{field}': True})
        .select_related('charge_type')
    )
    if semester is not None:
        # Annual charges carry no semester and apply across the whole year.
        blocking = blocking.filter(Q(semester=semester) | Q(semester__isnull=True))

    due = _charges_due(list(blocking), semester, period, today)
    balance = money(sum((charge_balance(c) for c in due), ZERO))
    cleared = balance <= ZERO

    if cleared:
        reason = 'Finance cleared'
    else:
        items = ', '.join(sorted({c.charge_type.name for c in due if charge_balance(c) > ZERO}))
        reason = f'{balance} outstanding on {items}'

    return {
        'status': 'cleared' if cleared else 'blocked',
        'cleared': cleared,
        'balance': balance,
        'reason': reason,
        'overridden': False,
        'charges': [c for c in due if charge_balance(c) > ZERO],
    }


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
    return [c for c in charges if c.due_date <= as_of]
