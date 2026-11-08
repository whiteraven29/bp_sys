"""Time to pay (said 6 November 2026): what the college lets a student owe for
now, and until when — the two agreements DPFPA makes.

A waiver: a shortfall on one instalment of tuition or direct costs, until the
day the student says they will pay. DPFPA may move that day later.

A monthly plan: tuition only, a sum agreed by DPFPA at the end of each month,
with seven days' grace. A month missed past its grace and the plan lapses for
good: the student is back on the normal instalments.

Neither touches the bill, so what was billed, paid and collected stays what the
auditors read. They only say how much of what is owed does not count against
the student today (`relief_for`), which the pass and the finance clearance both
read. Everything here is worked out from the bill and the payments, by the date
on the bank slip: a slip paid on time and recorded late does not lapse a plan,
and money arriving after a plan lapsed does not revive it.
"""

import calendar
import math
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import finance, notifications
from .models import ChargeType, MonthlyPlan, Notification, PaymentAllocation, StudentCharge, WaiverRequest

GRACE_DAYS = 7
TUITION = ChargeType.FEE
ZERO = finance.ZERO
ON_TRACK, IN_GRACE = 'on_track', 'in_grace'


class PlanError(ValueError):
    """A monthly plan that cannot be made as asked."""


def month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _next_month_end(end):
    return month_end(end + timedelta(days=1))


def plan_rows(monthly_amount, total, first_due):
    """Each month-end, what it asks, and what should be paid by then in all."""
    rows, paid, due = [], ZERO, first_due
    while paid < total:
        amount = min(monthly_amount, total - paid)
        paid += amount
        rows.append({'number': len(rows) + 1, 'due': due, 'amount': amount, 'should_have_paid': paid})
        due = _next_month_end(due)
    return rows


def tuition_charges(profile, academic_year):
    return list(finance.with_balances(
        StudentCharge.objects.filter(profile=profile, academic_year=academic_year, charge_type__family=TUITION)
        .exclude(finance.billed_as_missing()).select_related('charge_type')))


def _owed(charges):
    return sum((max(finance.charge_balance(c), ZERO) for c in charges), ZERO)


def _tuition_paid(profile_ids, academic_year):
    """{profile id: [(bank slip date, amount)]} on tuition; a reversed payment
    and its reversal count for nothing."""
    rows = {}
    for profile_id, day, amount in (PaymentAllocation.objects.filter(
            charge__profile_id__in=profile_ids, charge__academic_year=academic_year, charge__charge_type__family=TUITION,
            payment__reverses__isnull=True, payment__reversal__isnull=True)
            .values_list('charge__profile_id', 'payment__payment_date', 'amount')):
        rows.setdefault(profile_id, []).append((day, amount))
    return rows


def position(plan, today=None, *, paid=None, owed=None):
    """Where a running plan stands: state (on_track, in_grace, lapsed,
    completed or cancelled), what is paid under it, each month, the next month
    owed, and the day the plan's protection runs to."""
    today = today or date.today()
    if paid is None:
        paid = _tuition_paid([plan.profile_id], plan.academic_year).get(plan.profile_id, [])
    if owed is None:
        owed = _owed(tuition_charges(plan.profile, plan.academic_year))

    def under_plan(by=None):
        return max(sum((amount for day, amount in paid if by is None or day <= by), ZERO) - plan.paid_before, ZERO)

    grace = timedelta(days=GRACE_DAYS)
    paid_now = under_plan()
    rows = plan_rows(plan.monthly_amount, plan.total, plan.first_due)
    lapsed_on = None
    for row in rows:
        deadline = row['due'] + grace
        if deadline >= today:
            break
        if under_plan(deadline) < row['should_have_paid']:
            lapsed_on = deadline
            break
    next_row = next((row for row in rows if paid_now < row['should_have_paid']), None)
    if owed <= ZERO:
        state = MonthlyPlan.COMPLETED if paid_now >= plan.total else MonthlyPlan.CANCELLED
        next_row = None
    elif lapsed_on is not None:
        state = MonthlyPlan.LAPSED
    elif next_row is not None and next_row['due'] < today:
        state = IN_GRACE
    else:
        state = ON_TRACK
    return {
        'state': state, 'paid': paid_now, 'remaining': owed, 'rows': rows, 'next': next_row, 'lapsed_on': lapsed_on,
        'months_paid': sum(1 for row in rows if paid_now >= row['should_have_paid']),
        'until': next_row['due'] + grace if state in (ON_TRACK, IN_GRACE) and next_row else None,
    }


