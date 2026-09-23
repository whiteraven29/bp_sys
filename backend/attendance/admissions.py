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
    AdmissionRequirement, AdmissionWindow, Application, ApplicationStep, ChargeType, ClassLevel,
    CollegeIdFormat, Module, NextOfKin, Programme, RequirementCheck, Semester, SemesterRegistration,
    SemesterReview, Student, StudentProfile, StudentStanding,
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
def record_details(profile, *, name=None, phone=None, gender=None, date_of_birth=None,
                   next_of_kin=None, actor=None):
    """The records desk writes down who the student is.

    Name, phone, gender, date of birth and two next of kin — kept by records,
    read by finance and admission. Only what is given is changed; a next of kin
    left blank is left as it was.
    """
    fields = []
    if name is not None and name.strip():
        profile.name = name.strip(); fields.append('name')
    if phone is not None:
        profile.phone = phone.strip(); fields.append('phone')
    if gender is not None:
        if gender not in ('', StudentProfile.MALE, StudentProfile.FEMALE):
            raise AdmissionError('Gender is M or F.')
        profile.gender = gender; fields.append('gender')
    if date_of_birth is not None:
        profile.date_of_birth = date_of_birth or None; fields.append('date_of_birth')
    if fields:
        profile.save(update_fields=fields)
        # Enrollments carry a copy of the name for the class lists.
        if 'name' in fields:
            Student.objects.filter(profile=profile).update(name=profile.name)

    for position, kin in enumerate(next_of_kin or [], start=1):
        if position > 2 or not kin or not (kin.get('name') or '').strip():
            continue
        relationship = kin.get('relationship') or NextOfKin.GUARDIAN
        if relationship not in dict(NextOfKin.RELATIONSHIP_CHOICES):
            raise AdmissionError(f'{relationship!r} is not a relationship the form knows.')
        NextOfKin.objects.update_or_create(
            profile=profile, position=position,
            defaults={'name': kin['name'].strip(), 'phone': (kin.get('phone') or '').strip(),
                      'relationship': relationship},
        )
    logger.info('Details recorded for %s', profile.nactvet_reg_no)
    return profile


def missing_details(profile):
    """What the records desk has still to take down."""
    missing = []
    if not profile.phone:
        missing.append('phone')
    if not profile.gender:
        missing.append('gender')
    if not profile.date_of_birth:
        missing.append('date of birth')
    if profile.next_of_kin.count() < 2:
        missing.append('next of kin')
    return missing


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


# ── what the student will study, and so what they pay ───────────────────────

def is_repeat_only(profile):
    """A student held at their level to repeat what they failed. They study
    those modules only, and pay the per-module repeat rate — not a year's fees."""
    standing = StudentStanding.objects.filter(profile=profile).first()
    return bool(standing and standing.status == StudentStanding.REPEATING)


def planned_modules(application):
    """The modules this student will be enrolled in when they are admitted,
    and which of them are repeat sittings.

    The same choice admission makes when it enrolls them, made in advance so
    finance knows what it is billing for.
    """
    semester = application.semester
    owed = {repeat.module_code.upper(): repeat
            for repeat in progression.open_repeats(application.profile)
            if repeat.semester_number == semester.number}
    offered = list(Module.objects.filter(semester=semester).select_related('class_level'))
    repeats = [module for module in offered if module.code.upper() in owed
               and module.programme_id in (application.programme_id, None)]
    if is_repeat_only(application.profile):
        return [(module, True) for module in repeats]
    own = [module for module in offered
           if module.class_level_id == application.class_level_id
           and module.programme_id == application.programme_id
           and module.code.upper() not in owed]
    return [(module, False) for module in own] + [(module, True) for module in repeats]


