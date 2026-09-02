"""Performance summaries and trends.

The college could always see one student's marks, and one module's marks, and
nothing above that. Whether NTA 5 did better this semester than last, whether a
module fails half its class every year, whether CAT 2 is always worse than CAT 1
— none of those questions could be asked, so none of them were.

Everything here reads; nothing here writes. A summary is a way of watching how
students are doing, which is not the same power as changing a mark.
"""

from .grading import grade_for_mark
from .models import AcademicYear, Module, Semester, Student, StudentResult
from .serializers import StudentResultSerializer

#: The assessments a summary can be taken over. Every one is normalised to a
#: percentage so a CAT out of 100, a CA out of 40 and a final out of 100 can be
#: put on the same axis and compared across semesters.
CAT1 = 'cat1'
CAT2 = 'cat2'
CA = 'ca'
END = 'end'
FINAL = 'final'
ASSESSMENTS = {
    CAT1: 'CAT 1',
    CAT2: 'CAT 2',
    CA: 'Continuous assessment (40%)',
    END: 'End of semester examination (60%)',
    FINAL: 'Final total (100%)',
}

#: A CA is passed at half of it — the college's own eligibility rule — while
#: everything else is passed at 40%.
PASS_MARKS = {CAT1: 40, CAT2: 40, CA: 50, END: 40, FINAL: 40}


def _mean(values):
    return round(sum(values) / len(values), 1) if values else None


def _percentage(result, assessment, serializer):
    """This student's mark for one assessment, as a percentage, or None if it
    has not been recorded yet.

    An assessment the student was marked absent from counts as zero: they sat
    nothing and scored nothing, and dropping them would flatter the module.
    """
    def raw(field):
        if getattr(result, f'{field}_absent'):
            return 0.0
        value = getattr(result, field)
        return None if value is None else float(value)

    practical = result.student.module.has_practical

    if assessment in (CAT1, CAT2):
        fields = [f'{assessment}_theory'] + ([f'{assessment}_practical'] if practical else [])
        marks = [raw(field) for field in fields]
        if any(mark is None for mark in marks):
            return None
        return round(sum(marks) / len(marks), 2)

    if assessment == CA:
        total = serializer.get_total_ca(result)
        return None if total is None else round(float(total) * 100 / 40, 2)

    if assessment == END:
        total = serializer.get_end_exam_total(result)
        return None if total is None else round(float(total) * 100 / 60, 2)

    total = serializer.get_final_total(result)
    return None if total is None else round(float(total), 2)


def _results(academic_year=None, semester=None, class_level=None, module=None,
             module_code=None, modules=None):
    qs = (
        StudentResult.objects
        .filter(**{k: v for k, v in {
            'student__module__semester__academic_year': academic_year,
            'student__module__semester': semester,
            'student__module__class_level': class_level,
            'student__module': module,
            'student__module__code': module_code,
        }.items() if v is not None})
        .select_related('student__module__class_level',
                        'student__module__semester__academic_year')
    )
    # A tutor sees their own modules and nobody else's. `modules` is None for
    # the admin roles, which is not the same as an empty list — a tutor with no
    # modules assigned must see nothing, not everything.
    return qs if modules is None else qs.filter(student__module__in=modules)


def _measure(results, assessment, serializer):
    """Marks, grades and a pass count for one bag of results."""
    marks, grades, passed = [], [], 0
    pass_mark = PASS_MARKS[assessment]
    for result in results:
        mark = _percentage(result, assessment, serializer)
        if mark is None:
            continue
        marks.append(mark)
        grade, _points, _label = grade_for_mark(mark, result.student.module.class_level)
        grades.append(grade)
        if mark >= pass_mark:
            passed += 1
    return marks, grades, passed


def _stats(marks, passed, enrolled):
    return {
        'enrolled': enrolled,
        'assessed': len(marks),
        'mean': _mean(marks),
        'highest': round(max(marks), 1) if marks else None,
        'lowest': round(min(marks), 1) if marks else None,
        'passed': passed,
        'pass_rate': round(passed * 100 / len(marks), 1) if marks else None,
    }


