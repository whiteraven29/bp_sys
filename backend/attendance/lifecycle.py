"""Stopping for a while, and finishing: postponement and completion clearance.

Postponement
    A student asks — from the portal, or on paper through the records office —
    and the Principal decides. Postponing semester 1 is the whole year: they
    return to semester 1 next year. Postponing semester 2 brings them back to
    semester 2 next year. A repeating student may postpone too; what they owe
    stays owed.

    Approval takes them off the semester(s) — registrations cancelled with a
    reason, enrollments dated as withdrawn, never deleted, marks untouched —
    records them as postponed with the semester they come back to, and resets
    the fees for the period: each charge is waived in full, so what they had
    paid stands as credit and settles the bill they get when they return.

Finishing
    A student who finishes level 6 is "completed" at the year end. Finance
    checks their account across every year they were here and prints the
    statement of everything they paid; with nothing owing, finance clears
    them. The Principal then declares them cleared: they are archived — the
    portal closes, the record stays.
"""

import logging

from django.db import transaction
from django.utils import timezone

from . import finance, notifications, progression
from .models import (
    AcademicYear, Application, CompletionClearance, Notification, OutstandingRepeat, Postponement, Semester,
    SemesterRegistration, StudentStanding,
)

logger = logging.getLogger(__name__)


class LifecycleError(ValueError):
    """Something the postponement or clearance rules do not allow."""


# ── postponement ──────────────────────────────────────────────────────────────

def current_registration(profile, semester):
    return (SemesterRegistration.objects.filter(profile=profile, semester=semester)
            .exclude(status=SemesterRegistration.CANCELLED)
            .select_related('programme', 'class_level').first())


def request_postponement(profile, semester, reason, *, by_student=False, entered_by=None):
    """Ask the Principal for leave to stop."""
    reason = (reason or '').strip()
    if not reason:
        raise LifecycleError('Say why the student needs to postpone.')
    standing = getattr(profile, 'standing', None)
    if standing is not None and standing.status in (StudentStanding.POSTPONED, StudentStanding.ARCHIVED,
                                                    StudentStanding.COMPLETED):
        raise LifecycleError(f'The student is {standing.get_status_display().lower()}; '
                             'there is nothing to postpone.')
    if current_registration(profile, semester) is None:
        raise LifecycleError(f'The student is not registered for {semester.label}.')
    if Postponement.objects.filter(profile=profile, status=Postponement.PENDING).exists():
        raise LifecycleError('A postponement request is already waiting for the Principal.')
    postponement = Postponement.objects.create(profile=profile, semester=semester, reason=reason[:2000],
                                               by_student=by_student, entered_by=entered_by)
    notifications.postponement_requested(postponement)
    return postponement


def _return_year(academic_year):
    """Next academic year, written down now so the student is expected there
    (the year end creates the same year by the same name)."""
    year, _ = AcademicYear.objects.get_or_create(name=academic_year.next_name,
                                                 defaults={'is_active': False})
    return year


@transaction.atomic
def decide_postponement(postponement, *, approve, actor=None, note=''):
    if postponement.status != Postponement.PENDING:
        raise LifecycleError(f'This request is already {postponement.get_status_display().lower()}.')
    postponement.decided_by = actor
    postponement.decided_at = timezone.now()
    postponement.decision_note = (note or '')[:300]
    if not approve:
        if not postponement.decision_note:
            raise LifecycleError('Say why it is declined — the student reads it.')
        postponement.status = Postponement.DECLINED
        postponement.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
        notifications.postponement_decided(postponement)
        return postponement
    _postpone(postponement, actor=actor)
    postponement.status = Postponement.APPROVED
    postponement.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note',
                                     'return_year', 'return_semester_number', 'fees_reversed'])
    notifications.postponement_decided(postponement)
    return postponement


