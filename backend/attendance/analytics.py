"""Performance summaries and trends.

The college could always see one student's marks, and one module's marks, and
nothing above that. Whether NTA 5 did better this semester than last, whether a
module fails half its class every year, whether CAT 2 is always worse than CAT 1
— none of those questions could be asked, so none of them were.

Everything here reads; nothing here writes. A summary is a way of watching how
students are doing, which is not the same power as changing a mark.

The final result is read as the college acts on it (said 23 November 2026):
NACTVET's grade where it has been imported — most results arrive as grades,
not marks — then the supplementary's, else the college's approved marks. The
CATs, the CA and the examination stay on the marks entered here.
"""

from .grading import grade_for_mark, result_outcome
from .models import AcademicYear, Module, Semester, Student, StudentResult
from .serializers import StudentResultSerializer

#: The assessments a summary can be taken over. Every one is normalised to a
#: percentage so a CAT out of 100, a CA out of 40 and a final out of 100 can be
#: put on the same axis and compared across semesters.
RESULT = 'result'
CAT1 = 'cat1'
CAT2 = 'cat2'
CA = 'ca'
END = 'end'
FINAL = 'final'
ASSESSMENTS = {
    RESULT: 'Final result (official grade)',
    CAT1: 'CAT 1',
    CAT2: 'CAT 2',
    CA: 'Continuous assessment (40%)',
    END: 'End of semester examination (60%)',
    FINAL: 'Final total (100%), marks entered',
}

#: A CA is passed at half of it — the college's own eligibility rule — while
#: everything else is passed at 40%.
PASS_MARKS = {RESULT: None, CAT1: 40, CAT2: 40, CA: 50, END: 40, FINAL: 40}


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
    if assessment == RESULT:
        return grade_summary(academic_year=academic_year, semester=semester, class_level=class_level,
                             module=module, modules=modules)
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
        'kind': 'marks',
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


# ── the final result, as graded ───────────────────────────────────────────────

PASSED, SUPP_STATUSES, FAILED = {'PASS'}, {'SUPP'}, {'FAIL', 'REPEAT', 'DISCONTINUED'}
GRADE_ORDER = ['A', 'B+', 'B', 'C', 'D', 'F']


def official(result, serializer):
    """A module's official result, or None while there is none to count.

    NACTVET's grade when imported (its supplementary grade replacing a SUPP),
    else the college's marks once the examination office has approved them —
    the order the semester review and the results summary use. First sittings
    only: a repeat is the student's second try, and counting it with the class
    would compare unlike with unlike. Withheld, nullified and not-yet-complete
    results are not a pass or a fail, so they are not counted."""
    if result.student.attempt == Student.REPEAT:
        return None
    if not result.final_approved and not result.authority_grade:
        return None
    try:
        outcome = result_outcome(result, serializer)
    except ValueError:                       # a grade NACTVET's key does not know
        return None
    status = outcome['status']
    if status not in PASSED | SUPP_STATUSES | FAILED:
        return None
    letter = (outcome['grade'] or '').rstrip('*') or None
    if letter is None and outcome.get('official_total') is not None:
        letter = grade_for_mark(outcome['official_total'], result.student.module.class_level)[0]
    return {'status': status, 'grade': letter, 'points': outcome['grade_point']}


def _rate(part, whole):
    return round(part * 100 / whole, 1) if whole else None


def _grade_measure(results, serializer, enrolled=None):
    """Counts, rates and the grades of one bag of results."""
    outcomes = [o for o in (official(r, serializer) for r in results) if o is not None]
    graded = len(outcomes)
    passed = sum(o['status'] in PASSED for o in outcomes)
    supp = sum(o['status'] in SUPP_STATUSES for o in outcomes)
    failed = sum(o['status'] in FAILED for o in outcomes)
    points = [float(o['points']) for o in outcomes if o['points'] is not None]
    return {
        'enrolled': len(results) if enrolled is None else enrolled,
        'graded': graded, 'passed': passed, 'supp': supp, 'failed': failed,
        'pass_rate': _rate(passed, graded), 'supp_rate': _rate(supp, graded), 'fail_rate': _rate(failed, graded),
        'mean_points': round(sum(points) / len(points), 2) if points else None,
        'grades': [o['grade'] for o in outcomes if o['grade']],
    }


