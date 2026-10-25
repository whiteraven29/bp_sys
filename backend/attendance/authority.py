"""NACTVET's grades into the system: the upload's one rule, and the import of
earlier years' results the system never held (said 24 October 2026).

Earlier results come in three sheets per class, as NACTVET issues them: the
first sitting (exam numbers …TES…), the supplementary (…RSM…), and the repeat
sitting (…MTS2…), which NACTVET files under the year the student first sat
but which was sat in a later semester. A repeat is entered in the semester it
was sat and linked to the year it clears, so it shows under that year — the
same as every repeat the system raises itself.
"""

from django.db import transaction
from django.db.models import F

from . import progression
from .grading import NOT_SAT_MARK, parse_authority_grade, parse_authority_supp_grade, result_outcome
from django.utils import timezone

from .models import (
    AcademicYear, Module, Notification, Programme, ResultCorrection, Semester, SemesterReview, Student,
    StudentProfile, StudentResult,
)


class EarlierResultsError(ValueError):
    pass


def save_rows(semester, rows, sitting):
    """NACTVET's grades onto this semester's enrollments: the upload's one
    rule, used by Authority Grades and the earlier-results import.

    Only the semester named is touched: a module code repeats every year, and
    a repeating student has an enrollment for each attempt, so matching on the
    code alone could write a grade onto an old attempt.

    First sitting: a grade already there is never replaced by a different one
    (said 24 October 2026) — clear the sheet, or correct the one grade. `NS`
    (not sat) only on a repeat sitting. Supplementary: only the modules
    waiting for a supplementary are taken, into their own field; a module that
    was not a supplementary must carry the same grade as before.
    """
    from .serializers import StudentResultSerializer
    serializer = StudentResultSerializer()
    progression.link_repeat_origins(semester)
    saved, unchanged, carried, errors, not_sitting = 0, 0, 0, [], []
    # Who sits anything this semester, and who sits a repeat: a repeat sheet
    # re-lists every module, the ones not repeated too — those are carried.
    here = Student.objects.filter(module__semester=semester)
    sitting_here = {reg.upper() for reg in here.values_list('nactvet_reg_no', flat=True)}
    repeating_here = {reg.upper() for reg in here.filter(attempt=Student.REPEAT)
                      .values_list('nactvet_reg_no', flat=True)}
    with transaction.atomic():
        for row in rows:
            reg_no = str(row.get('reg_no', '')).strip()
            module_code = str(row.get('module_code', '')).strip()
            raw = str(row.get('grade', '')).strip().upper()
            if not reg_no or not module_code or not raw:
                continue
            student = (Student.objects
                       .filter(nactvet_reg_no__iexact=reg_no, module__code__iexact=module_code,
                               module__semester=semester)
                       .select_related('module__class_level')
                       .order_by(F('withdrawn_at').asc(nulls_first=True), '-id').first())
            if student is None:
                if raw == 'NA':
                    continue                      # not applicable: nothing to record
                if reg_no.upper() in repeating_here:
                    carried += 1                  # a repeater's module not repeated: last time's grade
                elif reg_no.upper() in sitting_here:
                    not_sitting.append(f'{reg_no} / {module_code}')
                else:
                    errors.append(f'{reg_no} / {module_code}: not enrolled in {semester.label}')
                continue
            if raw == 'NA':
                # Not applicable to them: nothing recorded, as for a blank cell —
                # the module stays without a result (INCOMPLETE), never quietly left
                # out of their semester (said 25 October 2026).
                continue
            if raw == NOT_SAT_MARK and (sitting != 'first' or student.attempt != Student.REPEAT):
                errors.append(f'{reg_no} / {module_code}: NS (not sat) is only for a repeat sitting')
                continue
            try:
                parse = parse_authority_supp_grade if sitting == 'supp' else parse_authority_grade
                parsed = parse(raw, student.module.class_level)
            except ValueError as exc:
                errors.append(f'{reg_no} / {module_code}: {exc}')
                continue
            if sitting == 'supp':
                result = getattr(student, 'result', None)
                if result is None or not result.authority_grade:
                    errors.append(f'{reg_no} / {module_code}: no first-sitting grade to follow')
                    continue
                first = parse_authority_grade(result.authority_grade, student.module.class_level)
                if first['status'] != 'SUPP':
                    # Re-listed, not re-sat: it must read as it did.
                    if raw == result.authority_grade.upper():
                        unchanged += 1
                    else:
                        errors.append(f'{reg_no} / {module_code}: {raw} on the supplementary '
                                      f'sheet, but {result.authority_grade} was not a supplementary')
                    continue
                if result.authority_supp_grade and result.authority_supp_grade.upper() != parsed['raw']:
                    errors.append(f'{reg_no} / {module_code}: already {result.authority_supp_grade} at the '
                                  f'supplementary — clear the sheet or correct this grade first')
                    continue
                if result.authority_supp_grade:
                    unchanged += 1
                    continue
                result.authority_supp_grade = parsed['raw']
                result.authority_status = result_outcome(result, serializer)['status']
                result.final_approved = True
                result.save(update_fields=['authority_supp_grade', 'authority_status',
                                           'final_approved', 'updated_at'])
                saved += 1
                continue
            result, _ = StudentResult.objects.get_or_create(student=student)
            if result.authority_grade and result.authority_grade.upper() != parsed['raw']:
                errors.append(f'{reg_no} / {module_code}: already {result.authority_grade} — clear the sheet '
                              f'or correct this grade first')
                continue
            if result.authority_grade:
                unchanged += 1
                continue
            result.authority_grade = parsed['raw']
            result.authority_status = parsed['status']
            result.final_approved = True
            result.save(update_fields=['authority_grade', 'authority_status', 'final_approved', 'updated_at'])
            saved += 1
    return {'saved': saved, 'unchanged': unchanged, 'carried': carried, 'errors': errors,
            'not_sitting': not_sitting}


