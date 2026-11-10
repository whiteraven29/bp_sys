"""Teaching plans (said 8–9 November 2026).

A tutor's plan for one module in one semester: the classes week by week, and
when each assessment is held. Most of it is known before the tutor starts:

  * the curriculum (CurriculumModule) gives the outcomes (SEOs), their related
    tasks, the hours a week of lecture, tutorial, practical and assignment, and
    how the module is assessed — loaded from the national documents;
  * the teaching calendar (TeachingCalendar, CalendarEvent) gives the weeks:
    when teaching starts, the public holidays and recess, the CAT weeks, the
    examinations — from the almanac, kept by DPARC.

So the tutor says on which days the module is taught; the plan is laid out
from that, outcomes in curriculum order and assessments in their weeks, and
the tutor adjusts it. After each class the tutor marks it done; the
attendance register taken that day is the evidence, and a class marked done
with no register is shown, not refused.

How far a tutor has got is measured against their own plan — classes done
of those due by today — so a 4-credit module and a 20-credit one are judged
alike. The Head of Department approves a plan and moderates each test before
it is given (assessment plan, Part IV); DPARC does for a module with no Head
of Department. The Principal, DPARC and QA see every plan; a Head of
Department their department's.

Nothing here touches marks or results: the weights shown are the assessment
plan's, or for a module of the old curriculum the weights results use today.
"""

import math
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    AssessmentComponent, CalendarEvent, CurriculumModule, LearningOutcome, Module, PlanAssessment, PlanEvent,
    PlanNote, PlanSession, RelatedTask, Session, TeachingCalendar, TeachingPlan,
)
from .roles import is_break_glass, is_dparc, is_head_of_department, is_principal, is_quality_assurance


class PlanError(ValueError):
    """Something the person can put right; the message says what."""


WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
KINDS = dict(PlanSession.KIND_CHOICES)
TEST_METHODS = frozenset({'written_test', 'practical_test', 'oral_test', 'ospe'})
#: How far behind is "behind": classes done of those due by today.
ON_TRACK, BEHIND = 90, 70
TWO = Decimal('0.01')


# ── who sees and does what ────────────────────────────────────────────────────

def sees_every_plan(user):
    return is_principal(user) or is_dparc(user) or is_quality_assurance(user) or is_break_glass(user)


def visible_modules(user):
    """The modules whose plans this account may read: their own, their
    department's for a Head of Department, all for the Principal, DPARC, QA."""
    if sees_every_plan(user):
        return Module.objects.all()
    rule = Q(teachers=user)
    if is_head_of_department(user):
        rule |= Q(programme__department__hod=user)
    return Module.objects.filter(rule).distinct()


def may_read(plan, user):
    return visible_modules(user).filter(pk=plan.module_id).exists()


def teaches(module, user):
    return bool(user and user.is_authenticated and module.teachers.filter(pk=user.pk).exists())


def approver_of(module):
    """Who approves this module's plan and moderates its tests: the Head of
    its department, or DPARC when it has none. Returns the user or None (for
    DPARC, whoever holds the office)."""
    department = getattr(getattr(module, 'programme', None), 'department', None)
    return department.hod if department is not None and department.hod_id else None


def may_approve(module, user):
    """Never one's own plan: a Head of Department who teaches the module
    leaves it to DPARC."""
    if is_break_glass(user):
        return True
    if teaches(module, user):
        return False
    head = approver_of(module)
    if head is not None and head.pk == user.pk:
        return True
    return is_dparc(user)


# ── the curriculum a module follows ───────────────────────────────────────────

def level_of(module):
    found = re.search(r'(\d+)', module.class_level.name or '')
    return int(found.group(1)) if found else None


def _words(text):
    return {w for w in re.findall(r'[a-z]+', (text or '').lower()) if len(w) > 3}


def curriculum_module_for(module):
    """The curriculum's description of this module: same code, same NTA level
    and a like name. The code alone is not enough — the old and new curricula
    give PST04104 to different modules — so a module of the old curriculum
    finds nothing, and its plan is made without outcomes."""
    candidates = CurriculumModule.objects.filter(code__iexact=module.code.replace(' ', ''))
    level = level_of(module)
    if level:
        candidates = candidates.filter(level=level)
    name = _words(module.name)
    best, score = None, 0.0
    for candidate in candidates:
        likeness = len(name & _words(candidate.name)) / max(len(name), 1)
        if likeness > score:
            best, score = candidate, likeness
    return best if score >= 0.5 else None


# ── the calendar ──────────────────────────────────────────────────────────────

def calendar_for(semester):
    return TeachingCalendar.objects.filter(semester=semester).first()


def blocked_days(semester):
    """{day: why} for every day no class is planned on."""
    days = {}
    for event in CalendarEvent.objects.filter(semester=semester, kind__in=CalendarEvent.NO_TEACHING):
        day = event.starts_on
        while day <= event.ends_on:
            days.setdefault(day, event.title)
            day += timedelta(days=1)
    return days


