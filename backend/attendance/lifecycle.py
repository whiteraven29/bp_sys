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
    if status == StudentStanding.DISCONTINUED:
        return {'tone': 'red', 'status': status,
                'text': 'Discontinued' + (f' — readmission to {back}' if back else '')}
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
