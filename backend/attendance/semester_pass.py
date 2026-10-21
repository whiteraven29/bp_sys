"""The student pass (said 20–21 October 2026).

At the start of a semester every student comes back through the desks with
their payment, and the admission office gives them a pass to classes and
exams — ID-card size, stamped by the office. The student may download it from
the portal to spare the queue; it is stamped all the same.

Who may have one: a student registered for the semester, still studying,
billed for the year, and paid up to the payment window they are in.

How long it runs follows what they paid, window by window. The year's bill
falls due in the payment schedule's windows (its instalment dates):

  * paid what the first window asks, not the second: until the day before
    the second window opens; paying again moves the date on;
  * first and second, not the third: until the day before the third; and so on;
  * the whole year in one payment, leaving nothing owed: a year from that day;
  * every window paid, in several payments: until the day before the first
    window comes round again a year later;
  * not paid up but let through by an override (Waivers & Overrides): the
    override's date.

Each pass is kept with a random code. It is checked by that code alone
(/semester-pass/check/): an edited copy — another name, a later date — does not
match what the college issued.
"""

import secrets
from datetime import date, timedelta

from django.db import transaction

from . import finance
from .models import (
    ChargeType, FinanceOverride, Payment, Semester, SemesterRegistration, StudentCharge,
    StudentPass, StudentProfile, StudentStanding,
)

STUDYING = {StudentStanding.ACTIVE, StudentStanding.REPEATING}
CODE_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'      # no 0/O, 1/I


def active_semester():
    return Semester.objects.filter(is_active=True).select_related('academic_year').first()


def _a_year_on(day):
    try:
        return day.replace(year=day.year + 1)
    except ValueError:                                  # 29 February
        return day.replace(year=day.year + 1, day=28)


def _years_bill(profile, academic_year):
    """The year's bill as the schedule laid it out: fees, direct costs, the
    hostel — not an exam declared or an item found missing."""
    return list(finance.with_balances(
        StudentCharge.objects.filter(profile=profile, academic_year=academic_year,
                                     source=StudentCharge.STRUCTURE)
        .exclude(charge_type_id__in=finance.item_charge_type_ids())
        .select_related('charge_type')))


def coverage(profile, academic_year, today=None):
    """Until when what they paid covers them, and why: (date or None, basis,
    the next window they owe and how much, or None)."""
    today = today or date.today()
    bill = [charge for charge in _years_bill(profile, academic_year) if charge.payable > finance.ZERO]
    # The student's own windows: the dates on their own bill, which the finance
    # desk raised from the schedule for their entry — new students' or
    # continuing students', never the two mixed (said 21 October 2026). A step
    # where they owe nothing new is no window of theirs.
    windows = sorted({charge.due_date for charge in bill})
    covered = 0
    for number, opens in enumerate(windows, start=1):
        if all(finance.charge_balance(c) <= finance.ZERO for c in bill if c.due_date <= opens):
            covered = number
        else:
            break
    owed = None
    if windows and covered == len(windows):
        payments = (Payment.objects.filter(
            allocations__charge__in=bill, amount__gt=finance.ZERO, reverses__isnull=True,
            reversal__isnull=True).distinct())
        if payments.count() == 1:
            paid_on = payments.first().payment_date
            until, basis = _a_year_on(paid_on), f'whole year paid at once on {paid_on:%d %b %Y}'
        else:
            until, basis = _a_year_on(windows[0]) - timedelta(days=1), 'every instalment of the year paid'
    elif covered:
        nxt = windows[covered]
        until, basis = nxt - timedelta(days=1), f'paid through instalment {covered} of {len(windows)}'
        owed = (nxt, sum((finance.charge_balance(c) for c in bill if c.due_date <= nxt), finance.ZERO))
    else:
        until, basis = None, 'the first instalment is not paid'
        if windows:
            owed = (windows[0], sum((finance.charge_balance(c) for c in bill if c.due_date <= windows[0]),
                                    finance.ZERO))
    # Let through by an override: its date is the pass's (said 21 October 2026).
    override = finance.active_override(profile, academic_year, ChargeType.REGISTRATION, today)
    if (override is not None and override.status == FinanceOverride.CLEARED and override.expires_on
            and (until is None or override.expires_on > until)):
        until, basis = override.expires_on, f'let through until {override.expires_on:%d %b %Y}: {override.reason}'
    return until, basis, owed