def _waiver_charges(waiver, charges):
    """The charges a waiver's shortfall sits on, those that hold a student
    first: an item (the medical fee) never holds anybody."""
    if waiver.charge_id:
        return [c for c in charges if c.id == waiver.charge_id]
    items = finance.item_charge_type_ids()
    mine = [c for c in charges if c.profile_id == waiver.profile_id and c.charge_type.family == waiver.family
            and c.installment_number == waiver.installment_number
            and (c.source == StudentCharge.STRUCTURE or c.charge_type_id not in items)]
    return sorted(mine, key=lambda c: (c.charge_type_id in items, c.due_date, c.charge_type.name, c.id))


def _shortfall(waiver):
    # The first requests said what is waived on the charge in all.
    return waiver.amount - waiver.charge.waived_amount if waiver.charge_id else waiver.amount


def relief_for(profile_ids, academic_year, today=None, *, charges=None):
    """What does not count against these students today: ({charge id: amount},
    {profile id: [(the day it runs to, why)]}). Two queries when nobody has
    such an agreement, so a whole class's clearance stays quick."""
    today = today or date.today()
    waivers = list(WaiverRequest.objects.filter(status=WaiverRequest.APPROVED, pay_by__gte=today,
                                                profile_id__in=profile_ids)
                   .filter(Q(academic_year=academic_year) | Q(charge__academic_year=academic_year))
                   .select_related('charge').order_by('pay_by', 'id'))
    plans = list(MonthlyPlan.objects.filter(status=MonthlyPlan.ACTIVE, academic_year=academic_year,
                                            profile_id__in=profile_ids))
    if not waivers and not plans:
        return {}, {}
    if charges is None:
        charges = list(finance.with_balances(
            StudentCharge.objects.filter(profile_id__in=profile_ids, academic_year=academic_year)
            .select_related('charge_type')))
    relief, caps = {}, {}
    items = finance.item_charge_type_ids()
    if plans:
        paid = _tuition_paid([plan.profile_id for plan in plans], academic_year)
        for plan in plans:
            tuition = [c for c in charges if c.profile_id == plan.profile_id and c.charge_type.family == TUITION
                       and (c.source == StudentCharge.STRUCTURE or c.charge_type_id not in items)]
            where = position(plan, today, paid=paid.get(plan.profile_id, []), owed=_owed(tuition))
            if where['state'] in (ON_TRACK, IN_GRACE):
                for charge in tuition:
                    if finance.charge_balance(charge) > ZERO:
                        relief[charge.id] = finance.charge_balance(charge)
                caps.setdefault(plan.profile_id, []).append(
                    (where['until'], f"monthly plan: month {where['next']['number']} of {len(where['rows'])}, "
                                     f"{where['next']['amount']:,.2f} due {where['next']['due']:%d %b %Y}"))
    for waiver in waivers:
        left = _shortfall(waiver)
        for charge in _waiver_charges(waiver, charges):
            take = min(left, finance.charge_balance(charge) - relief.get(charge.id, ZERO))
            if take <= ZERO:
                continue
            relief[charge.id] = relief.get(charge.id, ZERO) + take
            left -= take
            if charge.charge_type_id not in items:
                reason = f'{take:,.2f} to be paid by {waiver.pay_by:%d %b %Y}'
                caps.setdefault(waiver.profile_id, []).append((waiver.pay_by, reason))
            if left <= ZERO:
                break
    return relief, caps