def summary(*, academic_year=None, semester=None, class_level=None, module=None,
            assessment=FINAL, modules=None):
    """How students are doing, and how that has moved.

    Filters narrow the population; the trend deliberately ignores the semester
    filter, because a single semester is a point and not a trend. `modules`
    narrows it further to what one tutor is allowed to see.
    """
    if assessment not in ASSESSMENTS:
        raise ValueError(f'Unknown assessment: {assessment}')
    serializer = StudentResultSerializer()

    scope = list(_results(academic_year, semester, class_level, module, modules=modules))
    marks, grades, passed = _measure(scope, assessment, serializer)
    enrolled_qs = Student.objects.filter(**{k: v for k, v in {
        'module__semester__academic_year': academic_year,
        'module__semester': semester,
        'module__class_level': class_level,
        'module': module,
    }.items() if v is not None})
    if modules is not None:
        enrolled_qs = enrolled_qs.filter(module__in=modules)
    enrolled = enrolled_qs.count()

    # Grade bands, in the order the college prints them rather than by count.
    order = ['A', 'B+', 'B', 'C', 'D', 'F']
    counts = {grade: grades.count(grade) for grade in order if grade in grades}
    distribution = [{'grade': grade, 'count': counts.get(grade, 0)}
                    for grade in order if grade in counts or grade in ('A', 'C', 'F')]

    return {
        'assessment': assessment,
        'assessment_label': ASSESSMENTS[assessment],
        'pass_mark': PASS_MARKS[assessment],
        'headline': _stats(marks, passed, enrolled),
        'distribution': distribution,
        'by_module': by_module(scope, assessment, serializer),
        # The trend deliberately drops the semester filter and follows the
        # module by its code, so "this module, over time" means something.
        'trend': trend(academic_year=academic_year, class_level=class_level,
                       module=module, assessment=assessment, serializer=serializer,
                       modules=modules),
    }


def by_module(results, assessment, serializer):
    """Every module in scope, worst mean first — the ones to look at are the
    ones at the top."""
    buckets = {}
    for result in results:
        buckets.setdefault(result.student.module_id, []).append(result)

    rows = []
    for module_id, group in buckets.items():
        module = group[0].student.module
        marks, _grades, passed = _measure(group, assessment, serializer)
        if not marks:
            continue
        rows.append({
            'module_id': module_id,
            'module': module.name,
            'code': module.code,
            'level': module.class_level.name,
            'semester': str(module.semester),
            **_stats(marks, passed, len(group)),
        })
    rows.sort(key=lambda row: (row['mean'] is None, row['mean']))
    return rows


def trend(*, academic_year=None, class_level=None, module=None, assessment=FINAL,
          serializer=None, limit=12, modules=None):
    """The same measurement, semester by semester, oldest first.

    Scoped to one academic year when the caller asked for one; otherwise the
    whole history, so a year-on-year movement is visible.
    """
    serializer = serializer or StudentResultSerializer()
    semesters = (
        Semester.objects.select_related('academic_year')
        .order_by('academic_year__name', 'number')
    )
    if academic_year is not None:
        semesters = semesters.filter(academic_year=academic_year)
    # A module belongs to one semester, so following *this* module would give a
    # single point and call it a trend. The subject is the module code — PHM101
    # taught again next year is the thing anyone wants to compare against.
    code = module.code if module is not None else None
    if code:
        semesters = semesters.filter(modules__code=code).distinct()

    points = []
    for semester in semesters:
        results = list(_results(semester=semester, class_level=class_level,
                                module_code=code, modules=modules))
        if not results:
            continue
        marks, _grades, passed = _measure(results, assessment, serializer)
        if not marks:
            continue
        points.append({
            'label': f'{semester.academic_year.name} · Semester {semester.number}',
            'academic_year': semester.academic_year.name,
            'semester': semester.number,
            **_stats(marks, passed, len(results)),
        })
    return points[-limit:]


def filter_options(modules=None):
    """What the page can be filtered by, so the screen never offers a year or a
    module that has nothing behind it — nor, to a tutor, somebody else's."""
    module_qs = Module.objects.select_related('class_level', 'semester')
    if modules is not None:
        module_qs = module_qs.filter(id__in=[m.id for m in modules])
    module_qs = list(module_qs.order_by('class_level__order', 'name'))

    years = AcademicYear.objects.order_by('-name')
    semesters = Semester.objects.select_related('academic_year')
    if modules is not None:
        semester_ids = {m.semester_id for m in module_qs}
        semesters = semesters.filter(id__in=semester_ids)
        years = years.filter(semesters__id__in=semester_ids).distinct()
    semesters = semesters.order_by('-academic_year__name', 'number')

    return {
        'academic_years': [{'id': y.id, 'name': y.name} for y in years],
        'semesters': [{'id': s.id, 'label': f'{s.academic_year.name} · Semester {s.number}',
                       'academic_year_id': s.academic_year_id, 'number': s.number}
                      for s in semesters],
        'modules': [{'id': m.id, 'name': m.name, 'code': m.code,
                     'class_level_id': m.class_level_id, 'semester_id': m.semester_id}
                    for m in module_qs],
        'assessments': [{'value': key, 'label': label} for key, label in ASSESSMENTS.items()],
    }
