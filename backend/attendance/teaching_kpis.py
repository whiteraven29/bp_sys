"""Tutor KPIs against the teaching plan and the assessment plan (said 16
November 2026: "the QA must be able to have KPI defined to measure a tutor
performance as per teaching plan and assessment plan").

Every measure is read from what the plans already hold — classes marked,
registers, assessments held, setting plans moderated — on a day (today or a
CQI checkpoint), for the modules the account may follow. A plan is shared by
the tutors of its module, so each of them is measured on it. Figures are
summed before they are divided: a tutor with a 4-credit and a 20-credit
module is measured on all their classes, not on the mean of two percentages.
Nothing here records anything."""

from datetime import date, timedelta

from django.utils import timezone

from . import teaching_plans as tp
from .models import PlanAssessment, PlanSession, Session, TeachingPlan

#: The measures, in the order the screens show them. `better` says which way
#: is good; `target` is met, between target and `floor` is near, past the
#: floor is below. A measure with nothing to measure yet is not measured.
KPIS = [
    {'key': 'plans_approved', 'group': 'teaching', 'name': 'Plans approved', 'unit': '%', 'better': 'higher',
     'target': 100, 'floor': 75,
     'measure': 'Modules taught whose teaching plan the Head of Department (or DPARC) has approved.',
     'source': 'Teaching plan: approval'},
    {'key': 'on_schedule', 'group': 'teaching', 'name': 'Classes on schedule', 'unit': '%', 'better': 'higher',
     'target': 90, 'floor': 70,
     'measure': 'Plan classes due by the day that were taught: done 1, partly 0.5, and a class made up or '
                'finished in a later class counts as done from that day. Extra classes are not counted here.',
     'source': 'Teaching plan: classes'},
    {'key': 'hours', 'group': 'teaching', 'name': 'Hours taught', 'unit': '%', 'better': 'higher',
     'target': 90, 'floor': 70,
     'measure': 'Hours actually taught (extra and make-up classes included) of the plan hours due by the day. '
                'It can pass 100%.',
     'source': 'Teaching plan: hours'},
    {'key': 'evidence', 'group': 'teaching', 'name': 'Taught with a register', 'unit': '%', 'better': 'higher',
     'target': 100, 'floor': 90,
     'measure': 'Classes marked taught that have the attendance register of their day.',
     'source': 'Attendance registers'},
    {'key': 'missed_open', 'group': 'teaching', 'name': 'Missed, not made up', 'unit': 'classes',
     'better': 'lower', 'target': 0, 'floor': 2,
     'measure': 'Plan classes due, marked missed, and not made up in a later class.',
     'source': 'Teaching plan: classes'},
    {'key': 'registers_outside', 'group': 'teaching', 'name': 'Registers not in the plan', 'unit': 'registers',
     'better': 'lower', 'target': 0, 'floor': 3,
     'measure': 'Attendance registers on days the plan has no class marked taught: classes taught but not '
                'entered in the plan. The students\' attendance is not affected.',
     'source': 'Attendance registers against the plan'},
    {'key': 'held', 'group': 'assessment', 'name': 'Assessments held', 'unit': '%', 'better': 'higher',
     'target': 100, 'floor': 80,
     'measure': 'Continuous assessments (tests, assignments, practicals) due by the day that have been held.',
     'source': 'Assessment plan: CA'},
    {'key': 'moderated', 'group': 'assessment', 'name': 'Moderated before given', 'unit': '%', 'better': 'higher',
     'target': 100, 'floor': 80,
     'measure': 'Assessments held whose setting plan the Head of Department moderated on or before the day '
                'it was held.',
     'source': 'Assessment plan, Part IV: moderation'},
    {'key': 'coverage', 'group': 'assessment', 'name': 'Assessment plan covered', 'unit': '%', 'better': 'higher',
     'target': 100, 'floor': 80,
     'measure': 'Tasks the assessment plan requires, covered by the setting plans — counted only for kinds of '
                'assessment whose every sitting is due by the day.',
     'source': 'Assessment plan: what each kind is drawn from'},
    {'key': 'tested_early', 'group': 'assessment', 'name': 'Tested before taught', 'unit': 'tasks',
     'better': 'lower', 'target': 0, 'floor': 0,
     'measure': 'Tasks in assessments due by the day that come before the class that teaches them.',
     'source': 'Assessment plan against the teaching plan'},
]
KPI_KEYS = [k['key'] for k in KPIS]
_BY_KEY = {k['key']: k for k in KPIS}

