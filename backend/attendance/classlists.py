"""A level's class list for a semester, and the CAT exam numbers given from it
(said 12 October 2026).

The class list is who the level has this semester: everybody registered at the
level (or, before registrations were kept, enrolled in its modules), each a
first-sitting student or a repeater, with a repeater's repeat modules. The
admission office downloads it; once the admission window has closed it is the
class the semester runs with — before that it is marked provisional.

CAT exam numbers are given by the examination officer to the same list, for
CAT 1 and CAT 2, in the format they set (CatNumberFormat). A number once given
is kept; generating again numbers only the students who have none.
"""

import re

from django.db import transaction
from django.db.models import Max, Q

from . import finance
from .models import (
    AdmissionWindow, CatExamNumber, CatNumberFormat, Programme, SemesterRegistration, Student,
)

FIRST_SITTING = 'First sitting'
REPEATER = 'Repeater'


def class_list(semester, class_level, *, programme=None):
    """The level's students this semester, alphabetically by name."""
    registrations = (SemesterRegistration.objects.filter(semester=semester, class_level=class_level)
                     .exclude(status=SemesterRegistration.CANCELLED)
                     .select_related('profile', 'programme'))
    if programme is not None:
        registrations = registrations.filter(programme=programme)
    people = {reg.profile_id: {'profile': reg.profile, 'programme': reg.programme,
                               'repeating': reg.kind == SemesterRegistration.REPEATING}
              for reg in registrations}

    # Students from before registrations were kept are known by their enrollments.
    # Read only: a person with no profile yet is keyed by registration number
    # and gets a profile when something (a CAT number) has to hang off it.
    registered = SemesterRegistration.objects.filter(semester=semester).values('profile_id')
    legacy = (Student.objects.studying().filter(module__semester=semester, module__class_level=class_level)
              .exclude(profile_id__in=registered).select_related('profile', 'module__programme'))
    if programme is not None:
        legacy = legacy.filter(module__programme=programme)
    for enrollment in legacy:
        if enrollment.profile_id:
            key, name, reg_no = enrollment.profile_id, enrollment.profile.name, enrollment.profile.nactvet_reg_no
        else:
            key = enrollment.nactvet_reg_no.strip().upper()
            name, reg_no = enrollment.name, key
        people.setdefault(key, {'profile': enrollment.profile, 'name': name, 'reg_no': reg_no,
                                'programme': enrollment.module.programme, 'repeating': False,
                                'enrollment': enrollment})

    repeats = {}
    for enrollment in (Student.objects.studying().filter(module__semester=semester, attempt=Student.REPEAT)
                       .filter(Q(profile_id__in=[k for k in people if isinstance(k, int)])
                               | Q(profile__isnull=True,
                                   nactvet_reg_no__in=[k for k in people if isinstance(k, str)]))
                       .select_related('module')):
        key = enrollment.profile_id or enrollment.nactvet_reg_no.strip().upper()
        repeats.setdefault(key, []).append(enrollment.module.code)

    from .progression import provisional_profile_ids
    provisional = provisional_profile_ids()
    rows = []
    for key, person in people.items():
        profile = person['profile']
        repeated = sorted(set(repeats.get(key, [])))
        rows.append({
            'profile_id': profile.id if profile else None,
            'name': profile.name if profile else person['name'],
            'reg_no': profile.nactvet_reg_no if profile else person['reg_no'],
            'college_id': (profile.college_id or '') if profile else '',
            'gender': profile.get_gender_display() if profile and profile.gender else '',
            'programme': person['programme'].code if person['programme'] else '',
            'status': REPEATER if (person['repeating'] or repeated) else FIRST_SITTING,
            'modules_repeated': repeated,
            'provisional': bool(profile) and profile.id in provisional,
            '_enrollment': person.get('enrollment'),
        })
    return sorted(rows, key=lambda row: (row['name'].upper(), row['reg_no']))


def public(rows):
    """Rows as sent out: without the internal enrollment kept for numbering."""
    return [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows]


def class_list_is_final(semester):
    """Whether the admission window for this semester has closed: until it
    has, students are still being admitted and the list may grow."""
    from django.utils import timezone
    window = AdmissionWindow.objects.filter(semester=semester).first()
    return window is None or not window.is_active or window.closes_on < timezone.localdate()


# ── CAT exam numbers ──────────────────────────────────────────────────────────