def events_of(semester, *kinds):
    return list(CalendarEvent.objects.filter(semester=semester, kind__in=kinds).order_by('starts_on'))


def weeks_of_study(plan):
    if plan.weeks:
        return plan.weeks
    cm = plan.curriculum_module
    return cm.curriculum.weeks_of_study if cm is not None else 15


def teaching_weeks(plan):
    """The Mondays of the plan's teaching weeks: from the start, a week at a
    time, leaving out a week with no teaching day in it (the recess), up to
    the weeks of study or the end of teaching, whichever comes first."""
    calendar = calendar_for(plan.module.semester)
    if calendar is None:
        raise PlanError('The teaching calendar for this semester is not set yet. DPARC sets it '
                        '(Teaching plans → Teaching calendar).')
    start = plan.starts_on or calendar.teaching_starts
    blocked = blocked_days(plan.module.semester)
    monday = start - timedelta(days=start.weekday())
    wanted, found = weeks_of_study(plan), []
    while len(found) < wanted and monday <= calendar.teaching_ends:
        days = [monday + timedelta(days=d) for d in range(5) if monday + timedelta(days=d) >= start]
        if days and not all(day in blocked for day in days):
            found.append(monday)
        monday += timedelta(days=7)
    return found


def _place(day, monday, blocked, start, end):
    """The day itself if a class can be held on it, or the nearest teaching
    day of the same week; (day, why it is a problem) otherwise."""
    if day not in blocked and start <= day <= end:
        return day, ''
    options = [monday + timedelta(days=d) for d in range(5)]
    later = [d for d in options if d > day and d not in blocked and start <= d <= end]
    earlier = [d for d in reversed(options) if d < day and d not in blocked and start <= d <= end]
    for choice in later + earlier:
        return choice, f'Moved from {day:%a %d %b} ({blocked.get(day, "outside teaching")}).'
    return day, f'Falls on {blocked.get(day, "a day outside teaching")}: move it.'


# ── the weekly pattern ────────────────────────────────────────────────────────

def default_pattern(cm, module):
    """The week as the curriculum's scheme of study gives it, on two days the
    tutor will likely change: lecture and tutorial on Monday, practical and
    assignment on Thursday. PST04104 (L2 T1 P4 A1) is exactly the plan its
    tutor wrote by hand."""
    if cm is not None:
        hours = {'lecture': cm.lecture_hours, 'tutorial': cm.tutorial_hours,
                 'practical': cm.practical_hours, 'assignment': cm.assignment_hours}
    else:
        # The old curriculum is not loaded: a guess, to be put right.
        hours = {'lecture': 2, 'tutorial': 1, 'practical': 2 if module.has_practical else 0, 'assignment': 1}
    second = 3 if hours['practical'] else 2
    days = {'lecture': 0, 'tutorial': 0, 'practical': second, 'assignment': second}
    return [{'weekday': days[kind], 'kind': kind, 'hours': amount}
            for kind, amount in hours.items() if amount]


def clean_pattern(raw):
    if not isinstance(raw, list) or not raw:
        raise PlanError('Give at least one class a week: the day, the kind of class and its hours.')
    out = []
    for item in raw:
        try:
            weekday, kind, hours = int(item['weekday']), str(item['kind']), Decimal(str(item['hours']))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise PlanError('Each class needs a day, a kind and its hours.')
        if not 0 <= weekday <= 5:
            raise PlanError('Classes run Monday to Saturday.')
        if kind not in KINDS:
            raise PlanError(f'"{kind}" is not a kind of class.')
        if not Decimal('0.5') <= hours <= 12:
            raise PlanError('A class is between half an hour and 12 hours.')
        out.append({'weekday': weekday, 'kind': kind, 'hours': float(hours)})
    return sorted(out, key=lambda c: (c['weekday'], list(KINDS).index(c['kind'])))


def pattern_hours(pattern):
    return sum(Decimal(str(c['hours'])) for c in pattern)


# ── the old curriculum's scheme: the weights results use today ────────────────

def results_scheme(module):
    """For a module the loaded curriculum does not describe: the parts and
    weights the results calculation uses now (serializers.py), so the plan
    and the mark sheet agree."""
    if module.is_field_module:
        return [{'stage': 'field', 'method': 'other', 'label': 'Field CA', 'count': 1, 'weight': Decimal(40)},
                {'stage': 'se', 'method': 'other', 'label': 'Field final', 'count': 1, 'weight': Decimal(60)}]
    if module.has_practical:
        return [{'stage': 'ca', 'method': 'assignment', 'label': 'Assignment', 'count': 2, 'weight': Decimal(4)},
                {'stage': 'ca', 'method': 'written_test', 'label': 'CAT (theory)', 'count': 2, 'weight': Decimal(16)},
                {'stage': 'ca', 'method': 'practical_test', 'label': 'Practical test', 'count': 2,
                 'weight': Decimal(20)},
                {'stage': 'se', 'method': 'written_exam', 'label': 'End of semester — theory', 'count': 1,
                 'weight': Decimal(30)},
                {'stage': 'se', 'method': 'practical_exam', 'label': 'End of semester — practical', 'count': 1,
                 'weight': Decimal(30)}]
    return [{'stage': 'ca', 'method': 'assignment', 'label': 'Assignment', 'count': 2, 'weight': Decimal(10)},
            {'stage': 'ca', 'method': 'written_test', 'label': 'CAT', 'count': 2, 'weight': Decimal(30)},
            {'stage': 'se', 'method': 'written_exam', 'label': 'End of semester', 'count': 1, 'weight': Decimal(60)}]


