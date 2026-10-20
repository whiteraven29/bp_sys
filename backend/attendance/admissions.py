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
import re
import secrets

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import finance, progression
from .models import (
    AcademicYear, AdmissionRequirement, AdmissionWindow, Application, ApplicationStep, ChargeType, ClassLevel,
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
        veta_pattern=previous.veta_pattern if previous else '',
        college_code=previous.college_code if previous else 'BPH',
        sequence_width=previous.sequence_width if previous else 3,
        starts_at=1, next_number=1, veta_starts_at=1,
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
    # A VETA course student is numbered in the VETA series when the office has
    # set its pattern: a different card number, a counter of its own.
    veta = fmt.veta_series(programme)
    if veta:
        # Each VETA programme counts on its own (hair dressing 001…, computer basics 001…).
        from .models import CollegeIdCounter
        counter, _ = CollegeIdCounter.objects.select_for_update().get_or_create(
            id_format=fmt, programme=programme, defaults={'next_number': fmt.veta_starts_at})
        number = max(counter.next_number, fmt.veta_starts_at)
    else:
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
    if veta:
        counter.next_number = number + 1
        counter.save(update_fields=['next_number'])
    else:
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
def capture_student(*, nactvet_reg_no, name='', phone='', gender='', date_of_birth=None,
                    next_of_kin=(), form_four_index='', actor=None,
                    first_name='', middle_name='', last_name=''):
    """Write down a person the college has not met before.

    A person on file is not yet a student: they have no registration, no
    modules and no place in a class until they are admitted. Their NACTVET
    number may not exist yet — the authority issues it during the admission —
    so a placeholder is accepted and corrected later.
    """
    reg_no = (nactvet_reg_no or '').strip().upper()
    # First, middle and last name (said 20 October 2026); a whole name is split.
    if (first_name or '').strip() or (last_name or '').strip():
        if not ((first_name or '').strip() and (last_name or '').strip()):
            raise AdmissionError('The first name and the last name are both needed.')
        name = StudentProfile.join_name(first_name, middle_name, last_name)
        parts = (first_name.strip(), (middle_name or '').strip(), last_name.strip())
    else:
        parts = StudentProfile.split_name(name)
    if not (name or '').strip():
        raise AdmissionError('A name is needed.')
    if not reg_no:
        # The authority issues the NACTVET number during the admission, so a
        # first year often has none when they walk in. The college holds them
        # under a number of its own until the real one arrives at intake.
        reg_no = temporary_number()

    existing = StudentProfile.objects.filter(nactvet_reg_no=reg_no).first()
    index = clean_form_four_index(form_four_index, existing)
    profile, created = StudentProfile.objects.get_or_create(
        nactvet_reg_no=reg_no,
        defaults={'name': name.strip(), 'phone': phone, 'gender': gender,
                  'date_of_birth': date_of_birth, 'form_four_index': index,
                  'first_name': parts[0][:80], 'middle_name': parts[1][:80], 'last_name': parts[2][:80]},
    )
    if not created:
        profile.name = name.strip() or profile.name
        profile.first_name, profile.middle_name, profile.last_name = (part[:80] for part in parts)
        profile.phone = phone or profile.phone
        profile.gender = gender or profile.gender
        profile.date_of_birth = date_of_birth or profile.date_of_birth
        profile.form_four_index = index or profile.form_four_index
        profile.save(update_fields=['name', 'phone', 'gender', 'date_of_birth', 'form_four_index',
                                    'first_name', 'middle_name', 'last_name'])

    for position, kin in enumerate(next_of_kin, start=1):
        if not kin:
            continue
        NextOfKin.objects.update_or_create(
            profile=profile, position=position,
            defaults={'name': kin.get('name', ''), 'phone': kin.get('phone', ''),
                      'relationship': kin.get('relationship', NextOfKin.GUARDIAN)},
        )
    return profile


#: NECTA's Form Four (CSEE) index number: centre and candidate, and the year
#: sat — S0123/0045/2021 for a school candidate, P… for a private one. The
#: year is left off on some older slips.
FORM_FOUR_INDEX = re.compile(r'^[SP]\d{4}/\d{4}(/\d{4})?$')


def clean_form_four_index(value, profile=None):
    """The index number in its printed form, or AdmissionError saying what is
    wrong with it. Blank stays blank."""
    index = re.sub(r'[\s.-]+', '/', (value or '').strip().upper()).strip('/')
    if not index:
        return ''
    if not FORM_FOUR_INDEX.match(index):
        raise AdmissionError(f'{value!r} is not a Form Four index number. It reads like '
                             f'S0123/0045/2021: centre, candidate number and year.')
    taken = StudentProfile.objects.filter(form_four_index=index)
    if profile is not None:
        taken = taken.exclude(pk=profile.pk)
    other = taken.first()
    if other is not None:
        raise AdmissionError(f'Form Four index {index} is already on file for {other.name} '
                             f'({other.nactvet_reg_no}).')
    return index


def is_veta_only(profile):
    """A VETA course student with no PST record. They are not asked for a
    Form Four index number or a date of birth."""
    return (profile.course_applications.exists() and not profile.applications.exists()
            and not Student.objects.filter(profile=profile).exists())


def details_snapshot(profile):
    """Every detail records keeps, in words — compared before and after a save
    so each correction is written down (StudentDetailChange)."""
    snap = {
        'Name': profile.name, 'Phone': profile.phone, 'Gender': profile.get_gender_display() if profile.gender else '',
        'Date of birth': str(profile.date_of_birth or ''), 'Year of birth': str(profile.birth_year or ''),
        'Place of birth': profile.birth_place, 'Religion': profile.religion,
        'Education level now': profile.education_level, 'Form Four index': profile.form_four_index,
        'Address': profile.address, 'Fees paid by': profile.get_fee_payer_display() if profile.fee_payer else '',
        'Marital status': profile.get_marital_status_display() if profile.marital_status else '',
        'National ID': profile.national_id, 'NHIF card No.': profile.nhif_card_no,
        'Guarantor / sponsor': profile.sponsor_name, "Sponsor's phone": profile.sponsor_phone,
        'Long-term health problem': {True: 'Yes', False: 'No'}.get(profile.has_chronic_condition, ''),
        'Health declared': profile.health_declaration,
    }
    for kin in profile.next_of_kin.all():
        snap[f'Next of kin {kin.position}'] = ' · '.join(
            part for part in (kin.name, kin.get_relationship_display(), kin.phone, kin.address) if part)
    for row in profile.education.all():
        snap[row.get_level_display()] = ' · '.join(
            str(part) for part in (row.institution, row.start_year, row.end_year, row.index_number) if part)
    return snap


@transaction.atomic
def record_details(profile, *, name=None, phone=None, gender=None, date_of_birth=None,
                   next_of_kin=None, form_four_index=None, address=None, sponsor_name=None,
                   sponsor_phone=None, education=None, fee_payer=None, birth_year=None,
                   birth_place=None, religion=None, education_level=None, has_chronic_condition=None,
                   health_declaration=None, reason='', actor=None,
                   first_name=None, middle_name=None, last_name=None,
                   marital_status=None, national_id=None, nhif_card_no=None):
    """The records desk writes down who the student is.

    Name, phone, gender, date of birth, the Form Four index number, two next
    of kin, who pays the fees, and the admission form's other details — kept
    by records, read by finance and admission. Only what is given is changed;
    a next of kin left blank is left as it was. Every change is written down
    with what it was before (StudentDetailChange).
    """
    from django.utils import timezone as _tz
    before = details_snapshot(profile)
    fields = []
    if form_four_index is not None:
        profile.form_four_index = clean_form_four_index(form_four_index, profile)
        fields.append('form_four_index')
    if first_name is not None or last_name is not None:
        # The name in its parts (said 20 October 2026); the whole name follows.
        first = (first_name if first_name is not None else profile.first_name).strip()
        middle = (middle_name if middle_name is not None else profile.middle_name).strip()
        last = (last_name if last_name is not None else profile.last_name).strip()
        if not (first and last):
            raise AdmissionError('The first name and the last name are both needed.')
        profile.first_name, profile.middle_name, profile.last_name = first[:80], middle[:80], last[:80]
        profile.name = StudentProfile.join_name(first, middle, last)
        fields += ['first_name', 'middle_name', 'last_name', 'name']
    elif name is not None and name.strip():
        profile.name = name.strip(); fields.append('name')
        profile.first_name, profile.middle_name, profile.last_name = StudentProfile.split_name(profile.name)
        fields += ['first_name', 'middle_name', 'last_name']
    if marital_status is not None:
        if marital_status not in ('', *dict(StudentProfile.MARITAL_CHOICES)):
            raise AdmissionError('Marital status: single, married, divorced or widowed.')
        profile.marital_status = marital_status; fields.append('marital_status')
    if phone is not None:
        profile.phone = phone.strip(); fields.append('phone')
    if gender is not None:
        if gender not in ('', StudentProfile.MALE, StudentProfile.FEMALE):
            raise AdmissionError('Gender is M or F.')
        profile.gender = gender; fields.append('gender')
    if date_of_birth is not None:
        profile.date_of_birth = date_of_birth or None; fields.append('date_of_birth')
    if fee_payer is not None:
        if fee_payer not in ('', *dict(StudentProfile.FEE_PAYER_CHOICES)):
            raise AdmissionError('Who pays the fees: parents / guardian, a sponsor or the student.')
        profile.fee_payer = fee_payer; fields.append('fee_payer')
    if birth_year is not None:
        if birth_year in ('', 0):
            profile.birth_year = None
        else:
            try:
                year = int(birth_year)
            except (TypeError, ValueError):
                raise AdmissionError('Year of birth: a year, e.g. 1999.')
            if not 1930 <= year <= _tz.localdate().year:
                raise AdmissionError(f'Year of birth: {year} is not a year of birth.')
            profile.birth_year = year
        fields.append('birth_year')
    if has_chronic_condition is not None:
        profile.has_chronic_condition = {'yes': True, 'no': False, True: True, False: False}.get(
            has_chronic_condition, None)
        fields.append('has_chronic_condition')
    for field, value, limit in (('address', address, 300), ('sponsor_name', sponsor_name, 200),
                                ('sponsor_phone', sponsor_phone, 30), ('birth_place', birth_place, 120),
                                ('religion', religion, 60), ('education_level', education_level, 120),
                                ('health_declaration', health_declaration, 300),
                                ('national_id', national_id, 30), ('nhif_card_no', nhif_card_no, 30)):
        if value is not None:
            setattr(profile, field, str(value).strip()[:limit]); fields.append(field)
    if profile.fee_payer == StudentProfile.PAID_BY_SPONSOR and not profile.sponsor_name.strip():
        raise AdmissionError('A sponsor pays: name the sponsor.')
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
                      'relationship': relationship, 'address': (kin.get('address') or '').strip()[:200]},
        )
    for row in education or []:
        record_education(profile, **{key: row.get(key) for key in
                                     ('level', 'institution', 'start_year', 'end_year', 'index_number')})

    after = details_snapshot(profile)
    from .models import StudentDetailChange
    StudentDetailChange.objects.bulk_create([
        StudentDetailChange(profile=profile, field=label, old_value=str(before.get(label, ''))[:300],
                            new_value=str(after.get(label, ''))[:300], reason=(reason or '').strip()[:200],
                            changed_by=actor if getattr(actor, 'is_authenticated', False) else None)
        for label in sorted(set(before) | set(after))
        if (before.get(label) or '') != (after.get(label) or '')])
    logger.info('Details recorded for %s', profile.nactvet_reg_no)
    return profile