def academic_summary(application):
    """What the results say about this student, for the desks that are not the
    examination office — finance above all, which bills a full year for a
    student who is moving on and a module at a time for one who is repeating.
    """
    profile = application.profile
    standing = StudentStanding.objects.filter(profile=profile).select_related(
        'class_level').first()
    review = (SemesterReview.objects.filter(profile=profile)
              .exclude(semester=application.semester)
              .select_related('semester__academic_year')
              .order_by('-semester__academic_year__name', '-semester__number').first())
    planned = planned_modules(application)
    repeat_only = is_repeat_only(profile)

    if application.kind == Application.NEW:
        results = 'New to the college — no results yet.'
    elif review is None:
        results = 'No semester review on record.'
    elif not review.is_confirmed:
        results = (f'{review.semester.label}: not confirmed yet by the examination office '
                   f'({review.get_proposed_display().lower()}).')
    else:
        gpa = f', GPA {review.gpa}' if review.gpa is not None else ''
        results = f'{review.semester.label}: {review.get_confirmed_display()}{gpa}.'

    repeats = [module.code for module, is_repeat in planned if is_repeat]
    if repeat_only:
        billing = f'Repeat rate × {len(repeats)} module(s)'
    elif application.kind == Application.READMISSION:
        billing = 'Full fees, one-time charges again (readmission)'
    elif application.kind == Application.NEW:
        billing = 'Full fees and one-time charges (first year)'
    else:
        billing = 'Full fees for the level' + (
            f', plus repeat rate × {len(repeats)}' if repeats else '')

    return {
        'standing': standing.get_status_display() if standing else 'No standing recorded',
        'results': results,
        'results_confirmed': bool(review and review.is_confirmed) or application.kind == Application.NEW,
        'repeat_only': repeat_only,
        'billing': billing,
        'module_count': len(planned),
        'modules': [{'code': module.code, 'name': module.name, 'repeat': is_repeat}
                    for module, is_repeat in planned],
    }


def _raise_charges(application, *, actor=None):
    """Bill the student for the year they are being admitted into.

    Raised on the way into finance, not at the end: the student pays at the
    bank and brings the receipt to the accountant, so the charge has to exist
    before the desk can be cleared.

    A repeater is billed the repeat rate for each module they will sit again
    and nothing else. Admission finds those charges when it enrolls them and
    does not raise them a second time.
    """
    if is_repeat_only(application.profile):
        raised = []
        for module, is_repeat in planned_modules(application):
            if not is_repeat:
                continue
            try:
                charge, _ = finance.charge_declaration(
                    application.profile, module, ChargeType.REPEAT_MODULE, actor=actor,
                    reason=f'Repeat of {module.code}')
                raised.append(charge)
            except finance.DeclarationError as exc:
                logger.warning('No repeat rate for %s on %s: %s',
                               application.profile.nactvet_reg_no, module.code, exc)
        return raised
    year = application.semester.academic_year
    if application.kind == Application.CONTINUING:
        # Where they lived last year, until they say otherwise at the desk.
        finance.carry_residence(application.profile, year, actor=actor,
                                class_level=application.class_level, programme=application.programme)
    try:
        created = finance.generate_charges(
            application.profile, year, actor=actor,
            class_level=application.class_level, programme=application.programme,
            entry=entry_of(application),
            readmitted=application.kind == Application.READMISSION)
    except ValueError as exc:
        # No due dates set on the fee structure yet. The application still
        # stands; the accountant's bulk run catches it up.
        logger.warning('Could not bill %s on application: %s',
                       application.profile.nactvet_reg_no, exc)
        return []
    if application.semester.number == 2:
        # Coming back for semester 2 — after a postponement or a readmission —
        # they pay for semester 2, not the semester they were not here for.
        sem2 = finance.semester_two_dates(year)
        finance.reset_fees(application.profile, year, actor=actor,
                           reason=f'joins in {application.semester.label}',
                           charges=[charge for charge in created
                                    if finance.charge_semester_number(charge, sem2) == 1])
    # What they paid before a postponement settles the new bill.
    finance.apply_credit(application.profile, actor=actor)
    return created


def entry_of(application):
    """New or continuing, for the bill: a first year and a readmitted student
    pay as new students do, a continuing student as a continuing one."""
    from .models import PaymentSchedule
    return (PaymentSchedule.CONTINUING if application.kind == Application.CONTINUING
            else PaymentSchedule.NEW)


def set_residence(application, residence, *, actor=None):
    """Day or hostel, stated at the finance desk — the hostel fee follows."""
    if application.state != Application.FINANCE:
        raise AdmissionError('Where the student will live is stated at the finance desk.')
    try:
        return finance.set_residence(
            application.profile, application.semester.academic_year, residence, actor=actor,
            from_semester_number=application.semester.number,
            class_level=application.class_level, programme=application.programme,
            entry=entry_of(application))
    except ValueError as exc:
        raise AdmissionError(str(exc)) from exc


def open_continuing(profile, semester, *, programme, class_level, actor=None):
    """The year end hands a student to admissions: an application waiting at
    finance, for the level the results earned them.

    They pay, records double-checks them, admission confirms they are here —
    and only then are they registered. Safe to call twice.
    """
    existing = Application.objects.filter(profile=profile, semester=semester).exclude(
        state__in=[Application.REJECTED, Application.CANCELLED]).first()
    if existing is not None:
        return existing
    if SemesterRegistration.objects.filter(profile=profile, semester=semester).exclude(
            status=SemesterRegistration.CANCELLED).exists():
        return None
    return open_application(
        profile=profile, semester=semester, programme=programme, class_level=class_level,
        kind=Application.CONTINUING, actor=actor, ignore_window=True,
        note='Opened by the year end.')