COUNTS = ('modules', 'plans', 'approved', 'due', 'done_due', 'hours_due', 'hours_done', 'taught',
          'taught_with_register', 'missed_open', 'registers_outside', 'ca_due', 'ca_held', 'held',
          'held_moderated', 'cov_required', 'cov_covered', 'tested_early', 'assessments_due')


def band(key, value):
    if value is None:
        return 'not_measured'
    k = _BY_KEY[key]
    if k['better'] == 'higher':
        return 'met' if value >= k['target'] else 'near' if value >= k['floor'] else 'below'
    return 'met' if value <= k['target'] else 'near' if value <= k['floor'] else 'below'


def _pct(n, d):
    return None if not d else round(100 * float(n) / float(d))


def values(c):
    """The ten measures from summed counts."""
    planned = c['plans'] > 0
    return {
        'plans_approved': _pct(c['approved'], c['modules']),
        'on_schedule': _pct(c['done_due'], c['due']),
        'hours': _pct(c['hours_done'], c['hours_due']),
        'evidence': _pct(c['taught_with_register'], c['taught']),
        'missed_open': c['missed_open'] if c['due'] else None,
        'registers_outside': c['registers_outside'] if (planned or c['registers_outside']) else None,
        'held': _pct(c['ca_held'], c['ca_due']),
        'moderated': _pct(c['held_moderated'], c['held']),
        'coverage': _pct(c['cov_covered'], c['cov_required']),
        'tested_early': c['tested_early'] if c['assessments_due'] else None,
    }


def scored(c):
    v = values(c)
    b = {key: band(key, v[key]) for key in KPI_KEYS}
    measured = [key for key in KPI_KEYS if b[key] != 'not_measured']
    return {'values': v, 'bands': b, 'measured': len(measured),
            'met': sum(1 for key in measured if b[key] == 'met'),
            'counts': {k: (float(c[k]) if k in ('hours_due', 'hours_done', 'done_due') else c[k]) for k in COUNTS}}


def _local_day(moment):
    return timezone.localtime(moment).date() if moment else None


def module_counts(module, plan, day, sessions=None, assessments=None):
    """Every count the measures need, for one module on a day."""
    c = dict.fromkeys(COUNTS, 0)
    c['modules'] = 1
    if plan is None:
        c['registers_outside'] = Session.objects.filter(module=module, date__lte=day).count()
        return c
    sessions = list(plan.sessions.all()) if sessions is None else sessions
    c['plans'] = 1
    c['approved'] = int(plan.status == TeachingPlan.APPROVED)
    p = tp.progress(plan, day, sessions)
    by_id = {s.id: s for s in sessions}
    due = [s for s in sessions if not s.extra and s.planned_on <= day]
    c['due'], c['done_due'] = p['due'], p['done_due']
    c['hours_due'], c['hours_done'] = p['hours_due'], p['hours_done']
    c['taught'] = p['taught']
    c['taught_with_register'] = p['taught'] - p['without_register']
    c['missed_open'] = sum(1 for s in due if s.status == PlanSession.MISSED and not tp._finished(s, day, by_id))
    c['registers_outside'] = len(tp.registers_outside(plan, day, sessions))

    assessments = list(plan.assessments.all()) if assessments is None else assessments
    ca = [a for a in assessments if a.stage != 'se']
    ca_due = [a for a in ca if a.planned_on and a.planned_on <= day]
    held = [a for a in ca if a.held_on and a.held_on <= day]
    c['ca_due'] = len(ca_due)
    c['ca_held'] = sum(1 for a in ca_due if a.held_on and a.held_on <= day)
    c['held'] = len(held)
    c['held_moderated'] = sum(1 for a in held if a.moderation == PlanAssessment.APPROVED
                              and _local_day(a.moderated_at) is not None and _local_day(a.moderated_at) <= a.held_on)
    c['assessments_due'] = len(ca_due)

    cm = plan.curriculum_module
    if cm is not None:
        for component in cm.components.prefetch_related('required_tasks'):
            required = set(component.required_tasks.all())
            sittings = [a for a in ca if a.component_id == component.id]
            if not required or not sittings or any(not a.planned_on or a.planned_on > day for a in sittings):
                continue
            covered = set()
            for a in sittings:
                covered.update(a.tasks.all())
            c['cov_required'] += len(required)
            c['cov_covered'] += len(required & covered)
    c['tested_early'] = len(tp.coverage(plan, day=day)['tested_before_taught'])
    return c