def _year(value, label):
    if value in (None, ''):
        return None
    from django.utils import timezone
    try:
        year = int(str(value).strip())
    except ValueError:
        raise AdmissionError(f'{label}: a year, e.g. 2019.')
    if not 1950 <= year <= timezone.localdate().year + 1:
        raise AdmissionError(f'{label}: {year} is not a year the form would show.')
    return year


def record_education(profile, *, level, institution=None, start_year=None, end_year=None, index_number=None):
    """One row of the enrolment form's schooling: O-level, A-level or college.
    An empty institution removes the row."""
    from .models import StudentEducation
    if level not in dict(StudentEducation.LEVEL_CHOICES):
        raise AdmissionError('O-level, A-level or college.')
    institution = (institution or '').strip()
    if not institution:
        StudentEducation.objects.filter(profile=profile, level=level).delete()
        return None
    label = dict(StudentEducation.LEVEL_CHOICES)[level]
    start, end = _year(start_year, f'{label} start year'), _year(end_year, f'{label} end year')
    if start and end and end < start:
        raise AdmissionError(f'{label}: the end year is before the start year.')
    row, _ = StudentEducation.objects.update_or_create(
        profile=profile, level=level,
        defaults={'institution': institution[:200], 'start_year': start, 'end_year': end,
                  'index_number': (index_number or '').strip()[:30]})
    return row


