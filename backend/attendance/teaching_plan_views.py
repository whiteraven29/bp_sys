"""Teaching plans, the teaching calendar and the curricula behind them
(said 8–9 November 2026). The rules are in teaching_plans.py."""

from datetime import date

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from . import teaching_plans as tp
from .models import (
    CalendarEvent, Curriculum, CurriculumModule, Module, PlanAssessment, PlanSession, Semester, TeachingCalendar,
    TeachingPlan,
)
from .roles import is_break_glass, is_dparc, is_head_of_department
from .views import _a_date, active_semester, pk_or_none

DOCUMENT_TYPES = {'pdf': b'%PDF', 'docx': b'PK', 'doc': b'\xd0\xcf\x11\xe0'}
DOCUMENT_MAX_BYTES = 15 * 1024 * 1024


def _bad(message):
    return Response({'detail': str(message)}, status=status.HTTP_400_BAD_REQUEST)


def _staff(user):
    """Anyone who has a part in teaching plans."""
    return bool(user and user.is_authenticated and (
        user.modules_taught.exists() or tp.sees_every_plan(user) or is_head_of_department(user)))


def _semester(params):
    return Semester.objects.filter(id=pk_or_none(params.get('semester_id'))).first() or active_semester()


def _plan(user, pk):
    plan = get_object_or_404(TeachingPlan.objects.select_related(
        'module__semester', 'module__class_level', 'module__programme__department',
        'curriculum_module__curriculum'), pk=pk)
    if not tp.may_read(plan, user):
        raise PermissionDenied('Not a plan you follow.')
    return plan


def _tutor(plan, user):
    if not tp.teaches(plan.module, user):
        raise PermissionDenied("Only the module's tutors change its plan.")


def _detail(plan, user):
    return Response(tp.plan_detail(plan, user))


# ── plans ─────────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
def plans(request):
    """GET: the semester's modules and their plans — ?scope=mine (my plans),
    approve (waiting on me), all (what I follow) — on a day (?as_of=). POST:
    start the plan of a module I teach."""
    user = request.user
    if not _staff(user):
        raise PermissionDenied('Teaching plans are for tutors and the academic offices.')
    if request.method == 'POST':
        module = get_object_or_404(Module, pk=pk_or_none(request.data.get('module_id')))
        if not tp.teaches(module, user):
            raise PermissionDenied('A plan is made by one of the tutors of the module.')
        plan = tp.start_plan(module, user)
        return Response(tp.plan_detail(plan, user), status=status.HTTP_201_CREATED)
    semester = _semester(request.query_params)
    if semester is None:
        return _bad('No semester is open.')
    scope = request.query_params.get('scope', 'all')
    day = _a_date(request.query_params.get('as_of')) or date.today()
    return Response({**tp.overview(user, semester, day, scope=scope), 'semester': str(semester),
                     'semester_id': semester.id,
                     'can': {'calendar': is_dparc(user) or is_break_glass(user)}})


@api_view(['GET', 'PATCH'])
def plan(request, pk):
    """GET: the whole plan. PATCH: which curriculum module it follows — for the
    Head of Department or DPARC to put right a wrong match."""
    found = _plan(request.user, pk)
    if request.method == 'PATCH':
        allowed = is_dparc(request.user) or is_break_glass(request.user) or (
            tp.approver_of(found.module) is not None and tp.approver_of(found.module).pk == request.user.pk)
        if not allowed:
            raise PermissionDenied('The Head of Department or DPARC sets which curriculum a module follows.')
        chosen = request.data.get('curriculum_module_id')
        found.curriculum_module = (CurriculumModule.objects.filter(pk=pk_or_none(chosen)).first()
                                   if chosen not in (None, '') else None)
        found.save(update_fields=['curriculum_module', 'updated_at'])
        tp.log(found, request.user, 'curriculum set',
               str(found.curriculum_module) if found.curriculum_module_id else 'none')
    return _detail(found, request.user)


@api_view(['POST'])
def lay_out(request, pk):
    found = _plan(request.user, pk)
    _tutor(found, request.user)
    try:
        tp.lay_out(found, request.data.get('pattern'), actor=request.user, weeks=request.data.get('weeks'),
                   starts_on=_a_date(request.data.get('starts_on')),
                   assessments=request.data.get('assessments', True) is not False)
    except (tp.PlanError, ValueError) as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['POST'])
def submit(request, pk):
    found = _plan(request.user, pk)
    _tutor(found, request.user)
    try:
        tp.submit(found, actor=request.user)
    except tp.PlanError as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['POST'])
def decide(request, pk):
    found = _plan(request.user, pk)
    if not tp.may_approve(found.module, request.user):
        raise PermissionDenied("The Head of Department approves (DPARC where there is none), never one's own.")
    try:
        tp.decide(found, bool(request.data.get('approve')), note=request.data.get('note', ''), actor=request.user)
    except tp.PlanError as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['POST'])