def _postpone(postponement, *, actor=None):
    profile = postponement.profile
    semester = postponement.semester
    year = semester.academic_year
    registration = current_registration(profile, semester)
    if registration is None:
        raise LifecycleError(f'The student is no longer registered for {semester.label}.')
    # Semester 1 is the whole year; semester 2 is semester 2.
    stopping = list(Semester.objects.filter(academic_year=year, number__gte=semester.number))
    reason = f'Postponed with the Principal\'s leave ({semester.label}).'

    for each in stopping:
        SemesterRegistration.objects.filter(profile=profile, semester=each).exclude(
            status=SemesterRegistration.CANCELLED).update(
            status=SemesterRegistration.CANCELLED, cancelled_reason=reason[:300])
        progression.withdraw_enrollments(profile, each, reason=reason)
        Application.objects.filter(profile=profile, semester=each,
                                   state__in=sorted(Application.OPEN_STATES)).update(
            state=Application.CANCELLED, decided_reason=reason[:300])

    back_to = _return_year(year)
    repeating = profile.outstanding_repeats.filter(status=OutstandingRepeat.OPEN).exists()
    progression.set_standing(
        profile, StudentStanding.POSTPONED, actor=actor, semester=semester,
        class_level=registration.class_level, programme=registration.programme,
        return_year=back_to, return_semester_number=semester.number,
        reason=reason + (' Repeat modules still owed.' if repeating else ''))

    postponement.return_year = back_to
    postponement.return_semester_number = semester.number
    postponement.fees_reversed = finance.reset_fees(
        profile, year, from_semester_number=semester.number, actor=actor,
        reason=f'postponed from {semester.label}')
    logger.info('%s postponed from %s; back %s semester %s; %s reversed',
                profile.nactvet_reg_no, semester, back_to, semester.number, postponement.fees_reversed)


def withdraw_request(postponement):
    if postponement.status != Postponement.PENDING:
        raise LifecycleError('Only a waiting request can be withdrawn.')
    postponement.status = Postponement.WITHDRAWN
    postponement.save(update_fields=['status'])
    return postponement


# ── finishing ─────────────────────────────────────────────────────────────────

def finishing_students():
    """Students who have finished and are not yet archived, and those archived
    — with where their clearance stands."""
    standings = (StudentStanding.objects
                 .filter(status__in=[StudentStanding.COMPLETED, StudentStanding.ARCHIVED])
                 .select_related('profile', 'class_level', 'programme')
                 .order_by('status', 'profile__name'))
    clearances = {clearance.profile_id: clearance for clearance in
                  CompletionClearance.objects.filter(profile_id__in=[s.profile_id for s in standings])
                  .select_related('finance_cleared_by', 'declared_by')}
    rows = []
    for standing in standings:
        clearance = clearances.get(standing.profile_id)
        rows.append({'standing': standing, 'clearance': clearance,
                     'balance': finance.balance_for(standing.profile)['balance']})
    return rows


def _finished(profile):
    standing = getattr(profile, 'standing', None)
    if standing is None or standing.status != StudentStanding.COMPLETED:
        raise LifecycleError('Only a student who has finished the programme is cleared.')
    return standing


@transaction.atomic
def finance_clear(profile, *, actor=None, note=''):
    """Finance: nothing is owed across every year, and the statement has been
    printed and signed."""
    _finished(profile)
    balance = finance.balance_for(profile)['balance']
    if balance > 0:
        raise LifecycleError(f'{balance:,.2f} is still owed. It must be paid, or waived with a reason, '
                             'before finance can clear them.')
    clearance, _ = CompletionClearance.objects.get_or_create(profile=profile)
    clearance.finance_cleared_by = actor
    clearance.finance_cleared_at = timezone.now()
    clearance.balance_at_clearance = balance
    clearance.finance_note = (note or '')[:300]
    clearance.save()
    finance.audit('completion.finance_clear', 'StudentProfile', actor=actor, profile=profile,
                  summary=f'Finance cleared on completion; balance {balance}')
    return clearance