# ── a waiver's date ───────────────────────────────────────────────────────────

def clean_pay_by(pay_by, today=None):
    if pay_by is None:
        raise finance.WaiverError('Write the day the student says they will pay.')
    if pay_by <= (today or date.today()):
        raise finance.WaiverError('The day they will pay must be after today.')
    return pay_by


def outstanding_on(waiver):
    """What is still unpaid of what the waiver covers."""
    year = waiver.academic_year or waiver.charge.academic_year
    charges = list(finance.with_balances(StudentCharge.objects.filter(profile_id=waiver.profile_id, academic_year=year)
                                         .select_related('charge_type')))
    balance = sum((max(finance.charge_balance(c), ZERO) for c in _waiver_charges(waiver, charges)), ZERO)
    return min(balance, _shortfall(waiver))


def waiver_state(waiver, today=None, *, outstanding=None):
    today = today or date.today()
    outstanding = outstanding_on(waiver) if outstanding is None else outstanding
    return 'paid' if outstanding <= ZERO else 'running' if waiver.pay_by >= today else 'expired'


@transaction.atomic
def extend_waiver(waiver, pay_by, reason, *, actor=None):
    """DPFPA gives more time; the date before it stays in the audit trail."""
    waiver = WaiverRequest.objects.select_for_update(of=('self',)).select_related('charge', 'profile').get(pk=waiver.pk)
    reason = (reason or '').strip()
    if waiver.status != WaiverRequest.APPROVED or waiver.pay_by is None:
        raise finance.WaiverError('Only an approved waiver with a date can be given more time.')
    if outstanding_on(waiver) <= ZERO:
        raise finance.WaiverError('This waiver is paid: there is nothing to give time for.')
    clean_pay_by(pay_by)
    if pay_by <= waiver.pay_by:
        raise finance.WaiverError(f'Choose a day after {waiver.pay_by:%d %b %Y}, the date it has now.')
    if not reason:
        raise finance.WaiverError('Say why they are given more time.')
    before = waiver.pay_by
    waiver.pay_by = pay_by
    waiver.times_extended += 1
    waiver.save(update_fields=['pay_by', 'times_extended'])
    label = finance.waiver_label(waiver)
    finance.audit('waiver.extend', 'WaiverRequest', actor=actor, entity_id=waiver.id, profile=waiver.profile,
                  summary=f'{label}: pay-by date moved from {before:%d %b %Y} to {pay_by:%d %b %Y}: {reason}',
                  before={'pay_by': str(before)}, after={'pay_by': str(pay_by)})
    notifications.notify(notifications.accountants(), kind=Notification.WAIVER_DECIDED,
                         title=f'More time: {waiver.profile.name}',
                         body=f'{label}: to pay by {pay_by:%d %b %Y}. {reason}', link='finance:timetopay')
    notifications.notify(profile=waiver.profile, kind=Notification.WAIVER_DECIDED, title='More time to pay',
                         body=f'You have until {pay_by:%d %b %Y} to pay what is short. Your pass runs to that day.')
    return waiver


# ── a monthly plan ────────────────────────────────────────────────────────────

def open_plan(profile, academic_year):
    return MonthlyPlan.objects.filter(profile=profile, academic_year=academic_year,
                                      status__in=[MonthlyPlan.PENDING, MonthlyPlan.ACTIVE]).first()


def _tuition_waivers(profile, academic_year, status):
    return (WaiverRequest.objects.filter(profile=profile, status=status)
            .filter(Q(academic_year=academic_year, family=TUITION)
                    | Q(charge__academic_year=academic_year, charge__charge_type__family=TUITION))
            .select_related('charge__charge_type'))


def tuition_waiver_waiting(profile, academic_year):
    return _tuition_waivers(profile, academic_year, WaiverRequest.PENDING).exists()