def closing_semester(semester):
    """The semester whose review decides who continues into this one: semester
    2 of the year before, for a year's first semester; none otherwise."""
    if semester.number != Semester.SEM1:
        return None
    y1, y2 = semester.academic_year.name.split('/')
    return (Semester.objects.select_related('academic_year')
            .filter(academic_year__name=f'{int(y1) - 1}/{int(y2) - 1}', number=Semester.SEM2)
            .first())


@transaction.atomic
def queue_continuing(semester, *, actor=None):
    """Put last year's continuing students in the finance queue for this year.

    The year-end advance does this itself. This is for a year that was opened
    another way — or turned before the queue existed — so every student the
    last review sent on is waiting at finance. It follows that review and
    nothing else: a student without a confirmed review is reported, not
    guessed at, and anybody already registered or applied for this year is
    left where they are. Safe to run again.
    """
    closing = closing_semester(semester)
    if closing is None:
        raise AdmissionError('Continuing students are queued for a first semester, '
                             'from the review of the semester 2 before it.')
    plan = progression.plan_advance(closing)
    wanted = [move for move in plan['moves']
              if move.get('registers') and move.get('to_semester') == Semester.SEM1]
    profiles = {profile.id: profile for profile in
                StudentProfile.objects.filter(id__in=[move['profile_id'] for move in wanted])}
    programmes = {programme.id: programme for programme in
                  Programme.objects.filter(id__in={move['programme_id'] for move in wanted})}
    levels = {level.id: level for level in
              ClassLevel.objects.filter(id__in={move['to_level_id'] for move in wanted})}
    applied = set(Application.objects.filter(semester__academic_year=semester.academic_year)
                  .exclude(state__in=[Application.REJECTED, Application.CANCELLED])
                  .values_list('profile_id', flat=True))
    registered = set(SemesterRegistration.objects.filter(semester__academic_year=semester.academic_year)
                     .exclude(status=SemesterRegistration.CANCELLED)
                     .values_list('profile_id', flat=True))

    summary = {'from': closing.label, 'queued': 0, 'already_applied': 0,
               'already_registered': 0, 'unplaced': [], 'blocked': plan['blocked']}
    for move in wanted:
        profile = profiles[move['profile_id']]
        if profile.id in registered:
            summary['already_registered'] += 1
            continue
        if profile.id in applied:
            summary['already_applied'] += 1
            continue
        programme = programmes.get(move['programme_id'])
        level = levels.get(move['to_level_id'])
        if programme is None or level is None:
            summary['unplaced'].append({'reg_no': profile.nactvet_reg_no, 'name': profile.name,
                                        'reason': 'no programme or level on the review'})
            continue
        open_continuing(profile, semester, programme=programme, class_level=level, actor=actor)
        summary['queued'] += 1
    logger.info('Queued %s continuing student(s) for %s from %s', summary['queued'],
                semester, closing)
    return summary


# ── moving it along ───────────────────────────────────────────────────────────

@transaction.atomic
def complete_step(application, *, actor=None, note=''):
    """Clear the desk the application is at, and hand it to the next one."""
    if not application.is_open:
        raise AdmissionError(f'This application is {application.get_state_display().lower()}.')

    step = application.state
    # A first year is written down at the records desk, and it is not cleared
    # with half a record: finance and admission read from it. A continuing or
    # returning student is already on file — records double-checks them, and a
    # gap is flagged, not a reason to hold them.
    if step == Application.INFORMATION:
        missing = missing_details(application.profile)
        if missing:
            raise AdmissionError(
                'Records has not finished this student: ' + ', '.join(missing) + ' still to record.')
    # The one thing that holds a student: not having paid. Judged by the
    # college's own clearance rule on the charges marked "blocks registration",
    # the same rule every other screen uses — and the accountant's override
    # lifts it, for the student the college has agreed to let through.
    # A new student's bill depends on whether they live in the hostel, so
    # finance does not pass them on until they have said.
    if step == Application.FINANCE and application.kind != Application.CONTINUING and \
            finance.residence_for(application.profile, application.semester.academic_year) is None:
        raise AdmissionError('Say whether they will live in the hostel or at home (day) first.')
    if step == Application.FINANCE:
        clearance = finance.exam_clearance(
            application.profile, application.semester.academic_year,
            ChargeType.REGISTRATION, semester=application.semester)
        if not clearance['cleared']:
            raise AdmissionError(
                f'Not paid yet: {clearance["reason"]}. An override under Waivers & Overrides '
                f'lets them through.')
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