# ── the passport photo ────────────────────────────────────────────────────────

#: The browser sends a 300×300 JPEG, tens of kilobytes; anything far larger is
#: not what the page made.
MAX_PASSPORT_BYTES = 512 * 1024
PASSPORT_SIGNATURES = {b'\xff\xd8\xff': 'image/jpeg', b'\x89PNG\r\n\x1a\n': 'image/png'}


def passport_type(head):
    for signature, content_type in PASSPORT_SIGNATURES.items():
        if head.startswith(signature):
            return content_type
    return None


def set_passport(profile, upload, *, actor=None):
    """A student's passport photo, from the portal or the records desk. Only a
    real JPEG or PNG, small; the one it replaces is deleted."""
    from django.utils import timezone
    if not upload:
        raise AdmissionError('Choose a photo.')
    if upload.size > MAX_PASSPORT_BYTES:
        raise AdmissionError('That photo is too large — choose it again so the page can make it smaller.')
    upload.seek(0)
    content_type = passport_type(upload.read(8))
    upload.seek(0)
    if content_type is None:
        raise AdmissionError('A passport photo is a JPG or PNG picture.')
    old = profile.passport.name if profile.passport else ''
    extension = 'jpg' if content_type == 'image/jpeg' else 'png'
    profile.passport.save(f'{profile.id}-{timezone.now():%Y%m%d%H%M%S}.{extension}', upload, save=False)
    profile.passport_updated_at = timezone.now()
    profile.save(update_fields=['passport', 'passport_updated_at'])
    if old and old != profile.passport.name:
        profile.passport.storage.delete(old)
    logger.info('Passport photo for %s set by %s', profile.nactvet_reg_no,
                getattr(actor, 'username', 'the student'))
    return profile