class CatNumberError(ValueError):
    """A CAT number request the rules do not allow; the message says why."""


def cat_format_for(academic_year):
    """The year's format, carried from last year's if not set up yet."""
    existing = CatNumberFormat.objects.filter(academic_year=academic_year).first()
    if existing is not None:
        return existing
    previous = (CatNumberFormat.objects.filter(academic_year__name__lt=academic_year.name)
                .order_by('-academic_year__name').first())
    return CatNumberFormat.objects.create(
        academic_year=academic_year,
        pattern=previous.pattern if previous else CatNumberFormat._meta.get_field('pattern').default,
        college_code=previous.college_code if previous else CatNumberFormat._meta.get_field('college_code').default,
        sequence_width=previous.sequence_width if previous else 0)


def set_cat_format(academic_year, *, pattern, college_code, sequence_width=0, actor=None):
    pattern = (pattern or '').strip()
    if '{SEQ}' not in pattern:
        raise CatNumberError('The format needs {SEQ}, the running number, or every student gets the same number.')
    unknown = [part for part in re.findall(r'\{[A-Z]+\}', pattern)
               if part not in CatNumberFormat.PLACEHOLDERS]
    if unknown:
        raise CatNumberError(f'Unknown piece(s): {", ".join(unknown)}. '
                             f'Use {" ".join(CatNumberFormat.PLACEHOLDERS)}.')
    fmt = cat_format_for(academic_year)
    fmt.pattern = pattern[:120]
    fmt.college_code = (college_code or '').strip()[:20]
    fmt.sequence_width = max(0, min(int(sequence_width or 0), 6))
    fmt.updated_by = actor
    fmt.save()
    return fmt


@transaction.atomic
def generate_cat_numbers(semester, class_level, cat, *, order='name', actor=None):
    """Number every student on the level's class list who has no number for
    this CAT yet, continuing from the last number given. Returns how many were
    numbered now."""
    if cat not in (CatExamNumber.CAT1, CatExamNumber.CAT2):
        raise CatNumberError('CAT 1 or CAT 2.')
    rows = class_list(semester, class_level)
    if not rows:
        raise CatNumberError(f'Nobody is on the {class_level} class list for {semester} yet.')
    if order == 'reg_no':
        rows = sorted(rows, key=lambda row: row['reg_no'])
    fmt = cat_format_for(semester.academic_year)
    have = set(CatExamNumber.objects.filter(semester=semester, cat=cat)
               .values_list('profile_id', flat=True))
    taken = (CatExamNumber.objects.filter(semester=semester, cat=cat, class_level=class_level)
             .aggregate(top=Max('sequence'))['top'] or 0)
    made = 0
    programmes = {p.code: p for p in Programme.objects.all()}
    for row in rows:
        if row['profile_id'] is None:
            row['profile_id'] = finance.profile_for_student(row['_enrollment']).id
        if row['profile_id'] in have:
            continue
        taken += 1
        number = fmt.render(sequence=taken, level=class_level, semester=semester, cat=cat,
                            programme=programmes.get(row['programme']))
        if CatExamNumber.objects.filter(semester=semester, cat=cat, number=number).exists():
            raise CatNumberError(f'{number} is already given: the format must make each level\'s '
                                 f'numbers different (put {{LEVEL}} in it).')
        CatExamNumber.objects.create(profile_id=row['profile_id'], semester=semester, cat=cat,
                                     class_level=class_level, sequence=taken, number=number,
                                     created_by=actor)
        made += 1
    finance.audit('cat_numbers.generate', 'Semester', actor=actor, entity_id=semester.id,
                  summary=f'CAT {cat} numbers for {class_level} {semester}: {made} new')
    return made


def cat_number_rows(semester, class_level, cat):
    """The class list with each student's number for this CAT, in number
    order; students not numbered yet come last, by name."""
    numbers = {n.profile_id: n for n in
               CatExamNumber.objects.filter(semester=semester, cat=cat, class_level=class_level)}
    rows = []
    for row in public(class_list(semester, class_level)):
        given = numbers.get(row['profile_id'])
        rows.append({**row, 'cat_number': given.number if given else '',
                     'sequence': given.sequence if given else None})
    return sorted(rows, key=lambda row: (row['sequence'] is None, row['sequence'] or 0, row['name'].upper()))
