"""Admitting a student: three desks, in order, and what happens at the end.

The college admits three kinds of student, and each goes a different way:

    first year    intake, where the admission office takes them on and their
                  NACTVET number is obtained; then records, who keep their
                  details and their documents and handle no money at all; then
                  finance; then back to the admission office, which admits
                  them. They begin and end at the same desk.
    continuing    finance clears their account, records checks their results,
                  admission finalises
    readmission   finance clears their account, records confirms the semester
                  they are coming back to, admission finalises

Nothing registers or enrolls anybody until the last step. An application is a
queue position with a trail attached — who cleared which desk, when, and what
they said — and `admit` is the single moment it turns into a registration, an
enrollment, a bill and a college ID number.

The rules that live here rather than in a screen:

  * A student is registered once per semester, so an application is too.
  * Only finance bills. Charges are raised when the application reaches that
    desk, because there has to be something to pay before anybody can pay it,
    and no other desk raises a shilling.
  * The college ID is issued once and kept for life: a readmitted student keeps
    the number they were given the first time.
  * A requirement the student does not have is charged at the accountant's rate
    for it, and is checked again next semester.
  * An admitted student leaves with a portal password. It is shown to the
    officer once, because only its hash is kept, and the student is made to
    change it the first time they sign in.
"""

import logging
import secrets

from django.db import transaction
from django.utils import timezone

from . import finance, progression
from .models import (
    AdmissionRequirement, AdmissionWindow, Application, ApplicationStep,
    CollegeIdFormat, NextOfKin, RequirementCheck, SemesterRegistration,
    Student, StudentProfile, StudentStanding,
)

logger = logging.getLogger(__name__)


class AdmissionError(ValueError):
    """Something the admission rules do not allow."""


#: How an application's kind maps onto the footing a registration is made on.
REGISTRATION_KIND = {
    Application.NEW: SemesterRegistration.NEW,
    Application.CONTINUING: SemesterRegistration.CONTINUING,
    Application.READMISSION: SemesterRegistration.READMISSION,
}


# ── the window ────────────────────────────────────────────────────────────────

def window_for(semester):
    return AdmissionWindow.objects.filter(semester=semester).first()


def admissions_are_open(semester, *, today=None):
    window = window_for(semester)
    return bool(window and window.is_open(today))


# ── the college's own number ──────────────────────────────────────────────────

def id_format_for(academic_year):
    """The format for this year, carried from last year's if the office has not
    set one up yet — the shape of the number rarely changes, the counter always
    does."""
    existing = CollegeIdFormat.objects.filter(academic_year=academic_year).first()
    if existing is not None:
        return existing
    previous = (CollegeIdFormat.objects
                .filter(academic_year__name__lt=academic_year.name)
                .order_by('-academic_year__name').first())
    return CollegeIdFormat.objects.create(
        academic_year=academic_year,
        pattern=previous.pattern if previous else '{COLLEGE}/{PROG}/{YEAR}/{SEQ}',
        college_code=previous.college_code if previous else 'BPH',
        sequence_width=previous.sequence_width if previous else 3,
        starts_at=1, next_number=1,
    )


@transaction.atomic
def issue_college_id(profile, academic_year, programme):
    """Give this student the college's own number, once.

    A student who already has one keeps it — a readmitted student is the same
    person coming back, not a new one — so this returns what they have rather
    than spending another number on them.
    """
    if profile.college_id:
        return profile.college_id

    fmt = CollegeIdFormat.objects.select_for_update().get(
        pk=id_format_for(academic_year).pk)
    number = max(fmt.next_number, fmt.starts_at)
    for _ in range(1000):
        candidate = fmt.render(number, programme)
        if not StudentProfile.objects.filter(college_id=candidate).exists():
            break
        number += 1
    else:
        raise AdmissionError('Could not find an unused college ID number; check the format.')

    profile.college_id = candidate
    profile.save(update_fields=['college_id'])
    fmt.next_number = number + 1
    fmt.save(update_fields=['next_number', 'updated_at'])
    logger.info('Issued college ID %s to %s', candidate, profile.nactvet_reg_no)
    return candidate


# ── opening an application ────────────────────────────────────────────────────

TEMPORARY_PREFIX = 'TEMP/'


def temporary_number():
    """A number of the college's own, for a student the authority has not
    numbered yet. Replaced at intake by the real one."""
    from datetime import date as _date
    year = _date.today().year
    taken = StudentProfile.objects.filter(
        nactvet_reg_no__startswith=f'{TEMPORARY_PREFIX}{year}/').count()
    for offset in range(1, 10000):
        candidate = f'{TEMPORARY_PREFIX}{year}/{taken + offset:04d}'
        if not StudentProfile.objects.filter(nactvet_reg_no=candidate).exists():
            return candidate
    raise AdmissionError('Could not make a temporary number.')