def _public(measure):
    return {key: value for key, value in measure.items() if key != 'grades'}


def _semester_key(semester):
    return (semester.academic_year.name, semester.number)


def _label(semester):
    return f'{semester.academic_year.name} · Semester {semester.number}'


def _compare(now, before, semester=None):
    """This against an earlier time: the change in pass rate, in points."""
    if before is None or not before['graded']:
        return None
    change = (round(now['pass_rate'] - before['pass_rate'], 1)
              if now['pass_rate'] is not None and before['pass_rate'] is not None else None)
    return {'label': _label(semester) if semester is not None else '', 'graded': before['graded'],
            'pass_rate': before['pass_rate'], 'mean_points': before['mean_points'], 'change': change}


def same_semester_a_year_before(semester):
    """Like for like: Semester 1 against the Semester 1 before it — the same
    modules, taught again to the next class."""
    return (Semester.objects.select_related('academic_year')
            .filter(number=semester.number, academic_year__name__lt=semester.academic_year.name)
            .order_by('-academic_year__name').first())


def _module_rows(results, serializer, modules=None):
    buckets = {}
    for result in results:
        buckets.setdefault(result.student.module_id, []).append(result)
    rows = []
    for group in buckets.values():
        module = group[0].student.module
        measure = _grade_measure(group, serializer)
        if not measure['graded']:
            continue
        # The last time this module was taught (its code, an earlier semester).
        earlier = (Module.objects.select_related('semester__academic_year')
                   .filter(code=module.code, semester__isnull=False)
                   .exclude(semester=module.semester_id))
        earlier = [m for m in earlier if module.semester_id is None
                   or _semester_key(m.semester) < _semester_key(module.semester)]
        before = None
        for previous in sorted(earlier, key=lambda m: _semester_key(m.semester), reverse=True):
            measured = _grade_measure(list(_results(module=previous, modules=modules)), serializer)
            if measured['graded']:
                before = _compare(measure, measured, previous.semester)
                break
        rows.append({
            'module_id': module.id, 'module': module.name, 'code': module.code,
            'level': module.class_level.name,
            'semester': _label(module.semester) if module.semester_id else '',
            'semester_key': list(_semester_key(module.semester)) if module.semester_id else None,
            **_public(measure), 'previous': before,
        })
    # The ones to look at first: the lowest pass rate, then the fewest points.
    rows.sort(key=lambda row: (row['pass_rate'], row['mean_points'] if row['mean_points'] is not None else 0))
    return rows


def grade_summary(*, academic_year=None, semester=None, class_level=None, module=None, modules=None):
    """The final result, as graded: passes, supplementaries and fails, the
    modules doing best and worst, and each against the last time."""
    serializer = StudentResultSerializer()
    scope = list(_results(academic_year, semester, class_level, module, modules=modules))
    measure = _grade_measure(scope, serializer)
    counts = {grade: measure['grades'].count(grade) for grade in GRADE_ORDER}
    distribution = [{'grade': grade, 'count': counts[grade]}
                    for grade in GRADE_ORDER if counts[grade] or grade in ('A', 'C', 'F')]
    rows = _module_rows(scope, serializer, modules=modules)

    # Best and weakest: within one semester, so like is ranked with like — the
    # one chosen, else the latest in the selection with results.
    latest = max((tuple(r['semester_key']) for r in rows if r['semester_key']), default=None)
    ranked = [r for r in rows if r['semester_key'] and tuple(r['semester_key']) == latest]
    ranked_label = next((r['semester'] for r in ranked), '')
    overall = _grade_measure([r for r in scope if r.student.module.semester_id and
                              _semester_key(r.student.module.semester) == latest], serializer) if latest else None
    # Best: at or above the semester's pass rate; weakest: below it.
    bar = overall['pass_rate'] if overall else None
    best = sorted([r for r in ranked if bar is not None and r['pass_rate'] >= bar],
                  key=lambda r: (-r['pass_rate'], -(r['mean_points'] or 0)))[:5] if len(ranked) > 1 else []
    weakest = [r for r in ranked if bar is not None and r['pass_rate'] < bar][:5]

    # Against the time before: the module taught last, or the same semester a
    # year earlier for the same level (or the whole college).
    previous = None
    if module is not None:
        previous = next((r['previous'] for r in rows if r['module_id'] == module.id), None)
    elif semester is not None:
        before = same_semester_a_year_before(semester)
        if before is not None:
            previous = _compare(measure, _grade_measure(
                list(_results(semester=before, class_level=class_level, modules=modules)), serializer), before)

    return {
        'kind': 'grades',
        'assessment': RESULT,
        'assessment_label': ASSESSMENTS[RESULT],
        'pass_mark': None,
        'headline': {**_public(measure), 'assessed': measure['graded'], 'previous': previous},
        'distribution': distribution,
        'by_module': rows,
        'ranking': {'semester': ranked_label, 'overall_pass_rate': overall['pass_rate'] if overall else None,
                    'best': best, 'weakest': weakest},
        'trend': grade_trend(academic_year=academic_year, class_level=class_level, module=module,
                             serializer=serializer, modules=modules),
    }