def status(profile, semester=None, today=None):
    """Whether this student may have a pass now, until when, and if not, why."""
    today = today or date.today()
    semester = semester or active_semester()
    if semester is None:
        return {'ok': False, 'reason': 'No semester is open.'}
    registration = (SemesterRegistration.objects.filter(profile=profile, semester=semester)
                    .exclude(status=SemesterRegistration.CANCELLED)
                    .select_related('programme', 'class_level').first())
    base = {'semester': str(semester), 'semester_id': semester.id, 'name': profile.name,
            'college_id': profile.college_id or '', 'reg_no': profile.nactvet_reg_no,
            'profile_id': profile.id}
    if registration is None:
        return {**base, 'ok': False,
                'reason': f'Not registered for {semester}: admitted students only.'}
    base.update(programme=registration.programme.name, programme_code=registration.programme.code,
                class_level=registration.class_level.name)
    standing = StudentStanding.objects.filter(profile=profile).first()
    if standing is not None and standing.status not in STUDYING:
        return {**base, 'ok': False, 'reason': f'Their status is {standing.get_status_display()}.'}
    if not _years_bill(profile, semester.academic_year):
        # Nothing owed is not the same as paid: the year's bill, raised at the
        # finance desk, is what makes them a registered student.
        return {**base, 'ok': False,
                'reason': f'No bill has been raised for {semester.academic_year} — see the finance desk.'}
    until, basis, owed = coverage(profile, semester.academic_year, today)
    base.update(valid_until=until, basis=basis)
    if until is None or until < today:
        reason = 'Not paid up.'
        if owed is not None:
            reason = (f'Not paid up: {finance.money(owed[1]):,.2f} is due for the instalment from '
                      f'{owed[0]:%d %b %Y}.')
        return {**base, 'ok': False, 'reason': reason,
                'balance': finance.money(owed[1]) if owed else None}
    return {**base, 'ok': True}


def _new_code():
    while True:
        raw = ''.join(secrets.choice(CODE_LETTERS) for _ in range(8))
        code = f'{raw[:4]}-{raw[4:]}'
        if not StudentPass.objects.filter(code=code).exists():
            return code


@transaction.atomic
def issue(profile, *, semester=None, actor=None, source=StudentPass.OFFICE, today=None):
    """The pass for what they have paid now: the one already issued for that
    date, or a new one. Returns (pass or None, status)."""
    result = status(profile, semester, today)
    if not result['ok']:
        return None, result
    semester = semester or active_semester()
    existing = StudentPass.objects.filter(profile=profile, semester=semester,
                                          valid_until=result['valid_until']).first()
    if existing is not None:
        return existing, result
    made = StudentPass.objects.create(
        profile=profile, semester=semester, code=_new_code(), valid_until=result['valid_until'],
        basis=result['basis'][:200], source=source,
        issued_by=actor if actor is not None and getattr(actor, 'is_authenticated', False) else None)
    return made, result


def normalise(code):
    raw = ''.join(ch for ch in str(code or '').upper() if ch.isalnum())
    return f'{raw[:4]}-{raw[4:]}' if len(raw) == 8 else str(code or '').strip().upper()


def verify(code, today=None):
    """A pass someone shows, by its code alone: whether the college issued it,
    and whether it still runs."""
    today = today or date.today()
    found = (StudentPass.objects.filter(code=normalise(code))
             .select_related('profile', 'semester__academic_year').first())
    if found is None:
        return {'genuine': False, 'reason': 'The college issued no pass with this code.'}
    standing = StudentStanding.objects.filter(profile=found.profile).first()
    stopped = standing is not None and standing.status not in STUDYING
    return {'genuine': True, 'pass': found, 'profile': found.profile,
            'valid': found.valid_until >= today and not stopped,
            'reason': (f'Their status is now {standing.get_status_display()}.' if stopped
                       else f'Expired on {found.valid_until:%d %b %Y}.' if found.valid_until < today else '')}


def find_profile(identifier):
    """A student by registration number or college ID."""
    from django.db.models import Q
    value = str(identifier or '').strip()
    if not value:
        return None
    return StudentProfile.objects.filter(Q(nactvet_reg_no__iexact=value) | Q(college_id__iexact=value)).first()