@transaction.atomic
def declare_cleared(profile, *, actor=None, note=''):
    """The Principal: the student is cleared and leaves the system — archived,
    portal closed, every record kept."""
    standing = _finished(profile)
    clearance = CompletionClearance.objects.filter(profile=profile).first()
    if clearance is None or clearance.finance_cleared_at is None:
        raise LifecycleError('Finance has not cleared this student yet.')
    balance = finance.balance_for(profile)['balance']
    if balance > 0:
        raise LifecycleError(f'{balance:,.2f} has been billed since finance cleared them; '
                             'finance must clear them again.')
    clearance.declared_by = actor
    clearance.declared_at = timezone.now()
    clearance.note = (note or '')[:300]
    clearance.save(update_fields=['declared_by', 'declared_at', 'note'])
    progression.set_standing(profile, StudentStanding.ARCHIVED, actor=actor,
                             class_level=standing.class_level, programme=standing.programme,
                             reason='Declared cleared by the Principal on completing the programme.')
    return clearance


# ── what the student sees ─────────────────────────────────────────────────────

def standing_banner(profile):
    """One line for the top of the student portal saying where they stand, or
    None for a student simply studying."""
    standing = getattr(profile, 'standing', None)
    if standing is None:
        return None
    standing = lift_suspension_if_over(standing)
    owed = list(profile.outstanding_repeats.filter(status=OutstandingRepeat.OPEN).values_list('module_code', flat=True))
    back = ''
    if standing.return_semester_number:
        back = (f'{standing.class_level.name + ", " if standing.class_level_id else ""}'
                f'semester {standing.return_semester_number}'
                f'{" of " + standing.return_year.name if standing.return_year_id else ""}')
    status = standing.status
    if status == StudentStanding.ACTIVE and progression.is_provisional(profile):
        review = (profile.semester_reviews.filter(confirmed='provisional').select_related('class_level')
                  .order_by('-semester__academic_year__name', '-semester__number').first())
        before = review.class_level.name if review and review.class_level_id else 'the level before'
        here = standing.class_level.name if standing.class_level_id else 'this level'
        return {'tone': 'amber', 'status': 'provisional',
                'text': f'Provisional — your place in {here} depends on your {before} results.'}
    if status == StudentStanding.REPEATING or (status == StudentStanding.ACTIVE and owed):
        return {'tone': 'amber', 'status': status,
                'text': 'Repeating ' + ', '.join(sorted(owed)) if owed else 'Repeating'}
    if status == StudentStanding.POSTPONED:
        return {'tone': 'blue', 'status': status, 'text': 'Postponed' + (f' — due back: {back}' if back else '')}
    if status == StudentStanding.FAILED:
        return {'tone': 'red', 'status': status,
                'text': 'FAIL' + (f' — readmission to {back}' if back else ' — you may be readmitted')}
    if status == StudentStanding.DISCONTINUED:
        return {'tone': 'red', 'status': status,
                'text': 'Discontinued by the Principal — you may apply again as a new student'}
    if status == StudentStanding.SUSPENDED:
        return {'tone': 'red', 'status': status,
                'text': 'Suspended' + (f' until {standing.until:%d %B %Y}' if standing.until else '')
                        + ' — no classes or examinations until then'}
    if status == StudentStanding.ABSCONDED:
        return {'tone': 'red', 'status': status,
                'text': f'Absconded — you may return as a new student from {may_return_from(standing):%d %B %Y}'}
    if status == StudentStanding.COMPLETED:
        clearance = CompletionClearance.objects.filter(profile=profile).first()
        waiting = 'the Principal\'s declaration' if clearance and clearance.finance_cleared_at \
            else 'finance clearance'
        what = (f'Finished after {standing.class_level.name}' if standing.class_level_id
                and standing.class_level.order in EXIT_LEVELS else 'Finished the programme')
        return {'tone': 'green', 'status': status, 'text': f'{what} — awaiting {waiting}'}
    if status == StudentStanding.NOT_REPORTED:
        return {'tone': 'blue', 'status': status,
                'text': 'You did not report' + (f' — you may return: {back}' if back else '')}
    return None


# ── statuses recorded by hand (said 19 October 2026) ──────────────────────────
#
# Deceased and absconded (ABS) are recorded by records or admission;
# suspension, termination and a disciplinary discontinuation are the
# Principal's decision, and DPARC is told. Each takes a reason and a date.