def scheme_for(plan):
    """[{'stage','method','label','count','weight','component'}] — the
    assessment plan's parts, or the old scheme."""
    cm = plan.curriculum_module
    if cm is not None and cm.components.exists():
        return [{'stage': c.stage, 'method': c.method, 'label': c.label, 'count': c.min_count,
                 'weight': c.weight, 'component': c} for c in cm.components.order_by('order')]
    return results_scheme(plan.module)


def _singular(label):
    label = re.sub(r'\s*\(.*?\)\s*', ' ', label).strip()
    for plural, single in (('Tests', 'Test'), ('tests', 'test'), ('Assignments', 'Assignment'),
                           ('assignments', 'assignment'), ('Studies', 'Study'), ('scenarios', 'scenario'),
                           ('Scenarios', 'Scenario')):
        label = label.replace(plural, single)
    return label


# ── laying a plan out ─────────────────────────────────────────────────────────

def log(plan, actor, action, detail=''):
    PlanEvent.objects.create(plan=plan, actor=actor if getattr(actor, 'is_authenticated', False) else None,
                             action=action, detail=str(detail)[:500])


@transaction.atomic
def start_plan(module, actor):
    """A plan for a module, made by one of its tutors."""
    existing = TeachingPlan.objects.filter(module=module).first()
    if existing is not None:
        return existing
    cm = curriculum_module_for(module)
    plan = TeachingPlan.objects.create(module=module, curriculum_module=cm, created_by=actor,
                                       pattern=default_pattern(cm, module))
    log(plan, actor, 'started', f'Following {cm.curriculum.code} {cm.code}' if cm else
        'No curriculum loaded for this module: outcomes are typed in.')
    return plan


@transaction.atomic
def lay_out(plan, pattern, *, actor, weeks=None, starts_on=None, assessments=True):
    """Lay the classes out over the semester from the weekly pattern. Only
    before the plan is sent for approval and before any class is marked; after
    that, classes are moved one at a time with a reason."""
    if plan.status in (TeachingPlan.SUBMITTED, TeachingPlan.APPROVED):
        raise PlanError('This plan has been sent for approval: move classes one at a time instead.')
    if plan.sessions.exclude(status=PlanSession.PLANNED).exists():
        raise PlanError('Classes are already marked as taught: move classes one at a time instead.')
    plan.pattern = clean_pattern(pattern)
    plan.weeks = int(weeks) if weeks else None
    plan.starts_on = starts_on or None
    if plan.weeks is not None and not 1 <= plan.weeks <= 30:
        raise PlanError('A plan runs between 1 and 30 weeks.')
    plan.save(update_fields=['pattern', 'weeks', 'starts_on', 'updated_at'])
    calendar = calendar_for(plan.module.semester)
    mondays = teaching_weeks(plan)
    if not mondays:
        raise PlanError('No teaching week falls between the start and the end of teaching.')
    start = plan.starts_on or calendar.teaching_starts
    blocked = blocked_days(plan.module.semester)
    plan.sessions.all().delete()
    slots = []
    for week, monday in enumerate(mondays, start=1):
        for entry in plan.pattern:
            day = monday + timedelta(days=entry['weekday'])
            if day < start:
                continue
            placed, why = _place(day, monday, blocked, start, calendar.teaching_ends)
            slots.append((week, placed, entry, why))
    outcomes = list(plan.curriculum_module.outcomes.order_by('order')) if plan.curriculum_module else []
    taught = [s for s in slots if s[2]['kind'] != PlanSession.ASSIGNMENT]
    tutor = actor if teaches(plan.module, actor) else plan.module.teachers.first()
    made, current, at = [], None, 0
    for order, (week, day, entry, why) in enumerate(slots):
        outcome = None
        if outcomes:
            if entry['kind'] != PlanSession.ASSIGNMENT:
                current = outcomes[min(len(outcomes) - 1, math.floor(at * len(outcomes) / max(len(taught), 1)))]
                at += 1
            outcome = current or outcomes[0]
        made.append(PlanSession(plan=plan, order=order, week=week, planned_on=day, kind=entry['kind'],
                                hours=Decimal(str(entry['hours'])), outcome=outcome, tutor=tutor, note=why))
    PlanSession.objects.bulk_create(made)
    if assessments:
        lay_out_assessments(plan)
    wanted = weeks_of_study(plan)
    log(plan, actor, 'laid out', f'{len(made)} classes over {len(mondays)} weeks'
        + (f' — only {len(mondays)} of {wanted} weeks fit before teaching ends' if len(mondays) < wanted else ''))
    if plan.status == TeachingPlan.RETURNED:
        plan.status = TeachingPlan.DRAFT
        plan.save(update_fields=['status', 'updated_at'])
    return plan