def missing_details(profile):
    """What the records desk has still to take down. A VETA course student
    is asked for less: a phone, their gender and a next of kin."""
    if is_veta_only(profile):
        missing = []
        if not profile.phone:
            missing.append('phone')
        if not profile.gender:
            missing.append('gender')
        if not profile.next_of_kin.exists():
            missing.append('next of kin')
        if not profile.fee_payer:
            missing.append('who pays the fees')
        return missing
    missing = []
    if not profile.phone:
        missing.append('phone')
    if not profile.gender:
        missing.append('gender')
    if not profile.date_of_birth:
        missing.append('date of birth')
    if not profile.form_four_index:
        missing.append('Form Four index number')
    if profile.next_of_kin.count() < 2:
        missing.append('next of kin')
    if not profile.fee_payer:
        missing.append('who pays the fees')
    return missing


def compulsory_missing(profile):
    """What no student passes the records desk without (said 19 October 2026),
    continuing students included: a next of kin, and who pays the fees."""
    missing = []
    if not profile.next_of_kin.exists():
        missing.append('next of kin')
    if not profile.fee_payer:
        missing.append('who pays the fees')
    return missing


def returns_as_new(profile, *, today=None):
    """Why a student already on file is taken back as a new student — or
    None. Raises AdmissionError for one who may not be admitted (said
    19 October 2026):

      deceased, terminated            never admitted again
      suspended                       still the college's: back when it ends
      absconded (ABS)                 not before two years; then as new
      discontinued (disciplinary)     as new
      postponed more than two years   as new
      finished after Level 5          as new (for Level 6, in any later year)

    "As new": the first year's route and its one-time charges, on the same
    record and the same college ID.
    """
    from . import lifecycle
    standing = getattr(profile, 'standing', None)
    if standing is None:
        return None
    today = today or timezone.localdate()
    status = standing.status
    if status in StudentStanding.NEVER_ADMITTED:
        raise AdmissionError(f'{profile.name} is recorded as {standing.get_status_display().lower()} '
                             f'and cannot be admitted.')
    if status == StudentStanding.SUSPENDED:
        standing = lifecycle.lift_suspension_if_over(standing)
        if standing.status == StudentStanding.SUSPENDED:
            raise AdmissionError(f'{profile.name} is suspended until {standing.until}; they carry on '
                                 f'by themselves when it ends.')
        return None
    if status == StudentStanding.ABSCONDED:
        back = lifecycle.may_return_from(standing)
        if today < back:
            raise AdmissionError(f'{profile.name} absconded on {standing.since}: they may come back '
                                 f'from {back}, as a new student.')
        return 'absconded more than two years ago'
    if status == StudentStanding.DISCONTINUED:
        return 'discontinued by the Principal'
    if status == StudentStanding.POSTPONED and standing.since and \
            today > lifecycle.years_after(standing.since, lifecycle.AWAY_YEARS):
        return 'postponed more than two years ago'
    if status in (StudentStanding.COMPLETED, StudentStanding.ARCHIVED) and standing.class_level_id \
            and standing.class_level.order in lifecycle.EXIT_LEVELS:
        return 'finished after Level 5'
    return None