#: How long an absconded or postponed student may stay away; after it a
#: postponed student, and from it an absconded one, comes back as new.
AWAY_YEARS = 2


def years_after(day, years):
    try:
        return day.replace(year=day.year + years)
    except ValueError:                          # 29 February
        return day.replace(year=day.year + years, day=28)


def may_return_from(standing):
    """The day an absconded student may come back."""
    return years_after(standing.since or timezone.localdate(), AWAY_YEARS)


def lift_suspension_if_over(standing):
    """A suspension ends by itself on its end date: the student studies again."""
    if (standing.status == StudentStanding.SUSPENDED and standing.until
            and standing.until < timezone.localdate()):
        return progression.set_standing(
            standing.profile, StudentStanding.ACTIVE, reason=f'Suspension ended {standing.until}.')
    return standing


def is_suspended(profile):
    standing = StudentStanding.objects.filter(profile=profile).first()   # never a stale copy
    if standing is None or standing.status != StudentStanding.SUSPENDED:
        return False
    return lift_suspension_if_over(standing).status == StudentStanding.SUSPENDED


@transaction.atomic
def record_status(profile, status, *, actor=None, reason='', on_date=None, until=None):
    """Write down that a student has died, absconded, been suspended,
    terminated or discontinued.

    Deceased, absconded, terminated and discontinued take the student off the
    class from now — the current and later registrations cancelled, their
    modules withdrawn (marks kept), open applications cancelled. Nothing is
    deleted and no fee is reversed: the accountant decides. A suspension keeps
    the place and the fees; the student cannot sit an examination until it
    ends, and it ends by itself.
    """
    from .models import Semester
    if status not in StudentStanding.RECORDED_BY_HAND:
        raise LifecycleError('Deceased, absconded, suspended, terminated or discontinued.')
    reason = (reason or '').strip()
    if not reason:
        raise LifecycleError('Say why — it stays on the student\'s record.')
    on_date = on_date or timezone.localdate()
    if on_date > timezone.localdate():
        raise LifecycleError('The date cannot be in the future.')
    if status == StudentStanding.SUSPENDED and (until is None or until <= on_date):
        raise LifecycleError('A suspension needs the date it ends, after it starts.')
    standing = progression.standing_for(profile)
    if standing.status in StudentStanding.NEVER_ADMITTED:
        raise LifecycleError(f'{profile.name} is already recorded as {standing.get_status_display().lower()}.')

    label = dict(StudentStanding.STATUS_CHOICES)[status].split(' —')[0]
    text = f'{label} ({on_date}): {reason}'
    if status != StudentStanding.SUSPENDED:
        take_off_from_now(profile, text)
    standing = progression.set_standing(profile, status, actor=actor, reason=text, note=text,
                                        since=on_date, until=until)
    if status in StudentStanding.PRINCIPAL_DECIDES or status == StudentStanding.DECEASED:
        notifications.notify(notifications.principal_and_dparc(), profile=None,
                             kind=Notification.STATUS_RECORDED,
                             title=f'{profile.name}: {label.lower()}',
                             body=text[:300], link='status:standing')
    logger.info('%s recorded as %s by %s', profile.nactvet_reg_no, status, getattr(actor, 'username', None))
    return standing


def take_off_from_now(profile, text):
    """Off the class from the active semester on: the current and later
    registrations cancelled, their modules withdrawn (marks kept), open
    applications cancelled."""
    active = Semester.objects.filter(is_active=True).select_related('academic_year').first()
    later = SemesterRegistration.objects.filter(profile=profile).exclude(
        status=SemesterRegistration.CANCELLED).select_related('semester__academic_year')
    for registration in later:
        sem = registration.semester
        if active is None or (sem.academic_year.name, sem.number) >= (active.academic_year.name, active.number):
            registration.status = SemesterRegistration.CANCELLED
            registration.cancelled_reason = text[:300]
            registration.save(update_fields=['status', 'cancelled_reason'])
            progression.withdraw_enrollments(profile, sem, reason=text)
    Application.objects.filter(profile=profile, state__in=sorted(Application.OPEN_STATES)).update(
        state=Application.CANCELLED, decided_reason=text[:300])
    return active