def lay_out_assessments(plan):
    """One row per assessment the scheme asks for, dated: tests in the CAT
    weeks, assignments spread before the deadline for their results, the
    examinations in the examination window. Rows already sent for moderation
    or held are kept."""
    semester = plan.module.semester
    plan.assessments.filter(moderation=PlanAssessment.DRAFT, held_on__isnull=True).delete()
    kept = {(a.stage, a.method, a.number) for a in plan.assessments.all()}
    calendar = calendar_for(semester)
    start = (plan.starts_on or calendar.teaching_starts) if calendar else None
    cats = events_of(semester, CalendarEvent.CAT)
    exams = events_of(semester, CalendarEvent.EXAM)
    deadlines = [e for e in events_of(semester, CalendarEvent.DEADLINE) if 'assignment' in e.title.lower()]
    last_day = (deadlines[0].starts_on if deadlines else (cats[-1].starts_on if cats else
                (calendar.teaching_ends if calendar else None)))
    rows = []
    for part in scheme_for(plan):
        count = max(int(part['count']), 1)
        each = (Decimal(part['weight']) / count).quantize(TWO, rounding=ROUND_HALF_UP)
        for number in range(1, count + 1):
            if (part['stage'], part['method'], number) in kept:
                continue
            when = None
            if part['stage'] == 'se':
                when = exams[0].starts_on if exams else None
            elif part['stage'] == 'field':
                when = None
            elif part['method'] in TEST_METHODS and number <= len(cats):
                when = cats[number - 1].starts_on
            elif start and last_day and last_day > start:
                span = (last_day - start).days
                when = start + timedelta(days=round(span * number / (count + 1)))
                when += timedelta(days=(4 - when.weekday()) % 7 if when.weekday() <= 4 else 0)  # its Friday
            title = _singular(part['label']) + (f' {number}' if count > 1 else '')
            rows.append(PlanAssessment(plan=plan, component=part.get('component'), stage=part['stage'],
                                       method=part['method'], number=number, title=title[:120], weight=each,
                                       planned_on=when))
    PlanAssessment.objects.bulk_create(rows)


# ── following it ──────────────────────────────────────────────────────────────

def _check_outcome(plan, outcome_id):
    if outcome_id in (None, ''):
        return None
    outcome = LearningOutcome.objects.filter(pk=outcome_id, module=plan.curriculum_module).first()
    if outcome is None:
        raise PlanError('That outcome is not one of this module\'s.')
    return outcome


def _check_tasks(plan, task_ids):
    ids = [int(t) for t in (task_ids or []) if str(t).isdigit()]
    tasks = list(RelatedTask.objects.filter(pk__in=ids, outcome__module=plan.curriculum_module))
    if len(tasks) != len(set(ids)):
        raise PlanError('Some of those tasks are not this module\'s.')
    return tasks


@transaction.atomic
def edit_session(session, data, *, actor):
    """Change what a class covers, or move it. Once the plan is sent for
    approval a move needs a reason, and the first date is kept."""
    plan = session.plan
    changed = []
    if 'outcome_id' in data:
        session.outcome = _check_outcome(plan, data.get('outcome_id'))
        changed.append('outcome')
    if 'topic' in data:
        session.topic = str(data.get('topic') or '')[:300]
        changed.append('topic')
    if 'kind' in data:
        if data['kind'] not in KINDS:
            raise PlanError('Not a kind of class.')
        session.kind = data['kind']
        changed.append('kind')
    if 'hours' in data:
        hours = Decimal(str(data['hours']))
        if not Decimal('0.5') <= hours <= 12:
            raise PlanError('A class is between half an hour and 12 hours.')
        session.hours = hours
        changed.append('hours')
    if data.get('planned_on'):
        day = data['planned_on']
        if day != session.planned_on:
            reason = str(data.get('reason') or '').strip()
            if plan.status != TeachingPlan.DRAFT and not reason:
                raise PlanError('Say why the class moves: the plan has been sent for approval.')
            if session.status != PlanSession.PLANNED:
                raise PlanError('A class already marked is not moved; add a make-up class instead.')
            if session.moved_from is None:
                session.moved_from = session.planned_on
            log(plan, actor, 'moved a class', f'{session.get_kind_display()} of {session.planned_on:%d %b} to '
                f'{day:%d %b}' + (f': {reason}' if reason else ''))
            session.planned_on, session.move_reason, session.note = day, reason[:300], ''
            changed.append('date')
    session.save()
    if 'task_ids' in data:
        session.tasks.set(_check_tasks(plan, data.get('task_ids')))
    return session