def grade_trend(*, academic_year=None, class_level=None, module=None, serializer=None, limit=12, modules=None):
    """Pass, supplementary and fail rates, semester by semester, oldest first;
    a module followed by its code."""
    serializer = serializer or StudentResultSerializer()
    semesters = Semester.objects.select_related('academic_year').order_by('academic_year__name', 'number')
    if academic_year is not None:
        semesters = semesters.filter(academic_year=academic_year)
    code = module.code if module is not None else None
    if code:
        semesters = semesters.filter(modules__code=code).distinct()
    points = []
    for semester in semesters:
        measure = _grade_measure(list(_results(semester=semester, class_level=class_level, module_code=code,
                                               modules=modules)), serializer)
        if not measure['graded']:
            continue
        points.append({'label': _label(semester), 'academic_year': semester.academic_year.name,
                       'semester': semester.number, **_public(measure)})
    return points[-limit:]


# ── the year's results, for the report ────────────────────────────────────────
#
# Two things, kept apart (said 23 November 2026): how each MODULE did — its own
# grades, pass / supp / fail — and how the STUDENTS did overall — each one's
# semester remark (PASS, SUPP, REPEAT, FAIL), which NACTVET decides from the
# GPA and from failing more than half the modules.

#: A module's columns, in the order the report prints them.
YEAR_OUTCOMES = ('pass', 'supp', 'fail', 'incomplete')
#: The students' remarks, as NACTVET words them (DISCO is its FAIL).
OVERALL_OUTCOMES = (('PASS', 'pass'), ('SUPP', 'supp'), ('REPEAT', 'repeat'), ('DISCO', 'fail'),
                    ('INCOMPLETE', 'incomplete'))


def _column(row):
    """One registration's result in the module, read from that module alone:

    pass        passed — at the first sitting, after the supplementary, or at a repeat;
    supp        a starred grade, the supplementary not yet sat (at a first sitting);
    fail        D or F, the supplementary failed, a repeat sitting failed, or NACTVET's FAIL;
    incomplete  no published result yet (missing, not approved, withheld, nullified, not sat)."""
    from . import progression
    status = row['status']
    if status == 'PASS':
        return 'pass'
    if status == 'SUPP':
        return 'fail' if row.get('attempt') == Student.REPEAT else 'supp'   # no supplementary at a repeat
    if status in progression.FAILED_STATUSES | {'DISCONTINUED'}:
        return 'fail'
    return 'incomplete'


def _year_columns(year):
    """{enrollment id: column} for every registration of the year that holds a
    result — one taken off part-way, or a module already passed, does not."""
    from . import progression
    serializer = StudentResultSerializer()
    columns = {}
    for semester in Semester.objects.filter(academic_year=year):
        by_profile, by_reg_no = progression.enrollments_by_profile(semester)
        for group in list(by_profile.values()) + list(by_reg_no.values()):
            idle = progression.not_holding(group, serializer)
            for enrollment in group:
                if enrollment.id not in idle:
                    columns[enrollment.id] = _column(progression.module_row(enrollment, serializer))
    return columns


def _pct(part, whole):
    return round(part * 100 / whole, 1) if whole else None


