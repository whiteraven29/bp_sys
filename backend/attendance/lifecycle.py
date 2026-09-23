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

from . import finance, progression
from .models import (
    AcademicYear, Application, CompletionClearance, OutstandingRepeat, Postponement, Semester,
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
    return Postponement.objects.create(profile=profile, semester=semester, reason=reason[:2000],
                                       by_student=by_student, entered_by=entered_by)


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
        return postponement
    _postpone(postponement, actor=actor)
    postponement.status = Postponement.APPROVED
    postponement.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note',
                                     'return_year', 'return_semester_number', 'fees_reversed'])
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
        return {'tone': 'green', 'status': status, 'text': f'Finished the programme — awaiting {waiting}'}
    return None