def waivers_a_plan_replaces(profile, academic_year):
    """Approved tuition waivers with a date and money still owed: a monthly
    plan takes them over when DPFPA agrees it (said 8 November 2026)."""
    return [waiver for waiver in _tuition_waivers(profile, academic_year, WaiverRequest.APPROVED)
            .filter(pay_by__isnull=False) if outstanding_on(waiver) > ZERO]


# ── waivers written off before 6 November 2026, put back on the bill ──────────

def old_waivers(profile, academic_year):
    """Tuition waivers approved before waivers had dates: the money came off
    the bill. A monthly plan may put it back (said 8 November 2026)."""
    return list(_tuition_waivers(profile, academic_year, WaiverRequest.APPROVED).filter(pay_by__isnull=True))


def restorable(waiver):
    """[(charge, amount)]: what this old waiver took off, still off — found by
    the reason it wrote on the charges ("<reason> — approved by …")."""
    if waiver.charge_id:
        candidates = [waiver.charge]
    else:
        year = waiver.academic_year
        candidates = list(StudentCharge.objects.filter(
            profile_id=waiver.profile_id, academic_year=year, charge_type__family=waiver.family,
            installment_number=waiver.installment_number).select_related('charge_type').order_by('due_date', 'id'))
    mark = f'{waiver.reason} — approved by'[:60]
    left, found = waiver.amount, []
    for charge in candidates:
        if left <= ZERO:
            break
        if charge.waived_amount > ZERO and charge.waived_reason.startswith(mark):
            take = min(left, charge.waived_amount)
            found.append((charge, take))
            left -= take
    return found


def restorable_amount(waiver):
    return sum((amount for _, amount in restorable(waiver)), ZERO)


def _put_back(waiver, plan, actor):
    for charge, amount in restorable(waiver):
        rest = charge.waived_amount - amount
        finance.waive_charge(charge, rest, charge.waived_reason if rest > ZERO else '', actor=actor)
    waiver.status = WaiverRequest.REPLACED
    waiver.decision_note = (f'Put back on the bill and into the monthly plan agreed on {timezone.localdate():%d %b %Y}. '
                            f'{waiver.decision_note}').strip()[:300]
    waiver.save(update_fields=['status', 'decision_note'])
    finance.audit('waiver.restore', 'WaiverRequest', actor=actor, entity_id=waiver.id, profile=waiver.profile,
                  summary=f'{finance.waiver_label(waiver)}: {waiver.amount:,.2f} waived before put back on the bill, '
                          f'into the monthly tuition plan.')


def _refresh_invoices(profile, academic_year):
    """An invoice already issued says the new amount: it never refreshes itself."""
    from .models import Invoice
    for invoice in Invoice.objects.filter(profile=profile, academic_year=academic_year, cancelled=False):
        try:
            finance._issue_group_invoice(profile, academic_year, invoice.invoice_group, invoice.bank_account)
        except ValueError:
            pass                                    # nothing outstanding on it: left as it stands


def monthly_for(total, months):
    """The amount a month for this many months, rounded up to the shilling."""
    return Decimal(math.ceil(total / months))


def _check_fits(monthly_amount, total, first_due, academic_year):
    rows = plan_rows(monthly_amount, total, first_due)
    closes = academic_year.closes_on
    if closes and rows[-1]['due'] > closes:
        raise PlanError(f'At {monthly_amount:,.2f} a month it ends on {rows[-1]["due"]:%d %b %Y}, after the year '
                        f'closes on {closes:%d %b %Y}. Ask for more a month.')
    return rows