def hand_recorded_statuses():
    return (StudentStanding.objects.filter(status__in=sorted(StudentStanding.RECORDED_BY_HAND))
            .select_related('profile', 'class_level', 'programme').order_by('-since', 'profile__name'))


# ── transfer to another college ──────────────────────────────────────────────
#
# Said 27 October 2026: a student — passed, often — who goes to another
# college. The admission office finds them by NACTVET number, sees their
# details and last results, and approves the transfer: the portal closes,
# their places from now on are called off, and the fees from this semester on
# are reversed to nothing, so no year total counts a student who has gone.
# What they paid stays on their account as credit (a refund is the
# accountant's). Their details and last results are kept on the Transfer for
# reference. Should they ever come back, it is as a new student.
#
# Nobody leaves owing (said the same day): the period before this one must be
# paid — semester 1 when the transfer is in semester 2, last academic year
# when it is in semester 1. Where the system holds no bill for that period
# (last year's fees were never entered), finance's word that nothing is owed
# is written down instead, with who gave it.

#: Not transferred: already gone, or finished.
NOT_TRANSFERABLE = StudentStanding.NEVER_ADMITTED | {StudentStanding.TRANSFERRED, StudentStanding.COMPLETED,
                                                     StudentStanding.ARCHIVED}


def student_details(profile):
    """Their record as admission sees it, kept with a transfer."""
    return {
        'name': profile.name, 'reg_no': profile.nactvet_reg_no, 'college_id': profile.college_id or '',
        'phone': profile.phone or '', 'gender': profile.get_gender_display() if profile.gender else '',
        'date_of_birth': profile.date_of_birth.isoformat() if profile.date_of_birth else '',
        'form_four_index': profile.form_four_index or '',
        'next_of_kin': [{'name': kin.name, 'phone': kin.phone, 'relationship': kin.get_relationship_display()}
                        for kin in profile.next_of_kin.order_by('position')],
    }


def last_results(profile):
    """Their last semester's results, as the semester review holds them."""
    from .models import SemesterReview
    review = (SemesterReview.objects.filter(profile=profile)
              .select_related('semester__academic_year', 'class_level', 'programme')
              .order_by('-semester__academic_year__name', '-semester__number').first())
    if review is None:
        return {}
    decided = review.get_confirmed_display() if review.confirmed else ''
    return {
        'semester': review.semester.label,
        'class_level': review.class_level.name if review.class_level_id else '',
        'programme': review.programme.code if review.programme_id else '',
        'gpa': str(review.gpa) if review.gpa is not None else '',
        'results_say': review.get_proposed_display() if review.proposed else '',
        'decision': decided,
        'modules': [{'code': row.get('code', ''), 'name': row.get('name', ''), 'grade': row.get('grade') or '',
                     'status': row.get('status', '')} for row in (review.modules or [])],
    }


def earlier_debt(profile, active=None):
    """What the student still owes for the period before this one:
    {'period', 'owed', 'in_system'} — `in_system` False when the system holds
    no bill for that period at all, so it cannot say."""
    from .models import StudentCharge
    active = active or Semester.objects.filter(is_active=True).select_related('academic_year').first()
    if active is None:
        return {'period': '', 'owed': finance.ZERO, 'in_system': False}
    if active.number == Semester.SEM2:
        year = active.academic_year
        sem2 = finance.semester_two_dates(year)
        charges = [charge for charge in finance.with_balances(
                       StudentCharge.objects.filter(profile=profile, academic_year=year).select_related('semester'))
                   if finance.charge_semester_number(charge, sem2) == 1]
        period = f'Semester 1 · {year.name}'
    else:
        year = AcademicYear.objects.filter(name__lt=active.academic_year.name).order_by('-name').first()
        charges = list(finance.with_balances(StudentCharge.objects.filter(profile=profile, academic_year=year))
                       ) if year else []
        period = year.name if year else 'last year'
    owed = sum((max(finance.charge_balance(charge), finance.ZERO) for charge in charges), finance.ZERO)
    return {'period': period, 'owed': finance.money(owed), 'in_system': bool(charges)}