# ── earlier years' results ────────────────────────────────────────────────────

def _past_semester(year_name, number):
    """The semester of an earlier year, made (inactive) if the system never
    had it. Only years before the one running now: this year's results come
    through the ordinary upload."""
    year_name = (year_name or '').strip()
    active = AcademicYear.objects.filter(is_active=True).first()
    if not year_name or number not in (1, 2):
        raise EarlierResultsError('Give the year on the sheet (e.g. 2024/2025) and the semester (1 or 2).')
    if active is not None and year_name >= active.name:
        raise EarlierResultsError(f'{year_name} is not an earlier year: upload it under Authority Grades.')
    year, _ = AcademicYear.objects.get_or_create(name=year_name, defaults={'is_active': False})
    semester, _ = Semester.objects.get_or_create(academic_year=year, number=number, defaults={'is_active': False})
    return semester


def _programme_for(level):
    offering = list(Programme.objects.filter(levels=level).order_by('code'))
    return next((p for p in offering if p.code.upper() == 'PST'), offering[0] if len(offering) == 1 else None)


def _modules(semester, level, modules):
    """This semester's modules at the level, those the sheet names made if missing."""
    found = {m.code.upper(): m for m in Module.objects.filter(semester=semester, class_level=level)}
    programme = _programme_for(level)
    for row in modules or []:
        code = str(row.get('code') or '').strip().upper()
        if not code or code in found:
            continue
        try:
            credits = int(float(row.get('credits')))
        except (TypeError, ValueError):
            raise EarlierResultsError(f'{code} is new to {semester.label}: give its credits on the Modules sheet.')
        found[code] = Module.objects.create(code=code, name=str(row.get('name') or code)[:200], teacher='',
                                            class_level=level, semester=semester, programme=programme,
                                            credits=credits)
    return found


def _final_grade(enrollment):
    result = getattr(enrollment, 'result', None)
    if result is None:
        return ''
    return (result.authority_supp_grade or result.authority_grade or '').upper()