@transaction.atomic
def open_application(*, profile, semester, programme, class_level, kind,
                     returning_to=None, actor=None, note='', ignore_window=False):
    """Start one student's admission at the first desk of its route."""
    why_new = returns_as_new(profile)
    if why_new and kind != Application.NEW:
        # They come back as a new student — the first year's route and charges
        # — whatever kind the desk chose; the reason goes on the application.
        kind = Application.NEW
        note = f'Taken back as new: {why_new}. {note}'.strip()
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
    those modules only, and pay the per-module repeat rate — not a year's fees.

    A repeater who postponed is one too when they come back: postponing kept
    the modules they owe, and the college holds a student at their level until
    those are passed (said 12 October 2026). Before, their standing said only
    "postponed", and they came back billed a full year."""
    standing = StudentStanding.objects.filter(profile=profile).first()
    if standing is None:
        return False
    if standing.status == StudentStanding.REPEATING:
        return True
    return (standing.status == StudentStanding.POSTPONED
            and progression.open_repeats(profile).exists())


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
    elif review.confirmed == SemesterReview.PROVISIONAL:
        # Placed while the results were out (said 18 October 2026): finance
        # bills the level they were placed in, and nobody reads it as a pass.
        results = (f'{review.semester.label}: results not complete — placed provisionally; '
                   f'settled by the examination office once the grades are in.')
    elif not review.is_confirmed:
        results = (f'{review.semester.label}: not confirmed yet by the examination office '
                   f'({review.get_proposed_display().lower()}).')
    else:
        gpa = f', GPA {review.gpa}' if review.gpa is not None else ''
        results = f'{review.semester.label}: {review.get_confirmed_display()}{gpa}.'

    repeats = [module.code for module, is_repeat in planned if is_repeat]
    if repeat_only:
        billing = f'Repeat rate × {len(repeats)} module(s)'
        if ChargeType.objects.filter(for_repeaters=True, is_active=True).exists():
            billing += ", plus the repeaters' direct cost (once a year)"
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

    A repeater is billed the repeat rate for each module they will sit again,
    and the year's flat direct cost for repeaters, and nothing else. Admission
    finds those charges when it enrolls them and does not raise them a second
    time.
    """
    if is_repeat_only(application.profile):
        raised = finance.charge_repeater_costs(
            application.profile, application.semester.academic_year,
            class_level=application.class_level, programme=application.programme,
            semester=application.semester, actor=actor)
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
        fresh = False
        if application.kind == Application.NEW:
            try:
                fresh = returns_as_new(application.profile) is not None
            except AdmissionError:
                fresh = False
        created = finance.generate_charges(
            application.profile, year, actor=actor,
            class_level=application.class_level, programme=application.programme,
            entry=entry_of(application),
            readmitted=application.kind == Application.READMISSION, fresh=fresh,
            full_year=True)
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
    # What they paid before a postponement settles the new bill, and so does
    # money paid beyond an earlier bill (Level 5 and 6 tuition paid at once).
    finance.apply_credit(application.profile, actor=actor)
    finance.apply_on_account(application.profile, actor=actor)
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
    last review sent on is waiting at finance. It follows the same plan as
    the advance: a student whose review is not confirmed yet is placed
    provisionally (said 19 October 2026) and settled later from Provisional
    places; anybody already registered or applied for this year is left where
    they are. Safe to run again.
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
        if move.get('provisional'):
            review = SemesterReview.objects.filter(profile=profile, semester=closing).first()
            if review is not None and not review.is_confirmed:
                progression.confirm_review(review, SemesterReview.PROVISIONAL, actor=actor,
                                           reason=progression.PROVISIONAL_REASON.format(target=semester))
            summary['provisional'] = summary.get('provisional', 0) + 1
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
    if step == Application.RECORDS:
        missing = compulsory_missing(application.profile)
        if missing:
            raise AdmissionError('Records still needs: ' + ', '.join(missing) + '.')
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
    if following == Application.ADMISSION:
        sync_item_hold(application, actor=actor)
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

#: The desks a billed item may be marked at: finance checks them, and the
#: admission office records one the student brings back while held at its desk.
ITEM_DESKS = (Application.FINANCE, Application.ADMISSION)
#: A check-only item (a certificate, a signed form) is checked at the last
#: desk only, by the admission office, before it admits (said 19 October 2026:
#: records checks nothing; finance only the billed items).
CHECK_DESKS = (Application.ADMISSION,)
WAIVER_MARK = 'Item check: '
#: The reason on a hold the items put on, so that bringing them lifts it — and
#: only it: a hold the officer put on by hand stays until they lift it.
ITEM_HOLD = 'Missing: '


def item_desks(requirement):
    return CHECK_DESKS if requirement.kind == AdmissionRequirement.CHECK_ONLY else ITEM_DESKS


def items_holding(application):
    """The check-only items due on this application that are missing, and the
    ones nobody has checked yet: (missing, unchecked), by name."""
    due = [requirement for requirement in requirements_for(application)
           if requirement.kind == AdmissionRequirement.CHECK_ONLY]
    if not due:
        return [], []
    checks = {check.requirement_id: check.status for check in RequirementCheck.objects.filter(
        profile=application.profile, semester=application.semester, requirement__in=due)}
    missing = [r.name for r in due if checks.get(r.id) == RequirementCheck.MISSING]
    unchecked = [r.name for r in due if r.id not in checks and r.mandatory]
    return missing, unchecked


def sync_item_hold(application, *, actor=None):
    """Hold a student at the admission desk while a check-only item is missing,
    and lift that hold once every one is brought or waived."""
    missing, _ = items_holding(application)
    if missing and application.state == Application.ADMISSION:
        if not application.on_hold or application.hold_reason.startswith(ITEM_HOLD):
            hold(application, reason=ITEM_HOLD + ', '.join(missing), actor=actor)
    elif not missing and application.on_hold and application.hold_reason.startswith(ITEM_HOLD):
        release_hold(application, actor=actor)
    return application


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
    # VETA courses have their own items, checked on their course application.
    requirements = [r for r in requirements if r.applies_to != AdmissionRequirement.FOR_VETA]
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
        # Counted over the year: the count decides, not the frequency.
        if requirement.frequency == AdmissionRequirement.EVERY_YEAR and requirement.quantity <= 1 and any(
                check.semester.academic_year_id == semester.academic_year_id for check in before):
            continue
        if requirement.quantity > 1 and brought_this_year(
                requirement, [check for check in before
                              if check.semester.academic_year_id == semester.academic_year_id]) >= requirement.quantity:
            continue                  # all of them brought already: cleared for the year
        wanted.append(requirement)
    return wanted