def _where_they_were(profile, standing):
    """Programme and level: the standing's, else their last review's."""
    from .models import SemesterReview
    if standing.programme_id and standing.class_level_id:
        return standing.programme, standing.class_level
    review = (SemesterReview.objects.filter(profile=profile).select_related('programme', 'class_level')
              .order_by('-semester__academic_year__name', '-semester__number').first())
    return (standing.programme or (review.programme if review else None),
            standing.class_level or (review.class_level if review else None))


def transfer_preview(profile):
    """What the admission office sees before approving."""
    standing = progression.standing_for(profile)
    active = Semester.objects.filter(is_active=True).select_related('academic_year').first()
    bill = finance.balance_for(profile, active.academic_year) if active else None
    debt = earlier_debt(profile, active)
    why_not = (f'{profile.name} is {standing.get_status_display().lower()}: not transferred.'
               if standing.status in NOT_TRANSFERABLE else
               f'Owes {debt["owed"]:,} for {debt["period"]}: settle it at finance first.' if debt['owed'] > 0 else '')
    programme, level = _where_they_were(profile, standing)
    return {
        'profile_id': profile.id, 'details': student_details(profile),
        'standing': standing.get_status_display(),
        'programme': programme.code if programme else '',
        'class_level': level.name if level else '',
        'last_results': last_results(profile),
        'year': active.academic_year.name if active else '',
        'bill': {key: str(value) for key, value in bill.items()} if bill else None,
        'debt': {'period': debt['period'], 'owed': str(debt['owed']), 'in_system': debt['in_system']},
        'can_transfer': not why_not, 'why_not': why_not,
    }


@transaction.atomic
def transfer_student(profile, *, to_college, reason='', reference='', on_date=None, actor=None,
                     finance_confirmed=''):
    """Approve a transfer to another college. Returns the Transfer.

    `finance_confirmed`: finance's word that nothing is owed for the period
    before this one (who, and how) — needed only when the system holds no bill
    for that period."""
    from django.db.models import Sum
    from .models import PaymentAllocation, StudentCharge, Transfer
    to_college = (to_college or '').strip()
    if not to_college:
        raise LifecycleError('Which college are they going to?')
    on_date = on_date or timezone.localdate()
    if on_date > timezone.localdate():
        raise LifecycleError('The date cannot be in the future.')
    standing = progression.standing_for(profile)
    if standing.status in NOT_TRANSFERABLE:
        raise LifecycleError(f'{profile.name} is {standing.get_status_display().lower()}: not transferred.')
    debt = earlier_debt(profile)
    if debt['owed'] > 0:
        raise LifecycleError(f'{profile.name} owes {debt["owed"]:,} for {debt["period"]}: settle it at finance first.')
    finance_confirmed = (finance_confirmed or '').strip()
    if debt['in_system']:
        debt_check = f'Nothing owed for {debt["period"]} (the system\'s bill).'
    elif finance_confirmed:
        debt_check = f'{debt["period"]} not in the system — finance confirmed nothing owed: {finance_confirmed}'
    else:
        raise LifecycleError(f'The system holds no bill for {debt["period"]}: write down finance\'s confirmation '
                             'that nothing is owed (who, and the reference).')

    text = f'Transferred to {to_college} ({on_date}){": " + reason.strip() if reason.strip() else ""}'[:300]
    details, results = student_details(profile), last_results(profile)
    programme, level = _where_they_were(profile, standing)
    active = take_off_from_now(profile, text)

    # The year's fees, and any later year's, reversed to nothing — one-time
    # charges too: the year's total must not count a student who has gone.
    reversed_total = paid = finance.ZERO
    if active is not None:
        years = AcademicYear.objects.filter(name__gte=active.academic_year.name)
        charges = StudentCharge.objects.filter(profile=profile, academic_year__in=years)
        sem2 = finance.semester_two_dates(active.academic_year)
        paid = sum((allocation.amount for allocation in PaymentAllocation.objects.filter(charge__in=charges)
                    .select_related('charge__semester')
                    if not (allocation.charge.academic_year_id == active.academic_year_id
                            and active.number == Semester.SEM2
                            and finance.charge_semester_number(allocation.charge, sem2) == 1)), finance.ZERO)
        for year in years.filter(id__in=charges.values('academic_year_id')):
            this_year = year.id == active.academic_year_id
            reversed_total += finance.reset_fees(
                profile, year, reason=text, actor=actor,
                from_semester_number=active.number if this_year else 1,
                one_time=not (this_year and active.number == Semester.SEM2))
    credit = finance.money(paid + finance.on_account_total(profile))

    progression.set_standing(profile, StudentStanding.TRANSFERRED, actor=actor, reason=text, note=text,
                             since=on_date, class_level=level, programme=programme)
    transfer = Transfer.objects.create(
        profile=profile, to_college=to_college[:200], reason=(reason or '').strip()[:500],
        reference=(reference or '').strip()[:200], transferred_on=on_date, programme=programme,
        class_level=level, details=details, last_results=results, fees_reversed=reversed_total,
        credit=credit, debt_check=debt_check[:300], recorded_by=actor)
    body = text + (f'. Their money held: {credit} (credit; a refund is the accountant\'s).' if credit else '.')
    told = {user.id: user for user in notifications.principal_and_dparc() + notifications.accountants()}
    notifications.notify(list(told.values()), profile=None,
                         kind=Notification.TRANSFERRED, title=f'{profile.name}: transferred',
                         body=body[:300], link='adm:transferred')
    logger.info('%s transferred to %s by %s', profile.nactvet_reg_no, to_college, getattr(actor, 'username', None))
    return transfer