@transaction.atomic
def propose_plan(profile, academic_year, monthly_amount, first_month, reason, *, actor=None, today=None,
                 months=None, restore=()):
    """The accountant asks; DPFPA decides. Nothing changes until DPFPA agrees,
    and the student waits at the finance desk meanwhile.

    Set by the timeframe (said 8 November 2026): `monthly_amount` (the months
    follow) or `months` (the amount follows). `restore`: old tuition waivers
    whose money goes back on the bill, into the plan, when DPFPA agrees."""
    today = today or date.today()
    sync_plans(profile=profile, academic_year=academic_year, today=today)
    reason = (reason or '').strip()
    monthly_amount = finance.money(monthly_amount or 0)
    if open_plan(profile, academic_year) is not None:
        raise PlanError('This student already has a monthly plan this year.')
    if tuition_waiver_waiting(profile, academic_year):
        raise PlanError('A waiver on their tuition is waiting for DPFPA: it is decided first.')
    old = {waiver.id: waiver for waiver in old_waivers(profile, academic_year)}
    putting_back = []
    for waiver_id in restore:
        waiver = old.get(int(waiver_id)) if str(waiver_id).isdigit() else None
        if waiver is None or restorable_amount(waiver) <= ZERO:
            raise PlanError('Only a tuition waiver of theirs approved before waivers had dates can be put back.')
        putting_back.append(waiver)
    total = _owed(tuition_charges(profile, academic_year)) + sum(
        (restorable_amount(waiver) for waiver in putting_back), ZERO)
    if total <= ZERO:
        raise PlanError('Nothing is owed on their tuition this year.')
    if monthly_amount > ZERO and months:
        raise PlanError('Give the amount a month or the number of months, not both.')
    if months:
        if not 1 <= int(months) <= 36:
            raise PlanError('Between 1 and 36 months.')
        months = int(months)
        monthly_amount = monthly_for(total, months)
    elif monthly_amount <= ZERO:
        raise PlanError('Write how much they pay each month, or over how many months.')
    elif monthly_amount > total:
        raise PlanError(f'More than the {total:,.2f} they owe on tuition.')
    if first_month is None:
        raise PlanError('Choose the first month.')
    first_due = month_end(first_month)
    if first_due < today:
        raise PlanError('The first month-end has passed.')
    if not reason:
        raise PlanError('Say why.')
    rows = _check_fits(monthly_amount, total, first_due, academic_year)
    plan = MonthlyPlan.objects.create(profile=profile, academic_year=academic_year, monthly_amount=monthly_amount,
                                      months=months or None, first_due=first_due, total=total, reason=reason[:300],
                                      requested_by=actor)
    plan.restores.set(putting_back)
    finance.audit('plan.request', 'MonthlyPlan', actor=actor, entity_id=plan.id, profile=profile,
                  summary=f'Asked DPFPA for a monthly tuition plan: {monthly_amount:,.2f} a month from '
                          f'{first_due:%d %b %Y}, {len(rows)} months, {total:,.2f} in all: {reason}')
    notifications.notify(notifications.dpfpa(), kind=Notification.WAIVER_REQUESTED,
                         title=f'Monthly plan to approve: {profile.name}',
                         body=f'{monthly_amount:,.2f} a month from {first_due:%d %b %Y}, {total:,.2f} tuition. {reason}',
                         link='executive:waivers')
    return plan