def add_session(request, pk):
    found = _plan(request.user, pk)
    _tutor(found, request.user)
    try:
        tp.add_session(found, {**request.data, 'planned_on': _a_date(request.data.get('planned_on'))},
                       actor=request.user)
    except (tp.PlanError, ValueError) as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['PATCH', 'DELETE'])
def session(request, pk, session_id):
    found = _plan(request.user, pk)
    _tutor(found, request.user)
    item = get_object_or_404(PlanSession, pk=session_id, plan=found)
    if request.method == 'DELETE':
        if found.status != TeachingPlan.DRAFT or item.status != PlanSession.PLANNED:
            return _bad('Only a planned class of a plan still being prepared is taken out; mark it missed instead.')
        item.delete()
        tp.log(found, request.user, 'took out a class', f'{item.get_kind_display()} of {item.planned_on:%d %b}')
        return _detail(found, request.user)
    data = dict(request.data)
    if 'planned_on' in data:
        data['planned_on'] = _a_date(data['planned_on'])
    try:
        tp.edit_session(item, data, actor=request.user)
    except (tp.PlanError, ValueError, ArithmeticError) as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['POST'])
def mark_session(request, pk, session_id):
    found = _plan(request.user, pk)
    _tutor(found, request.user)
    item = get_object_or_404(PlanSession, pk=session_id, plan=found)
    try:
        tp.mark(item, str(request.data.get('status') or ''), delivered_on=_a_date(request.data.get('delivered_on')),
                note=request.data.get('note', ''), actor=request.user)
    except tp.PlanError as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['PATCH'])
def assessment(request, pk, assessment_id):
    found = _plan(request.user, pk)
    _tutor(found, request.user)
    item = get_object_or_404(PlanAssessment, pk=assessment_id, plan=found)
    data = dict(request.data)
    for field in ('planned_on', 'held_on'):
        if field in data:
            data[field] = _a_date(data[field])
    try:
        tp.edit_assessment(item, data, actor=request.user)
    except tp.PlanError as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['POST'])
def moderate(request, pk, assessment_id):
    """action=submit by the tutor; approve or return by the Head of Department."""
    found = _plan(request.user, pk)
    item = get_object_or_404(PlanAssessment, pk=assessment_id, plan=found)
    action = str(request.data.get('action') or '')
    if action == 'submit':
        _tutor(found, request.user)
    elif not tp.may_approve(found.module, request.user):
        raise PermissionDenied('The Head of Department moderates (DPARC where there is none).')
    try:
        tp.moderate(item, action, note=request.data.get('note', ''), actor=request.user)
    except tp.PlanError as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['POST'])
def note(request, pk):
    found = _plan(request.user, pk)
    try:
        tp.add_note(found, request.data.get('text'), checkpoint=request.data.get('checkpoint', ''),
                    actor=request.user)
    except tp.PlanError as exc:
        return _bad(exc)
    return _detail(found, request.user)


@api_view(['GET', 'POST'])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def document(request, pk):
    """The tutor's own Word or PDF plan, kept as the reference copy: uploaded
    by a tutor of the module, read by whoever reads the plan."""
    found = _plan(request.user, pk)
    if request.method == 'POST':
        _tutor(found, request.user)
        upload = request.FILES.get('file')
        if upload is None:
            return _bad('Choose the file.')
        ext = upload.name.rsplit('.', 1)[-1].lower() if '.' in upload.name else ''
        if ext not in DOCUMENT_TYPES:
            return _bad('A teaching plan is a PDF or a Word document.')
        if upload.size > DOCUMENT_MAX_BYTES:
            return _bad('The file must be 15 MB or smaller.')
        head = upload.read(8)
        upload.seek(0)
        if not head.startswith(DOCUMENT_TYPES[ext]):
            return _bad(f'That file is not a real {ext.upper()}.')
        if found.document:
            found.document.delete(save=False)
        found.document = upload
        found.document_name = upload.name[:200]
        found.save(update_fields=['document', 'document_name', 'updated_at'])
        tp.log(found, request.user, 'uploaded the document', found.document_name)
        return _detail(found, request.user)
    if not found.document:
        raise Http404
    name = found.document_name or found.document.name.rsplit('/', 1)[-1]
    kind = {'pdf': 'application/pdf',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'doc': 'application/msword'}.get(name.rsplit('.', 1)[-1].lower(), 'application/octet-stream')
    response = FileResponse(found.document.open('rb'), as_attachment=True, filename=name, content_type=kind)
    response['Content-Security-Policy'] = 'sandbox'
    return response


# ── the teaching calendar ─────────────────────────────────────────────────────