def brought(check, requirement):
    """How many one check counts towards an item counted over the year. A
    check without a count (has it, or waived) counts as all of them."""
    if check.status == RequirementCheck.MISSING:
        return 0
    return check.count if check.count is not None else requirement.quantity


def brought_this_year(requirement, checks):
    return sum(brought(check, requirement) for check in checks if check.requirement_id == requirement.id)


def still_needed(requirement, profile, semester):
    """How many of a counted item are still to be brought this year, before
    this semester's check (rim paper: 2, or 1 after a semester with one)."""
    if requirement.quantity <= 1:
        return 1
    earlier = RequirementCheck.objects.filter(
        profile=profile, requirement=requirement,
        semester__academic_year=semester.academic_year).exclude(semester=semester)
    return max(1, requirement.quantity - brought_this_year(requirement, earlier))


def requirements_for(application):
    """What this student has to have for the semester they are applying for.
    An item for new students only (uniforms, the clinical coat) is asked only
    of a first year — at Level 4, 5 or 6."""
    due = items_due(application.profile, application.semester, application.class_level)
    if application.kind != Application.NEW:
        due = [requirement for requirement in due if not requirement.new_students_only]
    return due


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
                application=None, actor=None, note='', count=None, value=''):
    """Say whether the student has it, and let the bill follow.

    Missing: charged at the accountant's rate (or, for insurance, the medical
    fee on the year's bill stands). Has it, or waived by the college: nothing is
    owed for it — what is still unpaid on its charge is waived, what was paid
    stays paid.
    """
    if status not in dict(RequirementCheck.STATUS_CHOICES):
        raise AdmissionError(f'{status!r} is not a requirement outcome.')
    value = (value or '').strip()
    if requirement.value_label and status == RequirementCheck.HAS_IT and not value:
        raise AdmissionError(f'Type the {requirement.value_label}.')
    if requirement.quantity > 1 and status == RequirementCheck.HAS_IT:
        needed = still_needed(requirement, profile, semester)
        try:
            count = needed if count in (None, '') else int(count)
        except (TypeError, ValueError):
            raise AdmissionError(f'How many {requirement.name.lower()} did they bring?')
        if not 1 <= count <= needed:
            raise AdmissionError(f'{requirement.name}: between 1 and {needed} this semester.')
    else:
        count = None

    defaults = {'status': status, 'note': note[:300], 'checked_by': actor, 'count': count,
                'value': value[:120] if status == RequirementCheck.HAS_IT else ''}
    if application is not None:
        defaults['application'] = application
    check, _ = RequirementCheck.objects.update_or_create(
        profile=profile, semester=semester, requirement=requirement, defaults=defaults)
    if requirement.kind == AdmissionRequirement.CHECK_ONLY:
        return check                  # checked, never billed

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


def record_requirement(application, requirement, status, *, actor=None, note='', count=None, value=''):
    """Mark an item on an application — a billed one at the finance desk (or
    the admission desk when a held student brings it back), a check-only one at
    the admission desk before admitting. A check-only item missing holds the
    student."""
    check = record_item(application.profile, application.semester, requirement, status,
                        class_level=application.class_level, programme=application.programme,
                        application=application, actor=actor, note=note, count=count, value=value)
    if requirement.kind == AdmissionRequirement.CHECK_ONLY:
        sync_item_hold(application, actor=actor)
    return check


def typed_items(profile):
    """What was typed in for an item — the NACTVET admission code — the latest
    for each, kept on the student's details."""
    rows = {}
    for check in (RequirementCheck.objects.filter(profile=profile, status=RequirementCheck.HAS_IT)
                  .exclude(value='').select_related('requirement', 'semester')
                  .order_by('checked_at')):
        rows[check.requirement_id] = {'name': check.requirement.value_label or check.requirement.name,
                                      'value': check.value}
    return list(rows.values())