def _year_rows(year, class_level=None, modules=None, include_field=False):
    """{(level order, code): row} for every module of the year — field modules
    only when asked for — counting every registration, first sittings and
    repeaters."""
    columns = _year_columns(year)
    enrollments = (Student.objects.filter(id__in=columns)
                   .select_related('module__class_level', 'module__semester'))
    if not include_field:
        enrollments = enrollments.filter(module__is_field_module=False)
    if class_level is not None:
        enrollments = enrollments.filter(module__class_level=class_level)
    if modules is not None:
        enrollments = enrollments.filter(module__in=modules)
    rows = {}
    for enrollment in enrollments:
        module = enrollment.module
        key = (module.class_level.order, module.code)
        row = rows.setdefault(key, {
            'code': module.code, 'name': module.name, 'level': module.class_level.name,
            'field': module.is_field_module,
            'level_order': module.class_level.order, 'semester': module.semester.number,
            'registered': 0, 'repeaters': 0, **{name: 0 for name in YEAR_OUTCOMES}})
        row['registered'] += 1
        row['repeaters'] += enrollment.attempt == Student.REPEAT
        row[columns[enrollment.id]] += 1
    for row in rows.values():
        for name in YEAR_OUTCOMES:
            row[f'{name}_pct'] = _pct(row[name], row['registered'])
    return rows


def _totals(rows, outcomes, base='registered'):
    totals = {base: sum(r[base] for r in rows), **{name: sum(r[name] for r in rows) for name in outcomes}}
    if base == 'registered':
        totals['repeaters'] = sum(r['repeaters'] for r in rows)
    for name in outcomes:
        totals[f'{name}_pct'] = _pct(totals[name], totals[base])
    return totals


def previous_year(year):
    return AcademicYear.objects.filter(name__lt=year.name).order_by('-name').first()


def _overall_rows(year, class_level=None):
    """{(semester, level order, sheet): row} — the students' semester remarks,
    exactly as Results ▸ Results Summary gives them: the continuing sheet (the
    class's first sittings) and the repeaters' sheet."""
    from . import progression
    from .models import ClassLevel
    rows = {}
    for semester in Semester.objects.filter(academic_year=year).order_by('number'):
        levels = ClassLevel.objects.filter(modules__semester=semester).distinct().order_by('order')
        if class_level is not None:
            levels = levels.filter(id=class_level.id)
        for level in levels:
            for sheet in ('continuing', 'repeaters'):
                counts = progression.results_summary(semester, level, sheet=sheet)['counts']
                if not counts['total']:
                    continue
                row = {'semester': semester.number, 'level': level.name, 'level_order': level.order,
                       'sheet': sheet, 'students': counts['total'],
                       **{name: counts.get(remark, 0) for remark, name in OVERALL_OUTCOMES}}
                for _remark, name in OVERALL_OUTCOMES:
                    row[f'{name}_pct'] = _pct(row[name], row['students'])
                rows[(semester.number, level.order, sheet)] = row
    return rows


def year_overall(year, before_year, class_level=None):
    """The students' remarks for the year, each semester and level against the
    same one the year before."""
    names = [name for _remark, name in OVERALL_OUTCOMES]
    rows = _overall_rows(year, class_level)
    before = _overall_rows(before_year, class_level) if before_year else {}
    out = []
    for key in sorted(rows, key=lambda k: (k[0], k[1], k[2] != 'continuing')):
        row, last = rows[key], before.get(key)
        row['previous'] = None if last is None else {'students': last['students'],
                                                     **{f'{n}_pct': last[f'{n}_pct'] for n in names}}
        row['change'] = (round(row['pass_pct'] - last['pass_pct'], 1)
                         if last and row['pass_pct'] is not None and last['pass_pct'] is not None else None)
        out.append(row)
    continuing = [r for r in out if r['sheet'] == 'continuing']
    return {'rows': out, 'totals': _totals(continuing, names, base='students') if continuing else None}