@transaction.atomic
def import_first_sitting(year_name, number, level, modules, rows, *, actor=None):
    """An earlier year's first sitting: its modules, a record of each student
    sitting them (no password, no bill, no class list) and their grades."""
    semester = _past_semester(year_name, number)
    by_code = _modules(semester, level, modules)
    names = {str(r.get('reg_no') or '').strip().upper(): str(r.get('name') or '').strip() for r in rows}
    made, added, unknown = 0, 0, []
    for reg, name in names.items():
        if not reg:
            continue
        profile = StudentProfile.objects.filter(nactvet_reg_no__iexact=reg).first()
        if profile is None:
            if not name:
                unknown.append(reg); continue
            from .admissions import capture_student
            profile = capture_student(nactvet_reg_no=reg, name=name, actor=actor)
            made += 1
        codes = {str(r.get('module_code') or '').strip().upper() for r in rows
                 if str(r.get('reg_no') or '').strip().upper() == reg
                 and str(r.get('grade') or '').strip().upper() not in ('', 'NA')}
        for code in sorted(codes):
            module = by_code.get(code)
            if module is None:
                raise EarlierResultsError(f'{code} is not a module of {level.name} in {semester.label}: '
                                          'add it on the Modules sheet with its credits.')
            if not Student.objects.filter(nactvet_reg_no__iexact=reg, module=module).exists():
                Student.objects.create(nactvet_reg_no=profile.nactvet_reg_no, name=profile.name, profile=profile,
                                       module=module, attempt=Student.NORMAL)
                added += 1
    if unknown:
        raise EarlierResultsError(f'Not on file, and no name on the sheet: {", ".join(unknown[:10])}.')
    saved = save_rows(semester, rows, 'first')
    return {**saved, 'semester': semester.label, 'students_recorded': made, 'sittings_added': added}


def import_supplementary(year_name, number, level, rows, *, actor=None):
    semester = _past_semester(year_name, number)
    return {**save_rows(semester, rows, 'supp'), 'semester': semester.label}