def is_temporary(reg_no):
    return str(reg_no or '').upper().startswith(TEMPORARY_PREFIX)


@transaction.atomic
def set_nactvet_number(profile, number, *, actor=None):
    """Write in the number the authority has issued.

    The number is the key the college files a student under, and their
    enrollments carry a copy of it, so both move together. A number already
    belonging to somebody else is refused rather than merged.
    """
    number = (number or '').strip().upper()
    if not number:
        raise AdmissionError('No number given.')
    if number == profile.nactvet_reg_no:
        return profile
    if StudentProfile.objects.filter(nactvet_reg_no=number).exclude(pk=profile.pk).exists():
        raise AdmissionError(f'{number} already belongs to another student.')

    was = profile.nactvet_reg_no
    profile.nactvet_reg_no = number
    profile.save(update_fields=['nactvet_reg_no'])
    moved = Student.objects.filter(nactvet_reg_no__iexact=was).update(nactvet_reg_no=number)
    logger.info('NACTVET number for %s set to %s (%d enrollment(s) moved)', was, number, moved)
    return profile


@transaction.atomic
def capture_student(*, nactvet_reg_no, name, phone='', gender='', date_of_birth=None,
                    next_of_kin=(), actor=None):
    """Write down a person the college has not met before.

    A person on file is not yet a student: they have no registration, no
    modules and no place in a class until they are admitted. Their NACTVET
    number may not exist yet — the authority issues it during the admission —
    so a placeholder is accepted and corrected later.
    """
    reg_no = (nactvet_reg_no or '').strip().upper()
    if not (name or '').strip():
        raise AdmissionError('A name is needed.')
    if not reg_no:
        # The authority issues the NACTVET number during the admission, so a
        # first year often has none when they walk in. The college holds them
        # under a number of its own until the real one arrives at intake.
        reg_no = temporary_number()

    profile, created = StudentProfile.objects.get_or_create(
        nactvet_reg_no=reg_no,
        defaults={'name': name.strip(), 'phone': phone, 'gender': gender,
                  'date_of_birth': date_of_birth},
    )
    if not created:
        profile.name = name.strip() or profile.name
        profile.phone = phone or profile.phone
        profile.gender = gender or profile.gender
        profile.date_of_birth = date_of_birth or profile.date_of_birth
        profile.save(update_fields=['name', 'phone', 'gender', 'date_of_birth'])

    for position, kin in enumerate(next_of_kin, start=1):
        if not kin:
            continue
        NextOfKin.objects.update_or_create(
            profile=profile, position=position,
            defaults={'name': kin.get('name', ''), 'phone': kin.get('phone', ''),
                      'relationship': kin.get('relationship', NextOfKin.GUARDIAN)},
        )
    return profile


@transaction.atomic
def open_application(*, profile, semester, programme, class_level, kind,
                     returning_to=None, actor=None, note='', ignore_window=False):
    """Start one student's admission at the first desk of its route."""
    if not ignore_window and not admissions_are_open(semester):
        raise AdmissionError(
            f'Admissions are not open for {semester}. The admission officer opens the '
            f'window under Admissions.')
    if Application.objects.filter(profile=profile, semester=semester).exclude(
            state__in=[Application.REJECTED, Application.CANCELLED]).exists():
        raise AdmissionError(f'{profile.nactvet_reg_no} already has an application for {semester}.')
    if SemesterRegistration.objects.filter(profile=profile, semester=semester).exclude(
            status=SemesterRegistration.CANCELLED).exists():
        raise AdmissionError(f'{profile.nactvet_reg_no} is already registered for {semester}.')

    application = Application.objects.create(
        profile=profile, semester=semester, programme=programme, class_level=class_level,
        kind=kind, state=Application.ROUTES[kind][0], returning_to=returning_to,
        note=note[:300], created_by=actor,
    )
    # A first year waits at intake for the admission office; everybody else
    # starts at finance, so their charges are raised now.
    if application.state == Application.FINANCE:
        _raise_charges(application, actor=actor)
    return application


def _raise_charges(application, *, actor=None):
    """Bill the student for the year they are being admitted into.

    Raised on the way into finance, not at the end: the student pays at the
    bank and brings the receipt to the accountant, so the charge has to exist
    before the desk can be cleared.
    """
    try:
        return finance.generate_charges(
            application.profile, application.semester.academic_year, actor=actor,
            class_level=application.class_level, programme=application.programme)
    except ValueError as exc:
        # No due dates set on the fee structure yet. The application still
        # stands; the accountant's bulk run catches it up.
        logger.warning('Could not bill %s on application: %s',
                       application.profile.nactvet_reg_no, exc)
        return []