# ── the items a student must have ─────────────────────────────────────────────
#
# The TPH book, a calculator, rim paper, insurance. Finance checks them: at the
# finance desk when a student is admitted, and again in semester 2 for what is
# checked every semester. An item the student does not have is a debt at the
# accountant's rate for it, never a reason to hold them — the charge types these
# items bill under are left out of every clearance (see finance.item_charge_type_ids).
#
# Insurance is on the published bill for everybody (the medical fee). A student
# who has their own has that fee waived when finance marks it; for the items the
# college does not bill by default, marking one missing raises the charge.

#: The desks an item may be marked at: finance checks them, and the admission
#: office records one the student brings back while held at its desk.
ITEM_DESKS = (Application.FINANCE, Application.ADMISSION)
WAIVER_MARK = 'Item check: '


def items_due(profile, semester, class_level, *, requirements=None, history=None):
    """What this student has to show this semester.

    Every-semester items each semester; once-a-year items once in the academic
    year; once-only items when they join. `requirements` and `history` (this
    student's checks from other semesters) may be passed in when a whole class
    is being listed.
    """
    if requirements is None:
        requirements = list(AdmissionRequirement.objects.filter(is_active=True)
                            .prefetch_related('applies_to_levels'))
    if history is None:
        history = list(RequirementCheck.objects.filter(profile=profile).exclude(semester=semester)
                       .select_related('semester'))
    wanted = []
    for requirement in requirements:
        levels = list(requirement.applies_to_levels.all())
        if levels and class_level not in levels:
            continue
        before = [check for check in history if check.requirement_id == requirement.id]
        if requirement.frequency == AdmissionRequirement.ONCE and before:
            continue
        if requirement.frequency == AdmissionRequirement.EVERY_YEAR and any(
                check.semester.academic_year_id == semester.academic_year_id for check in before):
            continue
        wanted.append(requirement)
    return wanted


def requirements_for(application):
    """What this student has to have for the semester they are applying for."""
    return items_due(application.profile, application.semester, application.class_level)


def _billed_by_default(requirement, profile, academic_year):
    """The year's own charges for an item the college bills everybody for —
    the medical fee, for insurance."""
    from .models import StudentCharge
    if not requirement.charge_type_id or requirement.charge_type.applies != ChargeType.AUTOMATIC:
        return None
    return list(finance.with_balances(StudentCharge.objects.filter(
        profile=profile, charge_type=requirement.charge_type, academic_year=academic_year,
        source=StudentCharge.STRUCTURE)))


def _waive_rest(charge, reason, actor):
    """Take what is still owed on this charge off the bill. Money already paid
    stays paid."""
    if charge.waived_amount and not charge.waived_reason.startswith(WAIVER_MARK):
        return          # the accountant waived it for their own reasons; leave it
    outstanding = finance.charge_balance(charge)
    if outstanding <= 0:
        return
    finance.waive_charge(charge, charge.waived_amount + outstanding, WAIVER_MARK + reason, actor=actor)


def _unwaive(charge, actor):
    """Put back a waiver this rule made, when the item turns out to be missing
    after all."""
    if charge.waived_amount and charge.waived_reason.startswith(WAIVER_MARK):
        finance.waive_charge(charge, 0, '', actor=actor)


@transaction.atomic
def record_item(profile, semester, requirement, status, *, class_level, programme,
                application=None, actor=None, note=''):
    """Say whether the student has it, and let the bill follow.

    Missing: charged at the accountant's rate (or, for insurance, the medical
    fee on the year's bill stands). Has it, or waived by the college: nothing is
    owed for it — what is still unpaid on its charge is waived, what was paid
    stays paid.
    """
    if status not in dict(RequirementCheck.STATUS_CHOICES):
        raise AdmissionError(f'{status!r} is not a requirement outcome.')

    defaults = {'status': status, 'note': note[:300], 'checked_by': actor}
    if application is not None:
        defaults['application'] = application
    check, _ = RequirementCheck.objects.update_or_create(
        profile=profile, semester=semester, requirement=requirement, defaults=defaults)

    year = semester.academic_year
    default_charges = _billed_by_default(requirement, profile, year)
    if default_charges is not None:
        for charge in default_charges:
            if status == RequirementCheck.MISSING:
                _unwaive(charge, actor)
            else:
                _waive_rest(charge, f'has own {requirement.name.lower()}', actor)
        if default_charges and check.charge_id is None:
            check.charge = default_charges[0]
            check.save(update_fields=['charge'])
        return check

    if status == RequirementCheck.MISSING:
        if check.charge is not None:
            _unwaive(check.charge, actor)
        elif requirement.charge_type_id:
            structure = finance.structures_for(class_level, year, programme).get(
                requirement.charge_type_id)
            if structure is None:
                logger.warning('No rate for %s at %s; %s not charged',
                               requirement, class_level, profile.nactvet_reg_no)
            else:
                schedule = list(structure.installment_schedule.all())
                due = max(schedule[0].due_date, timezone.localdate()) if schedule else timezone.localdate()
                check.charge = finance.raise_charge(
                    profile, requirement.charge_type, year, structure.amount, due,
                    semester=semester, actor=actor,
                    note=f'{requirement.name} — not held, {semester.label}')
                check.save(update_fields=['charge'])
    elif check.charge is not None:
        charge = finance.with_balances(type(check.charge).objects.filter(pk=check.charge_id)).get()
        _waive_rest(charge, f'{requirement.name.lower()} brought', actor)
    return check