# ── finishing after Level 5 ───────────────────────────────────────────────────
#
# A student may finish after Level 5 with that level's award (said 18 October
# 2026). They ask from the portal; only the Principal approves. Approved, the
# next level is called off — its application or registration cancelled and its
# fees reversed; nothing is refunded — and they finish through the same route
# as Level 6. The award is NACTVET's. They may come back for Level 6 in any
# later year, through admission as a continuing student.

#: The NTA levels a student may finish after, short of the programme's last.
EXIT_LEVELS = {5}


def exit_review(profile):
    """The review that lets this student finish early, or None and why not."""
    from .models import ExitRequest, SemesterReview
    standing = getattr(profile, 'standing', None)
    if standing is not None and standing.status not in (StudentStanding.ACTIVE,):
        return None, f'You are {standing.get_status_display().lower()}.'
    if ExitRequest.objects.filter(profile=profile, status=ExitRequest.PENDING).exists():
        return None, 'Your request is already waiting for the Principal.'
    review = (SemesterReview.objects.filter(profile=profile, semester__number=2,
                                            class_level__order__in=sorted(EXIT_LEVELS))
              .exclude(confirmed='').select_related('semester__academic_year', 'class_level', 'programme')
              .order_by('-semester__academic_year__name').first())
    if review is None:
        return None, 'Only a student who has finished Level 5 may ask.'
    if review.confirmed == SemesterReview.PROVISIONAL:
        return None, 'Your Level 5 results are not out yet: ask once they are.'
    if review.confirmed != SemesterReview.CLEAR:
        return None, 'Level 5 is not passed yet.'
    if profile.outstanding_repeats.filter(status=OutstandingRepeat.OPEN).exists():
        return None, 'You still owe a module.'
    higher = SemesterRegistration.objects.filter(
        profile=profile, class_level__order__gt=review.class_level.order).exclude(
        status=SemesterRegistration.CANCELLED).filter(
        semester__academic_year__name__gt=review.semester.academic_year.name)
    if higher.exists():
        return None, 'You are already registered at the next level: ask to postpone instead.'
    return review, ''