# ── moving it along ───────────────────────────────────────────────────────────

@transaction.atomic
def complete_step(application, *, actor=None, note=''):
    """Clear the desk the application is at, and hand it to the next one."""
    if not application.is_open:
        raise AdmissionError(f'This application is {application.get_state_display().lower()}.')

    step = application.state
    following = application.next_state
    # The last desk is the admission itself, and that stamps its own step —
    # doing it here as well recorded the admission twice.
    if following == Application.ADMITTED:
        return admit(application, actor=actor, note=note)

    ApplicationStep.objects.create(
        application=application, step=step, done_by=actor, note=note[:300])
    application.state = following
    application.save(update_fields=['state', 'updated_at'])
    if following == Application.FINANCE:
        _raise_charges(application, actor=actor)
    logger.info('Application %s moved from %s to %s', application.id, step, following)
    return application


@transaction.atomic
def send_back(application, *, to, actor=None, reason=''):
    """Hand an application back to an earlier desk — papers missing, a payment
    that turned out to be somebody else's, a level entered wrongly."""
    route = application.route
    if to not in route:
        raise AdmissionError(f'{to!r} is not a desk on this application.')
    if application.state in route and route.index(to) >= route.index(application.state):
        raise AdmissionError('Send an application back, not forward.')
    if not reason:
        raise AdmissionError('Sending an application back needs a reason.')

    ApplicationStep.objects.create(
        application=application, step=application.state, done_by=actor,
        note=f'Sent back to {to}: {reason}'[:300])
    application.state = to
    application.save(update_fields=['state', 'updated_at'])
    return application


@transaction.atomic
def refuse(application, *, actor=None, reason=''):
    if not reason:
        raise AdmissionError('Refusing an application needs a reason.')
    ApplicationStep.objects.create(
        application=application, step=application.state, done_by=actor,
        note=f'Refused: {reason}'[:300])
    application.state = Application.REJECTED
    application.decided_reason = reason[:300]
    application.save(update_fields=['state', 'decided_reason', 'updated_at'])
    return application


# ── the requirements ──────────────────────────────────────────────────────────

def requirements_for(application):
    """What this student has to have this semester."""
    rows = AdmissionRequirement.objects.filter(is_active=True).prefetch_related('applies_to_levels')
    wanted = []
    for requirement in rows:
        levels = list(requirement.applies_to_levels.all())
        if levels and application.class_level not in levels:
            continue
        if requirement.frequency == AdmissionRequirement.ONCE:
            # Only when they join: a student who has been checked for it before
            # is not asked again.
            seen = RequirementCheck.objects.filter(
                application__profile=application.profile, requirement=requirement).exclude(
                application=application).exists()
            if seen:
                continue
        wanted.append(requirement)
    return wanted


@transaction.atomic
def record_requirement(application, requirement, status, *, actor=None, note=''):
    """Say whether the student has it, and charge them when they do not."""
    if status not in dict(RequirementCheck.STATUS_CHOICES):
        raise AdmissionError(f'{status!r} is not a requirement outcome.')

    check, _ = RequirementCheck.objects.update_or_create(
        application=application, requirement=requirement,
        defaults={'status': status, 'note': note[:300], 'checked_by': actor},
    )
    if status == RequirementCheck.MISSING and requirement.charge_type_id and check.charge is None:
        year = application.semester.academic_year
        structure = finance.structures_for(
            application.class_level, year, application.programme).get(requirement.charge_type_id)
        if structure is None:
            logger.warning('No rate for %s at %s; %s not charged',
                           requirement, application.class_level, application.profile.nactvet_reg_no)
        else:
            schedule = list(structure.installment_schedule.all())
            due = schedule[0].due_date if schedule else timezone.localdate()
            check.charge = finance.raise_charge(
                application.profile, requirement.charge_type, year,
                structure.amount, due, actor=actor,
                note=f'{requirement.name} — not held at admission')
            check.save(update_fields=['charge'])
    elif status != RequirementCheck.MISSING and check.charge is not None:
        # They turned up with it after all. The charge is reversed by the
        # accountant, not silently deleted here; the check simply stops
        # pointing at it.
        logger.info('%s now has %s; charge %s stands until the accountant waives it',
                    application.profile.nactvet_reg_no, requirement, check.charge_id)
    return check


