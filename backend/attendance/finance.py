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
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import (
    BankAccount, ChargeType, CollegeProfile, FeeInstallment, FeeStructure,
    FinanceAuditLog, FinanceOverride, Invoice, InvoiceLine, Payment,
    PaymentAllocation, Student, StudentCharge, StudentProfile,
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
        # A "once" charge is billed one time for the whole programme — caution
        # money, admission, ID card, uniforms. It is the only thing separating a
        # first-year's bill from a continuing student's, so skip it the moment
        # the student has ever been charged it, in any year.
        if structure.billing_period == FeeStructure.ONCE:
            already = StudentCharge.objects.filter(
                profile=profile, charge_type=structure.charge_type).exists()
            if already:
                continue

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
    by_profile = {}
    for charge in charges:
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
    return [c for c in charges if c.due_date <= as_of]