def record_requirement(application, requirement, status, *, actor=None, note=''):
    """Mark an item on an application — at the finance desk, or at the
    admission desk when a held student brings it back."""
    return record_item(application.profile, application.semester, requirement, status,
                       class_level=application.class_level, programme=application.programme,
                       application=application, actor=actor, note=note)


def items_owed(profile, semester):
    """What the student still owes for items this semester, for the admission
    desk to see before it admits them."""
    rows = []
    checks = (RequirementCheck.objects.filter(profile=profile, semester=semester,
                                              status=RequirementCheck.MISSING)
              .select_related('requirement__charge_type'))
    for check in checks:
        requirement = check.requirement
        charges = _billed_by_default(requirement, profile, semester.academic_year)
        if charges is None:
            from .models import StudentCharge
            charges = list(finance.with_balances(
                StudentCharge.objects.filter(pk=check.charge_id))) if check.charge_id else []
        amount = sum((charge.payable for charge in charges), finance.ZERO)
        balance = sum((finance.charge_balance(charge) for charge in charges), finance.ZERO)
        rows.append({
            'requirement': requirement.id, 'name': requirement.name,
            'charged': bool(charges), 'amount': str(finance.money(amount)),
            'balance': str(finance.money(balance)), 'paid': bool(charges) and balance <= 0,
        })
    return rows


def items_unchecked(application):
    """Items due that finance has not marked yet."""
    marked = set(RequirementCheck.objects.filter(
        profile=application.profile, semester=application.semester).values_list('requirement_id', flat=True))
    return [requirement.name for requirement in requirements_for(application)
            if requirement.id not in marked]


# ── holding a student at the admission desk ──────────────────────────────────

def hold(application, *, reason, actor=None):
    """Keep the student waiting at the admission desk — for the items they owe,
    say. Admitting them later lifts it."""
    if application.state != Application.ADMISSION:
        raise AdmissionError('Only a student at the admission desk can be put on hold.')
    reason = (reason or '').strip()
    if not reason:
        raise AdmissionError('Say what they are being held for.')
    application.on_hold = True
    application.hold_reason = reason[:300]
    application.held_at = timezone.now()
    application.held_by = actor
    application.save(update_fields=['on_hold', 'hold_reason', 'held_at', 'held_by', 'updated_at'])
    logger.info('Application %s held: %s', application.id, reason)
    return application


def release_hold(application, *, actor=None):
    """Lift a hold without admitting — the officer changed their mind."""
    if not application.on_hold:
        return application
    application.on_hold = False
    application.save(update_fields=['on_hold', 'updated_at'])
    logger.info('Application %s released from hold by %s', application.id, actor)
    return application


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

    owed = [row['name'] for row in items_owed(application.profile, application.semester)
            if not row['paid']]
    if owed:
        logger.info('Admitting %s owing for item(s): %s',
                    application.profile.nactvet_reg_no, ', '.join(owed))

    profile = application.profile
    issue_college_id(profile, application.semester.academic_year, application.programme)

    kind = REGISTRATION_KIND[application.kind]
    if application.kind == Application.CONTINUING and is_repeat_only(profile):
        kind = SemesterRegistration.REPEATING
    registration = SemesterRegistration.objects.create(
        profile=profile, semester=application.semester, programme=application.programme,
        class_level=application.class_level, kind=kind, created_by=actor,
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
    application.on_hold = False
    application.save(update_fields=['state', 'registration', 'on_hold', 'updated_at'])

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