# ── the portal password ───────────────────────────────────────────────────────

#: Letters a person cannot misread when a password is written on a slip and
#: handed over: no I, no O, nothing that argues with 1 or 0.
PIN_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'


def generate_portal_pin():
    """A password to hand a new student: four letters, four digits."""
    letters = ''.join(secrets.choice(PIN_LETTERS) for _ in range(4))
    digits = ''.join(secrets.choice('23456789') for _ in range(4))
    return f'{letters}-{digits}'


@transaction.atomic
def issue_portal_pin(profile, *, force=False, actor=None):
    """Give this student a password for the portal, and return it once.

    Only the hash is kept, so this is the single moment anybody can read it —
    the officer writes it on the admission slip and hands it over. A student
    who already has one keeps it unless the office is deliberately resetting
    it: somebody coming back after a year knows their own password, and
    changing it under them helps nobody.
    """
    enrollments = list(profile.enrollments.all())
    if not enrollments:
        return None
    if not force and any(enrollment.portal_pin_hash for enrollment in enrollments):
        return None

    pin = generate_portal_pin()
    for enrollment in enrollments:
        # Every enrollment carries the password, because login checks them one
        # by one; they must all say the same thing.
        enrollment.set_portal_pin(pin, require_change=True)
        enrollment.save(update_fields=['portal_pin_hash', 'must_change_portal_password', 'portal_pin_set_at'])
    logger.info('Issued a portal password to %s (%d enrollment(s))',
                profile.nactvet_reg_no, len(enrollments))
    return pin


# ── admitting ─────────────────────────────────────────────────────────────────

@transaction.atomic
def admit(application, *, actor=None, note=''):
    """The last step: this person becomes a student of this semester.

    One place, one transaction: the college ID is issued, the registration is
    made on the right footing, the modules are enrolled and billed, and the
    student's standing says they are studying.
    """
    if application.state not in (Application.ADMISSION, *Application.OPEN_STATES):
        raise AdmissionError(f'This application is {application.get_state_display().lower()}.')

    missing = [check.requirement.name for check in application.requirement_checks.all()
               if check.status == RequirementCheck.MISSING and check.charge is None
               and check.requirement.mandatory and check.requirement.charge_type_id is None]
    if missing:
        logger.warning('Admitting %s with unmet requirement(s): %s',
                       application.profile.nactvet_reg_no, ', '.join(missing))

    profile = application.profile
    issue_college_id(profile, application.semester.academic_year, application.programme)

    registration = SemesterRegistration.objects.create(
        profile=profile, semester=application.semester, programme=application.programme,
        class_level=application.class_level,
        kind=REGISTRATION_KIND[application.kind], created_by=actor,
    )
    progression.set_standing(
        profile, StudentStanding.ACTIVE, actor=actor, semester=application.semester,
        class_level=application.class_level, programme=application.programme,
        reason=f'Admitted for {application.semester}.')
    enrolled = progression.enroll_registration(registration, actor=actor)

    # They leave with a password for the portal. Returned on the application
    # object rather than stored: after this moment only the hash exists.
    application.portal_pin = issue_portal_pin(profile, actor=actor)

    ApplicationStep.objects.create(
        application=application, step=Application.ADMISSION, done_by=actor,
        note=note[:300] or 'Admitted.')
    application.state = Application.ADMITTED
    application.registration = registration
    application.save(update_fields=['state', 'registration', 'updated_at'])

    logger.info('Admitted %s into %s: %d module(s)',
                profile.nactvet_reg_no, application.semester, len(enrolled))
    return application


# ── the students the college is expecting back ────────────────────────────────

def due_back(semester):
    """Who the college should be seeing at this semester's admissions.

    The discontinued and postponed students the year end recorded, matched to
    the semester they said they would come back to, and without an application
    already open.
    """
    standings = (StudentStanding.objects
                 .filter(status__in=sorted(StudentStanding.AWAY))
                 .select_related('profile', 'programme', 'class_level', 'return_year'))
    applied = set(Application.objects.filter(semester=semester).exclude(
        state__in=[Application.REJECTED, Application.CANCELLED]).values_list('profile_id', flat=True))
    registered = set(SemesterRegistration.objects.filter(semester=semester).exclude(
        status=SemesterRegistration.CANCELLED).values_list('profile_id', flat=True))

    waiting = []
    for standing in standings:
        if standing.profile_id in applied or standing.profile_id in registered:
            continue
        if standing.return_semester_number and standing.return_semester_number != semester.number:
            continue
        if standing.return_year and standing.return_year.name > semester.academic_year.name:
            continue
        waiting.append(standing)
    return waiting