def year_report(year, *, class_level=None, modules=None, include_field=False):
    """The year's results: each module on its own (field modules only when
    asked for) against the same module code the year before; and, apart, the
    students' overall remarks — not for a tutor, who sees their own modules."""
    rows = _year_rows(year, class_level, modules, include_field)
    before_year = previous_year(year)
    before = _year_rows(before_year, class_level, modules, include_field) if before_year else {}
    out = []
    for key in sorted(rows):
        row = rows[key]
        last = before.get(key)
        row['previous'] = None if last is None else {
            'name': last['name'], 'registered': last['registered'],
            **{f'{name}_pct': last[f'{name}_pct'] for name in YEAR_OUTCOMES},
            # The new curriculum reuses some codes for other modules: say so
            # rather than compare unlike with like in silence.
            'same_name': last['name'].strip().lower() == row['name'].strip().lower(),
        }
        row['change'] = (round(row['pass_pct'] - last['pass_pct'], 1)
                         if last and row['pass_pct'] is not None and last['pass_pct'] is not None else None)
        out.append(row)
    gone = [{'code': last['code'], 'name': last['name'], 'level': last['level'], 'pass_pct': last['pass_pct']}
            for key, last in sorted(before.items()) if key not in rows]
    return {
        'academic_year': year.name,
        'previous_year': before_year.name if before_year else None,
        'class_level': class_level.name if class_level else None,
        'include_field': include_field,
        'rows': out,
        'totals': _totals(out, YEAR_OUTCOMES),
        'previous_totals': _totals(list(before.values()), YEAR_OUTCOMES) if before else None,
        'not_this_year': gone,
        'overall': None if modules is not None else year_overall(year, before_year, class_level),
    }