def _calendar_payload(semester, user):
    calendar = TeachingCalendar.objects.filter(semester=semester).first()
    return {
        'semester': str(semester), 'semester_id': semester.id,
        'semesters': [{'id': s.id, 'label': str(s)} for s in Semester.objects.select_related('academic_year')
                      .order_by('-academic_year__name', '-number')[:6]],
        'calendar': ({'teaching_starts': calendar.teaching_starts, 'teaching_ends': calendar.teaching_ends,
                      'note': calendar.note} if calendar else None),
        'events': [{'id': e.id, 'kind': e.kind, 'kind_display': e.get_kind_display(), 'title': e.title,
                    'starts_on': e.starts_on, 'ends_on': e.ends_on, 'note': e.note,
                    'no_teaching': e.kind in CalendarEvent.NO_TEACHING}
                   for e in CalendarEvent.objects.filter(semester=semester)],
        'kinds': CalendarEvent.KIND_CHOICES,
        'can_edit': is_dparc(user) or is_break_glass(user),
    }


@api_view(['GET', 'POST'])
def calendar(request):
    """GET: a semester's teaching weeks and dates, for everyone with plans.
    POST: DPARC sets when teaching starts and ends."""
    if not _staff(request.user):
        raise PermissionDenied('For tutors and the academic offices.')
    semester = _semester(request.data if request.method == 'POST' else request.query_params)
    if semester is None:
        return _bad('No semester is open.')
    if request.method == 'POST':
        if not (is_dparc(request.user) or is_break_glass(request.user)):
            raise PermissionDenied('DPARC keeps the teaching calendar.')
        starts, ends = _a_date(request.data.get('teaching_starts')), _a_date(request.data.get('teaching_ends'))
        if not starts or not ends or ends <= starts:
            return _bad('Give when teaching starts and when it ends, the end after the start.')
        TeachingCalendar.objects.update_or_create(semester=semester, defaults={
            'teaching_starts': starts, 'teaching_ends': ends,
            'note': str(request.data.get('note') or '')[:300], 'updated_by': request.user})
    return Response(_calendar_payload(semester, request.user))


@api_view(['POST', 'DELETE'])
def calendar_event(request, event_id=None):
    if not (is_dparc(request.user) or is_break_glass(request.user)):
        raise PermissionDenied('DPARC keeps the teaching calendar.')
    if request.method == 'DELETE':
        event = get_object_or_404(CalendarEvent, pk=event_id)
        semester = event.semester
        event.delete()
        return Response(_calendar_payload(semester, request.user))
    semester = _semester(request.data)
    if semester is None:
        return _bad('No semester is open.')
    kind = str(request.data.get('kind') or '')
    starts = _a_date(request.data.get('starts_on'))
    ends = _a_date(request.data.get('ends_on')) or starts
    title = str(request.data.get('title') or '').strip()
    if kind not in dict(CalendarEvent.KIND_CHOICES) or not starts or not title or ends < starts:
        return _bad('Give the kind, a title and the dates (the end not before the start).')
    CalendarEvent.objects.create(semester=semester, kind=kind, title=title[:200], starts_on=starts, ends_on=ends,
                                 note=str(request.data.get('note') or '')[:300])
    return Response(_calendar_payload(semester, request.user))


# ── the curricula ─────────────────────────────────────────────────────────────

@api_view(['GET'])
def curricula(request):
    """Every curriculum module loaded, by level and semester, with what the
    national documents disagree on."""
    if not _staff(request.user):
        raise PermissionDenied('For tutors and the academic offices.')
    return Response([{
        'id': c.id, 'code': c.code, 'name': c.name, 'note': c.note, 'weeks_of_study': c.weeks_of_study,
        'modules': [{'id': m.id, 'code': m.code, 'name': m.name, 'level': m.level, 'semester': m.semester,
                     'credits': m.credits, 'hours': {'L': m.lecture_hours, 'T': m.tutorial_hours,
                                                     'P': m.practical_hours, 'A': m.assignment_hours},
                     'outcomes': m.outcomes.count(), 'checks': m.checks}
                    for m in c.modules.all()],
    } for c in Curriculum.objects.prefetch_related('modules')])


@api_view(['GET'])
def curriculum_module(request, pk):
    if not _staff(request.user):
        raise PermissionDenied('For tutors and the academic offices.')
    m = get_object_or_404(CurriculumModule.objects.select_related('curriculum'), pk=pk)
    return Response({
        'id': m.id, 'code': m.code, 'name': m.name, 'level': m.level, 'semester': m.semester,
        'credits': m.credits, 'curriculum': m.curriculum.name, 'checks': m.checks,
        'hours': {'L': m.lecture_hours, 'T': m.tutorial_hours, 'P': m.practical_hours, 'A': m.assignment_hours},
        'outcomes': tp.outcome_rows(m),
        'scheme': [{'stage': c.stage, 'method': c.get_method_display(), 'label': c.label, 'tool': c.tool,
                    'count': c.min_count, 'weight': c.weight, 'unplaced': c.unplaced,
                    'required': [f'{t.outcome.code} {t.letter}' for t in c.required_tasks.select_related('outcome')]}
                   for c in m.components.all()],
    })