@transaction.atomic
def import_repeat_sitting(year_name, number, level, sat_in, rows, *, actor=None):
    """NACTVET's repeat sheet (MTS2), filed under the year it clears, sat in
    `sat_in`. For each student the modules still owed after that year are read
    from the record; each becomes a repeat sitting in `sat_in`, linked to the
    first sitting it repeats, and takes its grade from the sheet — `NS` where
    the student did not sit it. The sheet's other modules are last time's
    grades: checked against the record, never written."""
    origin = Semester.objects.filter(academic_year__name=(year_name or '').strip(), number=number).first()
    if origin is None:
        raise EarlierResultsError(f'No {year_name} semester {number} in the system: import its first sitting first.')
    later = (sat_in.academic_year.name, sat_in.number) > (origin.academic_year.name, origin.number)
    if not later:
        raise EarlierResultsError(f'The repeat was sat after {origin.label}: choose the semester it was sat in.')
    sat_modules = {m.code.upper(): m for m in Module.objects.filter(semester=sat_in, class_level=level)}
    serializer_rows, warnings, created, linked, converted, withdrawn, owing = [], [], 0, 0, 0, [], {}
    by_reg = {}
    for row in rows:
        reg = str(row.get('reg_no') or '').strip().upper()
        if reg:
            by_reg.setdefault(reg, []).append(row)
    from .serializers import StudentResultSerializer
    serializer = StudentResultSerializer()
    for reg, sheet in by_reg.items():
        first = list(Student.objects.filter(nactvet_reg_no__iexact=reg, module__semester=origin,
                                            module__class_level=level, attempt=Student.NORMAL)
                     .select_related('module__class_level', 'result', 'profile'))
        if not first:
            warnings.append(f'{reg}: no {origin.label} results on record — import its first sitting first')
            continue
        decision = progression.decide([progression.module_row(e, serializer) for e in first])
        owed = {code.upper() for code in decision['failed']}
        owing[reg] = sorted(owed)
        record = {e.module.code.upper(): e for e in first}
        for row in sheet:
            code = str(row.get('module_code') or '').strip().upper()
            grade = str(row.get('grade') or '').strip().upper()
            if not grade or grade == 'NA':
                continue
            if code not in owed:
                if code in record and grade != _final_grade(record[code]):
                    warnings.append(f'{reg} / {code}: {grade} on the sheet, {_final_grade(record[code]) or "nothing"} '
                                    f'on record — not a module owed, not written')
                continue
            module = sat_modules.get(code)
            if module is None:
                warnings.append(f'{reg} / {code}: not a module of {level.name} in {sat_in.label} — add it first')
                continue
            sitting = Student.objects.filter(nactvet_reg_no__iexact=reg, module=module).first()
            if sitting is None:
                origin_row = record[code]
                Student.objects.create(nactvet_reg_no=origin_row.nactvet_reg_no, name=origin_row.name,
                                       profile=origin_row.profile, module=module, attempt=Student.REPEAT,
                                       repeat_of=origin_row)
                created += 1
            elif sitting.attempt != Student.REPEAT:
                # Enrolled with the class as a first sitting, but NACTVET's repeat
                # sheet says it is the repeat of a module owed (said 25 October
                # 2026): recorded as the repeat it is, linked to the year it
                # clears — only while nothing is graded on it.
                if _final_grade(sitting) or getattr(getattr(sitting, 'result', None), 'final_approved', False):
                    warnings.append(f'{reg} / {code}: already graded in {sat_in.label} as a first sitting — not '
                                    'written; correct it under Corrections if it was the repeat')
                    continue
                Student.objects.filter(pk=sitting.pk).update(attempt=Student.REPEAT, repeat_of=record[code])
                converted += 1
            elif sitting.repeat_of_id is None:
                Student.objects.filter(pk=sitting.pk).update(repeat_of=record[code])
                linked += 1
            serializer_rows.append({'reg_no': reg, 'module_code': code, 'grade': grade})
        # A module already passed in that year, enrolled again with the class as a
        # first sitting: NACTVET's repeat sheet carries its old grade — not sat
        # now. Taken off (withdrawn, never deleted), while nothing is graded on it.
        for extra in (Student.objects.filter(nactvet_reg_no__iexact=reg, module__semester=sat_in,
                                             module__class_level=level, attempt=Student.NORMAL,
                                             withdrawn_at__isnull=True)
                      .select_related('module', 'result')):
            code = extra.module.code.upper()
            if code in owed or code not in record:
                continue
            if _final_grade(extra) or getattr(getattr(extra, 'result', None), 'final_approved', False):
                warnings.append(f'{reg} / {code}: passed in {origin.label}, but graded again in {sat_in.label} — '
                                'left as it is')
                continue
            Student.objects.filter(pk=extra.pk).update(
                withdrawn_at=timezone.now(),
                withdrawn_reason=(f'Passed in {origin.label} ({_final_grade(record[code])}); NACTVET\'s repeat sheet: '
                                  f'not sat again')[:300])
            withdrawn.append(f'{reg} / {code}')
    saved = save_rows(sat_in, serializer_rows, 'first')
    return {**saved, 'semester': sat_in.label, 'clears': origin.label, 'repeats_added': created,
            'repeats_linked': linked, 'repeats_converted': converted, 'withdrawn': withdrawn,
            'warnings': warnings, 'owed': owing}


# ── the emergency correction ──────────────────────────────────────────────────

class CorrectionError(ValueError):
    pass


def _current(result, field):
    if result is None:
        return ''
    return (result.authority_supp_grade if field == ResultCorrection.SUPP else result.authority_grade).upper()