def items_owed(profile, semester):
    """What the student still owes for items this semester, for the admission
    desk to see before it admits them."""
    rows = []
    checks = (RequirementCheck.objects.filter(profile=profile, semester=semester,
                                              status=RequirementCheck.MISSING)
              .exclude(requirement__kind=AdmissionRequirement.CHECK_ONLY)   # money only
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
    # A certificate or a signed form not handed in holds the student; a billed
    # item never does (said 19 October 2026).
    missing, unchecked = items_holding(application)
    if missing:
        sync_item_hold(application, actor=actor)
        raise AdmissionError(f'On hold — missing: {", ".join(missing)}. Mark each "Has it" when it is '
                             f'brought (or "Waive"), then admit.')
    if unchecked:
        raise AdmissionError(f'Check first: {", ".join(unchecked)}.')

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

    # No password here: the ICT officer (or the Principal) gives every student
    # theirs at the password desk, and nobody else sees one (said 8 October
    # 2026). A returning student keeps the one they have.
    application.portal_pin = None

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

class ReturningError(AdmissionError):
    """A returning student's paper record that cannot be entered as given."""


#: What a student coming back from before the system can be.
RETURNING_KINDS = {
    'readmission': 'Readmission (discontinued last year)',
    'repeat': 'Repeating failed modules',
    'postponed': 'Back from a postponement',
}


@transaction.atomic
def record_returning_student(*, reg_no, name, programme, class_level, kind, return_semester,
                             modules=(), reference, actor=None, year=None, college_id=''):
    """Enter last year's position for a student the system has no results for,
    from the college's papers (NACTVET's results sheet, the postponement or
    discontinuation letter), so admission can take them back the usual way.

      readmission  → discontinued, due back this year into `return_semester`;
                     they sit that semester afresh as first sittings and are
                     billed as a new student (nothing of their old results is needed)
      repeat       → repeating: the `modules` owed (codes), held at the level;
                     billed 200,000 a module plus the yearly 280,000
      postponed    → postponed, due back into `return_semester`; billed as continuing —
                     or, with `modules` (they were repeating when they postponed),
                     as a repeater for those modules

    Nothing is guessed: a student whose results the system already holds is
    refused (their review decides), and `reference` — which paper it came
    from — is kept on every row this writes. `college_id`, the ID number the
    student already carries, is recorded when given — never over a different
    one, never one another student holds. Returns the standing.
    """
    from .models import AcademicYear, OutstandingRepeat, SemesterReview
    if kind not in RETURNING_KINDS:
        raise ReturningError('Readmission, repeat or postponed.')
    reference = (reference or '').strip()
    if not reference:
        raise ReturningError('Say which paper this comes from, e.g. "NACTVET results, semester 2 2024/25".')
    if return_semester not in (1, 2):
        raise ReturningError('The semester they come back to is 1 or 2.')
    year = year or AcademicYear.objects.filter(is_active=True).first()
    if year is None:
        raise ReturningError('No academic year is active.')
    reg_no = (reg_no or '').strip().upper()
    if not reg_no:
        raise ReturningError('A returning student is found by their registration number.')

    profile = StudentProfile.objects.filter(nactvet_reg_no__iexact=reg_no).first()
    if profile is None:
        if not (name or '').strip():
            raise ReturningError(f'{reg_no} is not on file: give their name too.')
        profile = capture_student(nactvet_reg_no=reg_no, name=name, actor=actor)
    if SemesterReview.objects.filter(profile=profile).exists():
        raise ReturningError(f'The system already holds {profile.name}\'s results; their semester '
                             f'review decides what they come back to.')
    if Application.objects.filter(profile=profile, semester__academic_year=year).exclude(
            state__in=[Application.REJECTED, Application.CANCELLED]).exists():
        raise ReturningError(f'{profile.name} already has an application this year.')
    college_id = (college_id or '').strip()
    if college_id:
        if profile.college_id and profile.college_id.upper() != college_id.upper():
            raise ReturningError(f'{profile.name} already has college ID {profile.college_id}; '
                                 f'it is not changed here.')
        holder = StudentProfile.objects.filter(college_id__iexact=college_id).exclude(pk=profile.pk).first()
        if holder is not None:
            raise ReturningError(f'College ID {college_id} already belongs to {holder.nactvet_reg_no}.')
        if not profile.college_id:
            profile.college_id = college_id[:40]
            profile.save(update_fields=['college_id'])

    status = {'readmission': StudentStanding.FAILED, 'repeat': StudentStanding.REPEATING,
              'postponed': StudentStanding.POSTPONED}[kind]
    reason = f'From the paper record: {reference}'
    owed = []
    codes = sorted({str(code).strip().upper() for code in modules if str(code).strip()})
    if kind == 'readmission' and codes:
        raise ReturningError('A readmission sits the semester afresh: leave the modules empty.')
    if kind == 'repeat' and not codes:
        raise ReturningError('List the module codes they repeat.')
    if codes:
        offered = {module.code.upper(): module for module in Module.objects.filter(
            semester__academic_year=year, semester__number=return_semester,
            class_level=class_level, programme__in=[programme, None])}
        unknown = [code for code in codes if code not in offered]
        if unknown:
            raise ReturningError(f'Not offered at {class_level} in semester {return_semester} this '
                                 f'year: {", ".join(unknown)}. Check the codes, or add the modules first.')
        previous = (AcademicYear.objects.filter(name__lt=year.name).order_by('-name').first())
        origin = (Semester.objects.filter(academic_year=previous, number=return_semester).first()
                  if previous else None)
        if origin is None:
            raise ReturningError('Last year and its semesters must exist (Academic Years) before a '
                                 'repeat from it can be entered.')
        for code in codes:
            row, _ = OutstandingRepeat.objects.update_or_create(
                profile=profile, module_code=code, origin_semester=origin,
                defaults={'module_name': offered[code].name, 'class_level': class_level,
                          'semester_number': return_semester, 'status': OutstandingRepeat.OPEN,
                          'note': reason[:300]})
            owed.append(row)

    standing = progression.set_standing(
        profile, status, actor=actor, reason=reason, class_level=class_level, programme=programme,
        return_year=year, return_semester_number=return_semester, note=reason)
    logger.info('Recorded %s as %s from paper (%s) by %s', profile.nactvet_reg_no, kind, reference,
                getattr(actor, 'username', None))
    return standing


def due_back(semester):
    """Who the college should be seeing at this semester's admissions.

    The discontinued and postponed students the year end recorded, matched to
    the semester they said they would come back to, and without an application
    already open.
    """
    # Repeaters with a return semester too: the year end registers its own
    # repeaters itself, so the ones left here are those entered from last
    # year's papers (record_returning_student).
    standings = (StudentStanding.objects
                 .filter(Q(status__in=sorted(StudentStanding.AWAY))
                         | Q(status=StudentStanding.REPEATING, return_semester_number__isnull=False))
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


# ── continuing students who never reported ────────────────────────────────────

#: How long the admission office waits before deciding about a continuing
#: student who has not come (said 18 October 2026): three months from the day
#: admission opened.
NOT_REPORTED_AFTER_MONTHS = 3


def decide_from(semester):
    """The day the admission office decides about this semester's no-shows."""
    window = window_for(semester)
    if window is None:
        return None
    opened = window.opens_on
    month = opened.month - 1 + NOT_REPORTED_AFTER_MONTHS
    year, month = opened.year + month // 12, month % 12 + 1
    import calendar
    day = min(opened.day, calendar.monthrange(year, month)[1])
    return opened.replace(year=year, month=month, day=day)


def not_reported(semester, *, today=None):
    """Continuing students handed to admissions for this semester who have not
    come through the desks."""
    from django.utils import timezone
    today = today or timezone.localdate()
    due = decide_from(semester)
    rows = []
    for application in (Application.objects.filter(semester=semester, kind=Application.CONTINUING,
                                                   state__in=sorted(Application.OPEN_STATES))
                        .select_related('profile', 'class_level', 'programme').order_by('profile__name')):
        last_check = application.steps.filter(note__startswith=STILL_EXPECTED).order_by('-done_at').first()
        rows.append({
            'application_id': application.id, 'reg_no': application.profile.nactvet_reg_no,
            'name': application.profile.name, 'level': application.class_level.name,
            'programme': application.programme.code if application.programme_id else '',
            'desk': application.get_state_display(), 'opened': application.created_at.date(),
            'decide_from': due, 'can_decide': bool(due and today >= due),
            'still_expected_on': last_check.done_at.date() if last_check else None,
        })
    return rows


STILL_EXPECTED = 'Still expected'


@transaction.atomic
def mark_not_reported(application, *, decision, actor=None, note='', today=None):
    """The admission office's decision, three months after admission opened:
    still expected (left open, the check recorded), or did not report — the
    application cancelled, its fees reversed, and the student due back in a
    later year at the level they were going to."""
    from django.utils import timezone
    today = today or timezone.localdate()
    application = Application.objects.select_for_update().select_related(
        'profile', 'semester__academic_year', 'class_level', 'programme').get(pk=application.pk)
    if application.kind != Application.CONTINUING or application.state not in Application.OPEN_STATES:
        raise AdmissionError('Only a continuing student still at the desks can be decided here.')
    due = decide_from(application.semester)
    if due is None or today < due:
        raise AdmissionError(f'Decide from {due:%d %b %Y}: three months after admission opened.'
                             if due else 'The admission window for this semester is not set.')
    note = (note or '').strip()
    if decision == 'expected':
        ApplicationStep.objects.create(application=application, step=application.state, done_by=actor,
                                       note=f'{STILL_EXPECTED}{": " + note if note else ""}'[:300])
        return application
    if decision != 'not_reported':
        raise AdmissionError('Still expected, or did not report.')
    reason = f'Did not report for {application.semester}{": " + note if note else ""}'[:300]
    ApplicationStep.objects.create(application=application, step=application.state, done_by=actor, note=reason)
    application.state = Application.CANCELLED
    application.decided_reason = reason
    application.save(update_fields=['state', 'decided_reason', 'updated_at'])
    finance.reset_fees(application.profile, application.semester.academic_year,
                       from_semester_number=application.semester.number, actor=actor, reason=reason)
    from . import progression
    nxt = AcademicYear.objects.filter(name=application.semester.academic_year.next_name).first()
    if nxt is None:
        nxt, _ = AcademicYear.objects.get_or_create(name=application.semester.academic_year.next_name)
    progression.set_standing(application.profile, StudentStanding.NOT_REPORTED, actor=actor,
                             semester=application.semester, class_level=application.class_level,
                             programme=application.programme, return_year=nxt,
                             return_semester_number=application.semester.number, reason=reason)
    return application