@transaction.atomic
def decide_plan(plan, *, approve, actor=None, note='', today=None):
    """DPFPA's yes starts the plan on what is owed that day."""
    today = today or date.today()
    plan = MonthlyPlan.objects.select_for_update(of=('self',)).select_related('profile', 'academic_year').get(pk=plan.pk)
    note = (note or '').strip()
    if plan.status != MonthlyPlan.PENDING:
        raise PlanError('This plan has already been decided.')
    if not approve and not note:
        raise PlanError('Say why it is refused.')
    if approve:
        if plan.first_due < today:
            raise PlanError('The first month-end has passed. Refuse it, and the accountant can ask again.')
        # The old waivers go back on the bill first: what they wrote off is
        # owed again, and the plan pays it off with the rest.
        restored = [waiver for waiver in plan.restores.all() if waiver.status == WaiverRequest.APPROVED]
        for waiver in restored:
            _put_back(waiver, plan, actor)
        if restored:
            _refresh_invoices(plan.profile, plan.academic_year)
        owed = _owed(tuition_charges(plan.profile, plan.academic_year))
        if owed <= ZERO:
            raise PlanError('Nothing is owed on their tuition now. Refuse it, saying it was paid.')
        if plan.months:
            plan.monthly_amount = monthly_for(owed, plan.months)
        _check_fits(plan.monthly_amount, owed, plan.first_due, plan.academic_year)
        plan.total = owed
        plan.paid_before = sum((amount for _, amount in _tuition_paid([plan.profile_id], plan.academic_year)
                                .get(plan.profile_id, [])), ZERO)
    if approve and tuition_waiver_waiting(plan.profile, plan.academic_year):
        raise PlanError('A waiver on their tuition is waiting for you: decide it first.')
    plan.status = MonthlyPlan.ACTIVE if approve else MonthlyPlan.REFUSED
    plan.decided_by, plan.decided_at, plan.decision_note = actor, timezone.now(), note[:300]
    plan.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note', 'total', 'paid_before',
                             'monthly_amount'])
    if approve:
        # The plan takes over a tuition waiver's date: the money it covered was
        # never off the bill, so it is in the plan's total.
        for waiver in waivers_a_plan_replaces(plan.profile, plan.academic_year):
            waiver.status = WaiverRequest.REPLACED
            waiver.decision_note = (f'Replaced by the monthly plan agreed on {timezone.localdate():%d %b %Y}. '
                                    f'{waiver.decision_note}').strip()[:300]
            waiver.save(update_fields=['status', 'decision_note'])
            finance.audit('waiver.replace', 'WaiverRequest', actor=actor, entity_id=waiver.id, profile=plan.profile,
                          summary=f'{finance.waiver_label(waiver)} (to pay by {waiver.pay_by:%d %b %Y}) replaced by '
                                  f'the monthly tuition plan.')
    finance.audit('plan.decide', 'MonthlyPlan', actor=actor, entity_id=plan.id, profile=plan.profile,
                  summary=f"{'Approved' if approve else 'Refused'} the monthly tuition plan: "
                          f'{plan.monthly_amount:,.2f} a month, {plan.total:,.2f} in all' + (f': {note}' if note else ''))
    notifications.notify([u for u in [plan.requested_by] if u is not None] or notifications.accountants(),
                         kind=Notification.WAIVER_DECIDED,
                         title=f"Monthly plan {'approved' if approve else 'refused'}: {plan.profile.name}",
                         body=note or f'{plan.monthly_amount:,.2f} a month from {plan.first_due:%d %b %Y}.',
                         link='finance:timetopay')
    if approve:
        notifications.notify(profile=plan.profile, kind=Notification.WAIVER_DECIDED, title='Monthly tuition plan agreed',
                             body=f'Pay {plan.monthly_amount:,.2f} by the end of each month, from {plan.first_due:%d %b %Y}. '
                                  f'Miss a month by more than {GRACE_DAYS} days and you go back to the normal instalments.')
    return plan


def sync_plans(*, profile=None, academic_year=None, today=None):
    """Write down the running plans that have ended, once, and tell those who
    need to know. The money rules never wait for this: `position` works it out."""
    today = today or date.today()
    plans = MonthlyPlan.objects.filter(status=MonthlyPlan.ACTIVE).select_related('profile', 'academic_year')
    if profile is not None:
        plans = plans.filter(profile=profile)
    if academic_year is not None:
        plans = plans.filter(academic_year=academic_year)
    for plan in plans:
        where = position(plan, today)
        if where['state'] in (ON_TRACK, IN_GRACE):
            continue
        plan.status = where['state']
        plan.ended_on = where['lapsed_on'] or today
        plan.save(update_fields=['status', 'ended_on'])
        finance.audit('plan.end', 'MonthlyPlan', entity_id=plan.id, profile=plan.profile,
                      summary=f'Monthly tuition plan: {plan.get_status_display()}, {where["paid"]:,.2f} paid under it.')
        if plan.status == MonthlyPlan.LAPSED:
            notifications.notify(notifications.accountants() + notifications.dpfpa(), kind=Notification.WAIVER_DECIDED,
                                 title=f'Monthly plan lapsed: {plan.profile.name}',
                                 body=f'A month unpaid past its {GRACE_DAYS} days of grace. {where["remaining"]:,.2f} still owed; '
                                      'back on the normal instalments.', link='finance:timetopay')
            notifications.notify(profile=plan.profile, kind=Notification.WAIVER_DECIDED, title='Monthly plan ended',
                                 body=f'A month was not paid within its {GRACE_DAYS} days of grace, so you are back on '
                                      'the normal instalments. See the Accounts Office.')