def _add(total, part):
    for k in COUNTS:
        total[k] += part[k]
    return total


def _weeks(semester, day):
    """The Sundays closing each teaching week up to the day (the day itself
    for the week it falls in), for the trend."""
    calendar = tp.calendar_for(semester)
    if calendar is None or calendar.teaching_starts > day:
        return []
    start = calendar.teaching_starts - timedelta(days=calendar.teaching_starts.weekday())
    end = min(day, calendar.teaching_ends)
    out, monday = [], start
    while monday <= end:
        out.append(min(monday + timedelta(days=6), end))
        monday += timedelta(days=7)
    return out


def tutor_kpis(user, semester, day=None):
    with tp.names_remembered():
        return _tutor_kpis(user, semester, day or date.today())


def _tutor_kpis(user, semester, day):
    modules = list(tp.visible_modules(user).filter(semester=semester)
                   .select_related('class_level', 'programme__department')
                   .prefetch_related('teachers').order_by('class_level__order', 'code'))
    plans = {p.module_id: p for p in TeachingPlan.objects.filter(module__in=[m.id for m in modules])
             .select_related('curriculum_module').prefetch_related('sessions', 'assessments__tasks')}
    weeks = _weeks(semester, day)
    tutors, unassigned, everyone = {}, [], dict.fromkeys(COUNTS, 0)
    trend_all = [[0, 0] for _ in weeks]
    for module in modules:
        plan = plans.get(module.id)
        sessions = list(plan.sessions.all()) if plan else []
        counts = module_counts(module, plan, day, sessions, list(plan.assessments.all()) if plan else None)
        _add(everyone, counts)
        series = []
        for i, end in enumerate(weeks):
            if plan is None:
                series.append((0, 0))
                continue
            p = tp.progress(plan, end, sessions)
            series.append((p['done_due'], p['due']))
            trend_all[i][0] += p['done_due']
            trend_all[i][1] += p['due']
        row = {'module_id': module.id, 'code': module.code, 'name': module.name, 'level': module.class_level.name,
               'plan_id': plan.id if plan else None,
               'status': plan.get_status_display() if plan else 'No plan yet', **scored(counts)}
        teachers = list(module.teachers.all())
        if not teachers:
            unassigned.append(row)
        for teacher in teachers:
            entry = tutors.setdefault(teacher.pk, {'id': teacher.pk, 'name': tp._name(teacher),
                                                   'counts': dict.fromkeys(COUNTS, 0), 'modules': [],
                                                   'trend': [[0, 0] for _ in weeks]})
            _add(entry['counts'], counts)
            entry['modules'].append(row)
            for i, (done, due) in enumerate(series):
                entry['trend'][i][0] += done
                entry['trend'][i][1] += due
    rows = []
    for entry in sorted(tutors.values(), key=lambda e: e['name'].lower()):
        rows.append({'id': entry['id'], 'name': entry['name'], 'modules': entry['modules'],
                     'trend': [_pct(d, n) for d, n in entry['trend']], **scored(entry['counts'])})
    return {
        'semester': str(semester), 'semester_id': semester.id, 'as_of': day,
        'definitions': KPIS, 'tutors': rows, 'unassigned': unassigned, 'all': scored(everyone),
        'trend': {'weeks': [{'n': i + 1, 'ends': end} for i, end in enumerate(weeks)],
                  'all': [_pct(d, n) for d, n in trend_all]},
        'checkpoints': [{'title': e.title, 'on': e.starts_on}
                        for e in tp.events_of(semester, tp.CalendarEvent.CQI)],
    }


def may_see(user):
    return tp.sees_every_plan(user) or tp.is_head_of_department(user)