@transaction.atomic
def correct_grade(enrollment, field, new_value, reason, reference='', *, actor=None, remove=False):
    """The examination officer corrects one grade — NACTVET's re-issued grade,
    NS for a repeat not sat, or `remove`: a grade written against a module the
    student did not sit, which then has no result and reads INCOMPLETE.

    Results are the examination officer's (said 25 October 2026): the
    correction applies at once; DPARC and the Principal are told, not asked.
    The grade it replaces is kept on the correction. A student already
    confirmed on these results whose results now say otherwise has their
    review's proposal brought up to date, for the examination officer to
    confirm again as the results say. Returns (correction, warning)."""
    from . import notifications
    if field not in (ResultCorrection.FIRST, ResultCorrection.SUPP):
        raise CorrectionError('Correct the first sitting or the supplementary.')
    new = '' if remove else (new_value or '').strip().upper()
    reason = (reason or '').strip()
    level = enrollment.module.class_level
    result = getattr(enrollment, 'result', None)
    current = _current(result, field)
    if remove:
        if not current:
            raise CorrectionError('There is no grade there to remove.')
        if field == ResultCorrection.FIRST and result.authority_supp_grade:
            raise CorrectionError('A supplementary grade sits on top: remove that first.')
    try:
        if remove:
            pass
        elif field == ResultCorrection.SUPP:
            first = parse_authority_grade(result.authority_grade, level) if result and result.authority_grade else None
            if first is None or first['status'] != 'SUPP':
                raise CorrectionError('Only a module that went to the supplementary has a supplementary grade.')
            parse_authority_supp_grade(new, level)
        elif new == NOT_SAT_MARK and enrollment.attempt != Student.REPEAT:
            raise CorrectionError('NS (not sat) is only for a repeat sitting.')
        else:
            parse_authority_grade(new, level)
    except ValueError as exc:
        raise CorrectionError(str(exc))
    if new == current and not remove:
        raise CorrectionError(f'{new} is the grade already on record.')
    if not reason:
        raise CorrectionError('Say why — what NACTVET issued, and where.')
    if result is None:
        result = StudentResult.objects.create(student=enrollment)
    who = actor if getattr(actor, 'is_authenticated', False) else None
    made = ResultCorrection.objects.create(result=result, field=field, old_value=current, new_value=new,
                                           reason=reason[:300], reference=(reference or '').strip()[:120],
                                           status=ResultCorrection.APPROVED, proposed_by=who,
                                           decided_by=who, decided_at=timezone.now())
    from .serializers import StudentResultSerializer
    if field == ResultCorrection.SUPP:
        result.authority_supp_grade = new
    else:
        result.authority_grade = new
    if field == ResultCorrection.FIRST and not new:
        # Removed: no result for this module any more — it reads INCOMPLETE.
        result.final_approved, result.authority_status = False, ''
    else:
        result.final_approved = True
        result.authority_status = result_outcome(result, StudentResultSerializer())['status']
    result.save(update_fields=['authority_grade', 'authority_supp_grade', 'authority_status', 'final_approved',
                               'updated_at'])
    semester = enrollment.module.semester
    progression.refresh_provisional(progression.provisional_reviews().filter(semester=semester))
    change = (f'{enrollment.module.code} ({semester.label}): {current or "nothing"} → {new or "no result"}. '
              f'{reason}' + (f' Ref. {made.reference}.' if made.reference else ''))
    warning = ''
    # The review keeps its own copy of the GPA and proposal: brought up to date
    # whatever its state, so Semester Review never shows the grade before the
    # correction (everything else works the GPA out from the grades each time).
    review = (SemesterReview.objects.filter(profile=enrollment.profile, semester=semester).first()
              if enrollment.profile_id else None)
    if review is not None:
        fresh = progression.evaluate(enrollment.profile, semester)
        review.modules, review.gpa = fresh['modules'], fresh['gpa']
        review.proposed, review.proposed_reason = fresh['proposed'], fresh['reason'][:300]
        review.save(update_fields=['modules', 'gpa', 'proposed', 'proposed_reason', 'updated_at'])
        if review.confirmed not in ('', SemesterReview.PROVISIONAL) and fresh['proposed'] != review.confirmed:
            warning = (f'{enrollment.name} was confirmed as "{review.get_confirmed_display()}" on {semester.label}; '
                       f'the corrected results now say "{review.get_proposed_display()}". Confirm it again in '
                       'Semester Review ▸ Show: Results now differ from the decision.')
    notifications.notify(notifications.principal_and_dparc(), kind=Notification.CORRECTION_DECIDED,
                         title=f'Result corrected: {enrollment.name}', body=change + (f' {warning}' if warning else ''),
                         link='results:corrections')
    return made, warning