@transaction.atomic
def request_exit(profile, *, reason=''):
    from .models import ExitRequest
    review, why_not = exit_review(profile)
    if review is None:
        raise LifecycleError(why_not)
    request = ExitRequest.objects.create(profile=profile, class_level=review.class_level, review=review,
                                         reason=(reason or '').strip()[:500])
    notifications.notify(notifications._users_holding(notifications.PrincipalProfile),
                         kind=Notification.EXIT_REQUESTED,
        title=f'Finishing after {review.class_level.name}: {profile.name}',
        body=f'{profile.nactvet_reg_no} asks to finish with the {review.class_level.name} award. '
             f'Waiting on the Principal.', link='status:exits')
    notifications.notify(notifications.deans(), kind=Notification.EXIT_REQUESTED,
                         title=f'Finishing after {review.class_level.name}: {profile.name}',
                         body='For your follow-up; the Principal decides.', link='status:exits')
    return request


@transaction.atomic
def withdraw_exit(request):
    from .models import ExitRequest
    request = ExitRequest.objects.select_for_update().get(pk=request.pk)
    if request.status != ExitRequest.PENDING:
        raise LifecycleError('Only a request still waiting can be withdrawn.')
    request.status = ExitRequest.WITHDRAWN
    request.save(update_fields=['status'])
    return request


@transaction.atomic
def decide_exit(request, *, approve, actor, note=''):
    """The Principal's decision. Approved: the next level called off, the
    student finished at this level and on the finishing route."""
    from .models import ExitRequest
    from .roles import is_break_glass, is_principal
    if not (is_principal(actor) or is_break_glass(actor)):
        raise LifecycleError('Only the Principal approves finishing early.')
    request = ExitRequest.objects.select_for_update().select_related(
        'profile', 'class_level', 'review__semester__academic_year', 'review__programme').get(pk=request.pk)
    if request.status != ExitRequest.PENDING:
        raise LifecycleError(f'This request is already {request.get_status_display().lower()}.')
    note = (note or '').strip()
    if not approve and not note:
        raise LifecycleError('Say why it is declined.')
    if approve:
        review, why_not = exit_review_ignoring_request(request)
        if review is None:
            raise LifecycleError(why_not)
    request.status = ExitRequest.APPROVED if approve else ExitRequest.DECLINED
    request.decided_by = actor
    request.decided_at = timezone.now()
    request.decision_note = note[:300]
    request.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    profile, level = request.profile, request.class_level
    if approve:
        reason = f'Finished after {level.name} — approved by the Principal.'
        progression.call_off_later(profile, request.review.semester, reason=reason, actor=actor,
                                   fee_reason=f'Finished after {level.name}; nothing refunded')
        progression.set_standing(profile, StudentStanding.COMPLETED, actor=actor,
                                 semester=request.review.semester, class_level=level,
                                 programme=request.review.programme, reason=reason)
        notifications.notify(notifications.accountants(), kind=Notification.EXIT_DECIDED,
                             title=f'Finishing clearance: {profile.name}',
                             body=f'{profile.nactvet_reg_no} finished after {level.name}. Check the account '
                                  f'for clearance (Postpone & Finish ▸ Finishing).', link='status:finish')
    notifications.notify(notifications.deans(), kind=Notification.EXIT_DECIDED,
                         title=f'Finishing after {level.name} {"approved" if approve else "declined"}: {profile.name}',
                         body=note, link='status:exits')
    notifications.notify(profile=profile, kind=Notification.EXIT_DECIDED,
                         title=f'Your request to finish after {level.name} was {"approved" if approve else "declined"}',
                         body=note or ('Finance will check your account for clearance. Your award comes from NACTVET.'
                                       if approve else ''), link='services')
    return request


def exit_review_ignoring_request(request):
    """`exit_review`, for the request being decided (which is itself waiting)."""
    from .models import ExitRequest
    request.status = ExitRequest.WITHDRAWN
    try:
        ExitRequest.objects.filter(pk=request.pk).update(status=ExitRequest.WITHDRAWN)
        return exit_review(request.profile)
    finally:
        ExitRequest.objects.filter(pk=request.pk).update(status=ExitRequest.PENDING)
        request.status = ExitRequest.PENDING