def year_report_workbook(report):
    """The year's report as a workbook: each module's results; the students'
    overall remarks, kept apart; and the modules' pass rates beside last
    year's, with an Excel chart that can be pasted into the report as it is."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    navy = PatternFill('solid', fgColor='123F80')
    head_font = Font(bold=True, color='FFFFFF', size=10)
    centre = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin = Border(bottom=Side(style='thin', color='DCE2EA'))
    muted = Font(size=9, color='6B7785')
    year, before = report['academic_year'], report['previous_year']
    scope = report['class_level'] or 'All levels'
    last_head = f'Pass % {before}' if before else 'Pass % last year'

    def pct(value):
        return '' if value is None else value

    def change(now, then):
        return round(now - then, 1) if now is not None and then is not None else ''

    def heading(sheet, title, note, header):
        sheet.append([title])
        sheet['A1'].font = Font(bold=True, size=13)
        sheet.append([note])
        sheet['A2'].font = Font(italic=True, size=9, color='6B7785')
        sheet.append([])
        sheet.append(header)
        for cell in sheet[4]:
            cell.fill, cell.font, cell.alignment = navy, head_font, centre

    def widths(sheet, values):
        for column, width in enumerate(values, start=1):
            sheet.column_dimensions[get_column_letter(column)].width = width

    book = Workbook()

    # ── each module, on its own ──
    sheet = book.active
    sheet.title = f'Modules {year}'.replace('/', '-')
    heading(sheet, f'Module performance {year} — {scope}',
            'Each module read on its own grades. Every registration counted, repeaters included; field modules '
            + ('included' if report.get('include_field') else 'left out')
            + '. Percentages are of the students registered, so each row adds up to 100.',
            ['Level', 'Module code', 'Module', 'Semester', 'Registered', 'of whom repeaters', 'Pass %', 'Supp %',
             'Fail %', 'Incomplete %', last_head, 'Change (points)'])
    for row in report['rows']:
        last = row['previous']
        sheet.append([row['level'], row['code'], row['name'], row['semester'], row['registered'], row['repeaters'],
                      *[pct(row[f'{name}_pct']) for name in YEAR_OUTCOMES],
                      pct(last['pass_pct']) if last else '', pct(row['change'])])
        for cell in sheet[sheet.max_row]:
            cell.border = thin
    totals, last_totals = report['totals'], report['previous_totals']
    sheet.append(['All', '', 'All modules', '', totals['registered'], totals['repeaters'],
                  *[pct(totals[f'{name}_pct']) for name in YEAR_OUTCOMES],
                  pct(last_totals['pass_pct']) if last_totals else '',
                  change(totals['pass_pct'], last_totals['pass_pct']) if last_totals else ''])
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
    sheet.append([])
    notes = ['Pass: passed, at the first sitting, after the supplementary or at a repeat. Supp: a starred grade, the '
             'supplementary not yet sat. Fail: D or F, the supplementary failed, a repeat sitting failed, or '
             "NACTVET's FAIL. Incomplete: no published result yet."]
    renamed = [row['code'] for row in report['rows'] if row['previous'] and not row['previous']['same_name']]
    if renamed:
        notes.append(f'Same code, another module name last year (compare with care): {", ".join(renamed)}.')
    if report['not_this_year']:
        notes.append('Taught last year, not this year: '
                     + ', '.join(f'{m["code"]} {m["name"]}' for m in report['not_this_year']) + '.')
    for note in notes:
        sheet.append([note])
        sheet.cell(sheet.max_row, 1).font = muted
    widths(sheet, (14, 13, 40, 9, 11, 11, 9, 9, 9, 12, 14, 12))
    sheet.freeze_panes = 'D5'

    # ── the students, overall ──
    overall = report.get('overall')
    if overall is not None:
        names = [name for _remark, name in OVERALL_OUTCOMES]
        people = book.create_sheet(f'Overall {year}'.replace('/', '-'))
        heading(people, f'Overall results {year} — {scope}',
                "Each student's semester remark, as NACTVET decides it from the GPA and from failing more than half "
                'the modules — as Results ▸ Results Summary shows it. Percentages are of the students.',
                ['Semester', 'Level', 'Sheet', 'Students', 'Pass %', 'Supp %', 'Repeat %', 'Fail %', 'Incomplete %',
                 last_head, 'Change (points)'])
        for row in overall['rows']:
            last = row['previous']
            people.append([row['semester'], row['level'], 'Continuing' if row['sheet'] == 'continuing' else 'Repeaters',
                           row['students'], *[pct(row[f'{name}_pct']) for name in names],
                           pct(last['pass_pct']) if last else '', pct(row['change'])])
            for cell in people[people.max_row]:
                cell.border = thin
        if overall['totals']:
            t = overall['totals']
            people.append(['All', '', 'Continuing', t['students'], *[pct(t[f'{name}_pct']) for name in names]])
            for cell in people[people.max_row]:
                cell.font = Font(bold=True)
        people.append([])
        people.append(['Pass: passed every module. Supp: supplementaries to sit (GPA 2.0 or more, half or fewer '
                       'modules failed). Repeat: modules to repeat (failed, or more than half failed so no '
                       'supplementary). Fail: discontinued (GPA below 2.0, NACTVET\'s FAIL, or a repeat failed). '
                       'Incomplete: a result still missing.'])
        people.cell(people.max_row, 1).font = muted
        widths(people, (10, 14, 13, 10, 9, 9, 10, 9, 12, 14, 12))
        people.freeze_panes = 'D5'

    # ── the comparison, with its chart ──
    compare = book.create_sheet('Comparison')
    compare.append(['Module code', 'Module', last_head, f'Pass % {year}', 'Change (points)'])
    for cell in compare[1]:
        cell.fill, cell.font, cell.alignment = navy, head_font, centre
    for row in report['rows']:
        last = row['previous']
        compare.append([row['code'], row['name'], last['pass_pct'] if last and last['pass_pct'] is not None else 0,
                        row['pass_pct'] or 0, pct(row['change'])])
    widths(compare, (13, 40, 15, 15, 14))
    count = len(report['rows'])
    if count:
        chart = BarChart()
        chart.type = 'bar'
        chart.grouping = 'clustered'
        chart.title = f'Module pass rate: {year} against {before or "last year"}'
        chart.y_axis.title = 'Pass %'
        chart.y_axis.scaling.min, chart.y_axis.scaling.max = 0, 100
        chart.add_data(Reference(compare, min_col=3, max_col=4, min_row=1, max_row=count + 1), titles_from_data=True)
        chart.set_categories(Reference(compare, min_col=1, min_row=2, max_row=count + 1))
        chart.height = max(8, 0.6 * count + 3)
        chart.width = 18
        compare.add_chart(chart, 'G2')

    output = BytesIO()
    book.save(output)
    return output.getvalue()