@transaction.atomic
def add_session(plan, data, *, actor):
    """A class the layout did not have — a make-up class, an extra practical."""
    day = data.get('planned_on')
    if not day:
        raise PlanError('Give the date of the class.')
    kind = data.get('kind') or PlanSession.LECTURE
    if kind not in KINDS:
        raise PlanError('Not a kind of class.')
    first = plan.sessions.order_by('planned_on').first()
    week = ((day - first.planned_on).days // 7 + first.week) if first else 1
    session = PlanSession.objects.create(
        plan=plan, order=plan.sessions.count(), week=max(week, 1), planned_on=day, kind=kind,
        hours=Decimal(str(data.get('hours') or 1)), outcome=_check_outcome(plan, data.get('outcome_id')),
        topic=str(data.get('topic') or '')[:300], tutor=actor if teaches(plan.module, actor) else None,
        note=str(data.get('note') or 'Added to the plan')[:300])
    log(plan, actor, 'added a class', f'{session.get_kind_display()} on {day:%d %b}')
    return session


@transaction.atomic
def mark(session, status, *, delivered_on=None, note='', actor, today=None):
    """What became of a class. Done or partly done needs the day it was
    taught; the register of that day, if there is one, is linked as evidence."""
    today = today or date.today()
    if status not in dict(PlanSession.STATUS_CHOICES):
        raise PlanError('Not a state a class can be in.')
    if status in (PlanSession.DONE, PlanSession.PARTIAL):
        delivered_on = delivered_on or session.planned_on
        if delivered_on > today:
            raise PlanError('A class is marked once it has been taught.')
        session.register = (Session.objects.filter(module=session.plan.module, date=delivered_on)
                            .order_by('created_at').first())
    else:
        delivered_on, session.register = None, None
    session.status, session.delivered_on = status, delivered_on
    session.note = str(note or session.note or '')[:300] if status != PlanSession.PLANNED else ''
    if actor is not None and teaches(session.plan.module, actor):
        session.tutor = actor
    session.save()
    log(session.plan, actor, 'marked a class',
        f'{session.get_kind_display()} of {session.planned_on:%d %b}: {session.get_status_display()}')
    return session


@transaction.atomic
def edit_assessment(assessment, data, *, actor):
    if assessment.moderation in (PlanAssessment.SUBMITTED, PlanAssessment.APPROVED) and 'task_ids' in data:
        raise PlanError('Its setting plan has gone for moderation: ask for it back to change it.')
    for field in ('planned_on', 'held_on'):
        if field in data:
            setattr(assessment, field, data[field] or None)
    if 'title' in data and str(data['title']).strip():
        assessment.title = str(data['title']).strip()[:120]
    assessment.save()
    if 'task_ids' in data:
        assessment.tasks.set(_check_tasks(assessment.plan, data.get('task_ids')))
    log(assessment.plan, actor, 'updated an assessment', assessment.title)
    return assessment


@transaction.atomic
def moderate(assessment, action, *, note='', actor):
    """The tutor sends a test's setting plan; the Head of Department (DPARC
    without one) approves it or returns it with a reason."""
    if action == 'submit':
        if not assessment.tasks.exists() and assessment.plan.curriculum_module_id:
            raise PlanError('Tick the SEOs and tasks it covers first: that is its setting plan.')
        assessment.moderation = PlanAssessment.SUBMITTED
    elif action in ('approve', 'return'):
        if assessment.moderation != PlanAssessment.SUBMITTED:
            raise PlanError('It has not been sent for moderation.')
        if action == 'return' and not str(note).strip():
            raise PlanError('Say what to change.')
        assessment.moderation = PlanAssessment.APPROVED if action == 'approve' else PlanAssessment.RETURNED
        assessment.moderated_by, assessment.moderated_at = actor, timezone.now()
    else:
        raise PlanError('Unknown step.')
    assessment.moderation_note = str(note or '')[:500]
    assessment.save()
    log(assessment.plan, actor, f'moderation: {action}', assessment.title + (f' — {note}' if note else ''))
    return assessment


@transaction.atomic
def submit(plan, *, actor):
    if plan.status not in (TeachingPlan.DRAFT, TeachingPlan.RETURNED):
        raise PlanError('It has already been sent.')
    if not plan.sessions.exists():
        raise PlanError('Lay the classes out first.')
    plan.status, plan.submitted_by, plan.submitted_at = TeachingPlan.SUBMITTED, actor, timezone.now()
    plan.save()
    log(plan, actor, 'sent for approval')
    return plan


@transaction.atomic
def decide(plan, approve, *, note='', actor):
    if plan.status != TeachingPlan.SUBMITTED:
        raise PlanError('It is not waiting for approval.')
    if not approve and not str(note).strip():
        raise PlanError('Say what to change.')
    plan.status = TeachingPlan.APPROVED if approve else TeachingPlan.RETURNED
    plan.decided_by, plan.decided_at, plan.decision_note = actor, timezone.now(), str(note or '')[:500]
    plan.save()
    log(plan, actor, 'approved' if approve else 'returned', note)
    return plan


def add_note(plan, text, *, checkpoint='', actor):
    text = str(text or '').strip()
    if not text:
        raise PlanError('Write the note.')
    note = PlanNote.objects.create(plan=plan, author=actor, text=text[:4000], checkpoint=str(checkpoint)[:120])
    log(plan, actor, 'note', text[:120])
    return note


# ── how far it has got ────────────────────────────────────────────────────────

def _credit(session, day):
    if session.delivered_on is None or session.delivered_on > day:
        return 0
    return {PlanSession.DONE: 1, PlanSession.PARTIAL: 0.5}.get(session.status, 0)


def progress(plan, day=None, sessions=None):
    """Against the plan itself, on a day (today, or a CQI checkpoint)."""
    day = day or date.today()
    sessions = list(plan.sessions.all()) if sessions is None else sessions
    due = [s for s in sessions if s.planned_on <= day]
    done_due = sum(_credit(s, day) for s in due)
    done_all = sum(_credit(s, day) for s in sessions)
    hours_planned = sum((s.hours for s in sessions), Decimal(0))
    hours_done = sum((s.hours * Decimal(str(_credit(s, day))) for s in sessions), Decimal(0))
    behind = [s.planned_on for s in due if _credit(s, day) == 0]
    on_schedule = round(100 * done_due / len(due)) if due else None
    if not sessions:
        band = 'no_plan'
    elif not due:
        band = 'not_started'
    elif on_schedule >= ON_TRACK:
        band = 'on_track'
    elif on_schedule >= BEHIND:
        band = 'behind'
    else:
        band = 'far_behind'
    return {
        'as_of': day, 'sessions': len(sessions), 'due': len(due), 'done': done_all, 'done_due': done_due,
        'on_schedule': on_schedule, 'completion': round(100 * done_all / len(sessions)) if sessions else 0,
        'hours_planned': hours_planned, 'hours_done': hours_done,
        'weeks_behind': ((day - min(behind)).days // 7) if behind else 0,
        'missed': sum(1 for s in sessions if s.status == PlanSession.MISSED and s.planned_on <= day),
        'without_register': sum(1 for s in sessions if _credit(s, day) and s.register_id is None),
        'band': band,
    }


def week_strip(sessions, day=None):
    """The plan week by week, for the strip the screens draw (said 10 November
    2026): each teaching week's Monday, its classes in order with what became
    of each, and whether it is the week of `day`. A gap of more than a week
    between two weeks is the recess, or a break the calendar made."""
    day = day or date.today()
    weeks = {}
    for s in sorted(sessions, key=lambda s: (s.planned_on, s.order)):
        monday = s.planned_on - timedelta(days=s.planned_on.weekday())
        entry = weeks.setdefault(s.week, {'week': s.week, 'monday': monday, 'classes': []})
        entry['monday'] = min(entry['monday'], monday)
        state = s.status if _credit(s, day) or s.status == PlanSession.MISSED else (
            'due' if s.planned_on <= day else 'planned')
        entry['classes'].append(state)
    rows = sorted(weeks.values(), key=lambda w: w['monday'])
    out, before = [], None
    for w in rows:
        if before is not None and (w['monday'] - before).days > 7:
            out.append({'gap': True, 'from': before + timedelta(days=7), 'to': w['monday'] - timedelta(days=1)})
        w['current'] = w['monday'] <= day < w['monday'] + timedelta(days=7)
        out.append(w)
        before = w['monday']
    return out


def assessments_progress(plan, day=None):
    day = day or date.today()
    rows = list(plan.assessments.all())
    due = [a for a in rows if a.planned_on and a.planned_on <= day and a.stage != 'se']
    return {'total': len(rows), 'due': len(due), 'held': sum(1 for a in due if a.held_on),
            'late': sum(1 for a in due if not a.held_on),
            'moderated': sum(1 for a in rows if a.moderation == PlanAssessment.APPROVED)}


def coverage(plan):
    """What the assessment plan says each kind of assessment must be drawn
    from, against what this plan's assessments cover; and any task tested
    before the class that teaches it."""
    gaps = []
    cm = plan.curriculum_module
    assessments = list(plan.assessments.prefetch_related('tasks__outcome'))
    if cm is not None:
        for component in cm.components.prefetch_related('required_tasks__outcome'):
            required = set(component.required_tasks.all())
            if not required:
                continue
            covered = set()
            for a in assessments:
                if a.component_id == component.id:
                    covered.update(a.tasks.all())
            missing = sorted(required - covered, key=lambda t: (t.outcome.order, t.order))
            gaps.append({'component': component.label, 'stage': component.stage, 'required': len(required),
                         'covered': len(required & covered),
                         'missing': [f'{t.outcome.code} {t.letter}' for t in missing]})
    sessions = list(plan.sessions.prefetch_related('tasks'))
    first_taught = {}
    for s in sessions:
        when = s.delivered_on or s.planned_on
        keys = {('task', t.id) for t in s.tasks.all()} or ({('outcome', s.outcome_id)} if s.outcome_id else set())
        for key in keys:
            if key not in first_taught or when < first_taught[key]:
                first_taught[key] = when
    early = []
    for a in assessments:
        when = a.held_on or a.planned_on
        if when is None or a.stage == 'se':
            continue
        for task in a.tasks.all():
            taught = first_taught.get(('task', task.id)) or first_taught.get(('outcome', task.outcome_id))
            if taught is None or taught > when:
                early.append({'assessment': a.title, 'task': f'{task.outcome.code} {task.letter}',
                              'taught': taught})
    return {'components': gaps, 'tested_before_taught': early}


# ── what screens show ─────────────────────────────────────────────────────────

_NAMES = __import__('contextvars').ContextVar('teaching_plan_names', default=None)


def _name(user):
    """A person's name. Finding it asks every office in turn, so a screen
    of sixty classes remembers each name once (names_remembered)."""
    from .views import full_name_for         # views imports half the app; only when needed
    if user is None:
        return ''
    known = _NAMES.get()
    if known is None:
        return full_name_for(user)
    if user.pk not in known:
        known[user.pk] = full_name_for(user)
    return known[user.pk]


class names_remembered:
    def __enter__(self):
        self.token = _NAMES.set({})

    def __exit__(self, *exc):
        _NAMES.reset(self.token)


def plan_summary(module, plan, day):
    tutors = [_name(u) for u in module.teachers.all()]
    row = {'module_id': module.id, 'code': module.code, 'name': module.name, 'level': module.class_level.name,
           'programme': module.programme.code if module.programme_id else '',
           'department': (module.programme.department.name if module.programme_id else ''),
           'tutors': tutors, 'credits': module.credits}
    if plan is None:
        return {**row, 'plan_id': None, 'status': 'none', 'status_display': 'No plan yet',
                'progress': {'band': 'no_plan'}}
    sessions = list(plan.sessions.all())
    return {**row, 'plan_id': plan.id, 'status': plan.status, 'status_display': plan.get_status_display(),
            'curriculum': plan.curriculum_module.curriculum.code if plan.curriculum_module_id else '',
            'has_document': bool(plan.document), 'progress': progress(plan, day, sessions),
            'strip': week_strip(sessions, day),
            'assessments': assessments_progress(plan, day), 'updated_at': plan.updated_at}


def overview(user, semester, day=None, scope='all'):
    with names_remembered():
        return _overview(user, semester, day, scope)


def _overview(user, semester, day=None, scope='all'):
    """Every module of the semester the account may see, with or without a
    plan: what the Head of Department, DPARC, the Principal and QA follow."""
    day = day or date.today()
    modules = (visible_modules(user).filter(semester=semester)
               .select_related('class_level', 'programme__department')
               .prefetch_related('teachers').order_by('class_level__order', 'code'))
    if scope == 'mine':
        modules = modules.filter(teachers=user)
    elif scope == 'approve':
        modules = [m for m in modules if may_approve(m, user)]
    plans = {p.module_id: p for p in TeachingPlan.objects.filter(module__in=[m.id for m in modules])
             .select_related('curriculum_module__curriculum').prefetch_related('sessions', 'assessments')}
    rows = [plan_summary(m, plans.get(m.id), day) for m in modules]
    counts = {}
    for row in rows:
        counts[row['progress']['band']] = counts.get(row['progress']['band'], 0) + 1
    return {'as_of': day, 'rows': rows, 'counts': counts,
            'checkpoints': [{'title': e.title, 'on': e.starts_on}
                            for e in events_of(semester, CalendarEvent.CQI)]}


def outcome_rows(cm):
    if cm is None:
        return []
    return [{'id': o.id, 'code': o.code, 'text': o.text, 'enabling': o.enabling_code,
             'enabling_text': o.enabling_text, 'criteria': o.criteria,
             'tasks': [{'id': t.id, 'letter': t.letter, 'text': t.text} for t in o.tasks.all()]}
            for o in cm.outcomes.prefetch_related('tasks')]


def plan_detail(plan, user, day=None):
    with names_remembered():
        return _plan_detail(plan, user, day)


def _plan_detail(plan, user, day=None):
    day = day or date.today()
    module = plan.module
    cm = plan.curriculum_module
    calendar = calendar_for(module.semester)
    sessions = list(plan.sessions.select_related('outcome', 'tutor').prefetch_related('tasks'))
    mine = teaches(module, user)
    return {
        'id': plan.id, 'status': plan.status, 'status_display': plan.get_status_display(),
        'module': {'id': module.id, 'code': module.code, 'name': module.name, 'level': module.class_level.name,
                   'semester': str(module.semester), 'credits': module.credits,
                   'tutors': [_name(u) for u in module.teachers.all()]},
        'curriculum': ({'id': cm.id, 'code': cm.code, 'name': cm.name, 'curriculum': cm.curriculum.name,
                        'credits': cm.credits, 'hours': {'L': cm.lecture_hours, 'T': cm.tutorial_hours,
                                                         'P': cm.practical_hours, 'A': cm.assignment_hours},
                        'weeks_of_study': cm.curriculum.weeks_of_study, 'checks': cm.checks}
                       if cm else None),
        'calendar': ({'teaching_starts': calendar.teaching_starts, 'teaching_ends': calendar.teaching_ends}
                     if calendar else None),
        'today': day,
        'strip': week_strip(sessions, day),
        'events': [{'kind': e.kind, 'title': e.title, 'starts_on': e.starts_on, 'ends_on': e.ends_on}
                   for e in events_of(module.semester, CalendarEvent.CAT, CalendarEvent.SPECIAL_CAT,
                                      CalendarEvent.EXAM, CalendarEvent.DEADLINE, CalendarEvent.RECESS,
                                      CalendarEvent.CQI)],
        'pattern': plan.pattern, 'weeks': plan.weeks, 'starts_on': plan.starts_on,
        'weekly_hours': pattern_hours(plan.pattern or []),
        'outcomes': outcome_rows(cm),
        'sessions': [{'id': s.id, 'week': s.week, 'planned_on': s.planned_on, 'kind': s.kind,
                      'kind_display': s.get_kind_display(), 'hours': s.hours,
                      'outcome_id': s.outcome_id, 'outcome': s.outcome.code if s.outcome_id else '',
                      'outcome_text': s.outcome.text if s.outcome_id else '',
                      'task_ids': [t.id for t in s.tasks.all()], 'topic': s.topic,
                      'tutor': _name(s.tutor) if s.tutor_id else '', 'status': s.status,
                      'status_display': s.get_status_display(), 'delivered_on': s.delivered_on,
                      'register': bool(s.register_id), 'note': s.note, 'moved_from': s.moved_from,
                      'move_reason': s.move_reason, 'due': s.planned_on <= day} for s in sessions],
        'assessments': [{'id': a.id, 'stage': a.stage, 'method': a.method, 'method_display': a.get_method_display(),
                         'number': a.number, 'title': a.title, 'weight': a.weight, 'planned_on': a.planned_on,
                         'held_on': a.held_on, 'task_ids': [t.id for t in a.tasks.all()],
                         'component_id': a.component_id, 'moderation': a.moderation,
                         'moderation_display': a.get_moderation_display(), 'moderation_note': a.moderation_note,
                         'moderated_by': _name(a.moderated_by) if a.moderated_by_id else ''}
                        for a in plan.assessments.prefetch_related('tasks').select_related('moderated_by')],
        'scheme': [{'stage': p['stage'], 'label': p['label'], 'count': p['count'], 'weight': p['weight'],
                    'tool': getattr(p.get('component'), 'tool', ''),
                    'component_id': getattr(p.get('component'), 'id', None)} for p in scheme_for(plan)],
        'scheme_source': 'assessment plan' if cm is not None and cm.components.exists() else 'results today',
        'progress': progress(plan, day, sessions), 'assessment_progress': assessments_progress(plan, day),
        'coverage': coverage(plan),
        'document': ({'name': plan.document_name or plan.document.name.rsplit('/', 1)[-1]} if plan.document else None),
        'decision': {'by': _name(plan.decided_by) if plan.decided_by_id else '', 'at': plan.decided_at,
                     'note': plan.decision_note},
        'notes': [{'id': n.id, 'text': n.text, 'checkpoint': n.checkpoint, 'author': _name(n.author),
                   'at': n.written_at} for n in plan.notes.select_related('author')],
        'history': [{'action': e.action, 'detail': e.detail, 'by': _name(e.actor), 'at': e.at}
                    for e in plan.history.select_related('actor')[:200]],
        'can': {'edit': mine and plan.status in (TeachingPlan.DRAFT, TeachingPlan.RETURNED),
                'follow': mine, 'approve': may_approve(module, user) and plan.status == TeachingPlan.SUBMITTED,
                'moderate': may_approve(module, user), 'note': may_read(plan, user),
                'relink': is_dparc(user) or is_break_glass(user) or (is_head_of_department(user)
                                                                      and approver_of(module) == user)},
        'weekdays': WEEKDAYS, 'kinds': PlanSession.KIND_CHOICES,
    }