# ── what the offices and the student read ─────────────────────────────────────

def _plan_row(plan, today, names):
    live = plan.status == MonthlyPlan.ACTIVE
    where = position(plan, today) if plan.status != MonthlyPlan.PENDING else None
    rows = plan_rows(plan.monthly_amount, plan.total, plan.first_due)
    paid = where['paid'] if where else ZERO
    state = where['state'] if live else plan.status
    months = []
    for row in rows:
        before = row['should_have_paid'] - row['amount']
        got = min(max(paid - before, ZERO), row['amount'])
        months.append({'number': row['number'], 'due': row['due'], 'amount': str(row['amount']), 'paid': str(got),
                       'state': 'paid' if got >= row['amount'] else 'part' if got > ZERO else 'unpaid'})
    return {
        'id': plan.id, 'status': plan.status, 'state': state, 'status_display': plan.get_status_display(),
        'profile_id': plan.profile_id, 'name': plan.profile.name, 'reg_no': plan.profile.nactvet_reg_no,
        'college_id': plan.profile.college_id or '', 'monthly_amount': str(plan.monthly_amount),
        'total': str(plan.total), 'paid': str(paid), 'remaining': str(where['remaining'] if where else plan.total),
        'months': months, 'months_paid': where['months_paid'] if where else 0,
        'next_due': where['next']['due'] if where and live and where['next'] else None,
        'next_amount': str(where['next']['amount']) if where and live and where['next'] else None,
        'until': where['until'] if where and live else None, 'ended_on': plan.ended_on, 'reason': plan.reason,
        'requested_by': names(plan.requested_by), 'requested_at': plan.requested_at,
        'decided_by': names(plan.decided_by), 'decision_note': plan.decision_note,
        'replaces': ([f'{finance.waiver_label(w)}: {outstanding_on(w):,.2f}, to pay by {w.pay_by:%d %b %Y}'
                      for w in waivers_a_plan_replaces(plan.profile, plan.academic_year)]
                     if plan.status == MonthlyPlan.PENDING else []),
        'puts_back': ([f'{finance.waiver_label(w)}: {restorable_amount(w):,.2f}, waived before'
                       for w in plan.restores.all()] if plan.status == MonthlyPlan.PENDING else []),
        'months_set': plan.months,
    }


def _names():
    from .views import full_name_for
    known = {}

    def name(user):
        if user is None:
            return ''
        if user.pk not in known:
            known[user.pk] = full_name_for(user)
        return known[user.pk]
    return name


def plan_options(profile, academic_year):
    """What the plan form needs: tuition owed, the old waivers that may go
    back on the bill, and the dated waivers a plan would replace."""
    return {
        'tuition_owed': str(_owed(tuition_charges(profile, academic_year))),
        'old_waivers': [{'id': w.id, 'what': finance.waiver_label(w), 'amount': str(restorable_amount(w)),
                         'reason': w.reason} for w in old_waivers(profile, academic_year) if restorable_amount(w) > ZERO],
        'replaces': [{'what': finance.waiver_label(w), 'outstanding': str(outstanding_on(w)), 'pay_by': w.pay_by}
                     for w in waivers_a_plan_replaces(profile, academic_year)],
        'closes_on': academic_year.closes_on, 'academic_year_id': academic_year.id,
    }


def plan_row(plan, today=None):
    return _plan_row(plan, today or date.today(), _names())


def summary(academic_year, today=None):
    """Every agreement of the year and the money under it, for DPFPA and finance
    to follow until the student has paid. It reads the bill; it adds nothing to
    what was billed or collected."""
    today = today or date.today()
    sync_plans(academic_year=academic_year, today=today)
    names = _names()
    plans = [_plan_row(plan, today, names) for plan in MonthlyPlan.objects.filter(academic_year=academic_year)
             .exclude(status=MonthlyPlan.REFUSED).select_related('profile', 'requested_by', 'decided_by')]
    waivers = []
    for waiver in (WaiverRequest.objects.filter(status=WaiverRequest.APPROVED, pay_by__isnull=False)
                   .filter(Q(academic_year=academic_year) | Q(charge__academic_year=academic_year))
                   .select_related('charge__charge_type', 'profile', 'decided_by').order_by('pay_by')):
        outstanding = outstanding_on(waiver)
        waivers.append({
            'id': waiver.id, 'state': waiver_state(waiver, today, outstanding=outstanding),
            'profile_id': waiver.profile_id, 'name': waiver.profile.name, 'reg_no': waiver.profile.nactvet_reg_no,
            'college_id': waiver.profile.college_id or '', 'what': finance.waiver_label(waiver),
            'amount': str(finance.money(_shortfall(waiver))), 'outstanding': str(outstanding), 'pay_by': waiver.pay_by,
            'first_pay_by': waiver.first_pay_by, 'times_extended': waiver.times_extended, 'reason': waiver.reason,
            'days_left': (waiver.pay_by - today).days, 'decided_by': names(waiver.decided_by),
        })

    def total(rows, field, *states, key='state'):
        return str(sum((Decimal(row[field]) for row in rows if not states or row[key] in states), ZERO))
    running = (ON_TRACK, IN_GRACE)
    return {
        'academic_year': academic_year.name, 'plans': plans, 'waivers': waivers,
        'totals': {
            'plans_running': sum(1 for row in plans if row['state'] in running),
            'plans_in_grace': sum(1 for row in plans if row['state'] == IN_GRACE),
            'plans_lapsed': sum(1 for row in plans if row['state'] == MonthlyPlan.LAPSED),
            'plans_completed': sum(1 for row in plans if row['state'] == MonthlyPlan.COMPLETED),
            'plans_agreed': total(plans, 'total', *running, MonthlyPlan.COMPLETED, MonthlyPlan.LAPSED),
            'plans_paid': total(plans, 'paid', *running, MonthlyPlan.COMPLETED, MonthlyPlan.LAPSED),
            'plans_remaining': total(plans, 'remaining', *running),
            'lapsed_owed': total(plans, 'remaining', MonthlyPlan.LAPSED),
            'waivers_running': total(waivers, 'outstanding', 'running'),
            'waivers_expired': total(waivers, 'outstanding', 'expired'),
            'waivers_expired_count': sum(1 for row in waivers if row['state'] == 'expired'),
        },
    }


def portal_view(profile, academic_year, today=None):
    """What the student reads: their plan and the dates they gave."""
    today = today or date.today()
    if profile is None or academic_year is None:
        return None
    sync_plans(profile=profile, academic_year=academic_year, today=today)
    plan = MonthlyPlan.objects.filter(profile=profile, academic_year=academic_year,
                                      status=MonthlyPlan.ACTIVE).first()
    row = plan_row(plan, today) if plan else None
    waivers = []
    for waiver in WaiverRequest.objects.filter(profile=profile, status=WaiverRequest.APPROVED, pay_by__isnull=False,
                                               academic_year=academic_year).select_related('charge__charge_type'):
        outstanding = outstanding_on(waiver)
        if outstanding > ZERO:
            waivers.append({'what': finance.waiver_label(waiver), 'outstanding': outstanding, 'pay_by': waiver.pay_by,
                            'expired': waiver.pay_by < today})
    return {'plan': row, 'waivers': waivers} if row or waivers else None
