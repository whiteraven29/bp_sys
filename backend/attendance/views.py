from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .forms import TeacherRegistrationForm, StyledAuthForm
from .models import (
    AcademicYear, Semester, ClassLevel, Module,
    Student, Session, AttendanceRecord, TeacherProfile, StudentResult,
)
from .serializers import (
    AcademicYearSerializer, SemesterSerializer, ClassLevelSerializer,
    ModuleSerializer, StudentSerializer,
    SessionSerializer, SessionCreateSerializer, BulkStudentSerializer,
    StudentResultSerializer,
)


# ── HELPERS ────────────────────────────────────────────────────────────────────

def user_modules(user):
    if user.is_staff:
        return Module.objects.all()
    return user.modules_taught.all()


def active_semester():
    return Semester.objects.filter(is_active=True).select_related('academic_year').first()


def _make_both_semesters(year):
    """Ensure Semester 1 and 2 both exist for a given AcademicYear."""
    for num in (1, 2):
        Semester.objects.get_or_create(
            academic_year=year, number=num,
            defaults={'is_active': False},
        )


# ── AUTH VIEWS ─────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('frontend')
    form = StyledAuthForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect(request.GET.get('next', 'frontend'))
    return render(request, 'login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


def register_view(request):
    if request.user.is_authenticated:
        return redirect('frontend')
    levels = ClassLevel.objects.prefetch_related('modules__semester__academic_year').all()
    form = TeacherRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        full_name = form.cleaned_data['full_name'].strip()
        parts = full_name.split(None, 1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ''
        user.save()
        TeacherProfile.objects.create(user=user, full_name=full_name)
        for mid in request.POST.getlist('modules'):
            try:
                Module.objects.get(id=mid).teachers.add(user)
            except Module.DoesNotExist:
                pass
        login(request, user)
        return redirect('frontend')
    return render(request, 'register.html', {'form': form, 'levels': levels})


# ── ACADEMIC YEAR ──────────────────────────────────────────────────────────────

class AcademicYearViewSet(viewsets.ModelViewSet):
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='advance')
    def advance(self, request):
        """
        Advance the active semester:
          Semester 1 → Semester 2 (same academic year)
          Semester 2 → Semester 1 of the next academic year
        Staff only.
        """
        if not request.user.is_staff:
            return Response({'detail': 'Staff only.'}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            cur = Semester.objects.select_for_update().filter(is_active=True).first()
            if not cur:
                return Response({'detail': 'No active semester found.'}, status=status.HTTP_400_BAD_REQUEST)

            cur.is_active = False
            cur.save()

            if cur.number == 1:
                new_sem, _ = Semester.objects.get_or_create(
                    academic_year=cur.academic_year, number=2,
                    defaults={'is_active': True}
                )
                new_sem.is_active = True
                new_sem.save()
                new_year = cur.academic_year
            else:
                cur.academic_year.is_active = False
                cur.academic_year.save()
                new_year, _ = AcademicYear.objects.get_or_create(
                    name=cur.academic_year.next_name,
                    defaults={'is_active': True}
                )
                new_year.is_active = True
                new_year.save()
                # Ensure both semesters exist for the new year
                _make_both_semesters(new_year)
                new_sem = Semester.objects.get(academic_year=new_year, number=1)
                new_sem.is_active = True
                new_sem.save()

        return Response({
            'detail': f'Advanced to {new_sem}',
            'year': new_year.name,
            'semester': new_sem.number,
            'label': new_sem.label,
        })

    @action(detail=False, methods=['get'], url_path='active')
    def active(self, request):
        sem = active_semester()
        if not sem:
            return Response(None)
        return Response(SemesterSerializer(sem).data)


class SemesterViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SemesterSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Semester.objects.select_related('academic_year').all()
        if self.request.query_params.get('is_active') == 'true':
            qs = qs.filter(is_active=True)
        year_id = self.request.query_params.get('year_id')
        if year_id:
            qs = qs.filter(academic_year_id=year_id)
        return qs

    @action(detail=True, methods=['patch'], url_path='cutoffs')
    def update_cutoffs(self, request, pk=None):
        """Admin-only: set the attendance cutoff dates for CAT1, CAT2 and end of semester."""
        if not request.user.is_staff:
            raise PermissionDenied('Only the administrator can set cutoff dates.')
        semester = self.get_object()
        for field in ('cat1_cutoff', 'cat2_cutoff', 'end_cutoff'):
            if field in request.data:
                setattr(semester, field, request.data[field] or None)
        semester.save()
        return Response(SemesterSerializer(semester).data)


# ── CLASS LEVEL ────────────────────────────────────────────────────────────────

class ClassLevelViewSet(viewsets.ModelViewSet):
    queryset = ClassLevel.objects.all()
    serializer_class = ClassLevelSerializer
    permission_classes = [IsAuthenticated]


# ── MODULES ────────────────────────────────────────────────────────────────────

class ModuleViewSet(viewsets.ModelViewSet):
    serializer_class = ModuleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = user_modules(self.request.user).select_related('class_level', 'semester__academic_year')
        for param, field in [
            ('class_level_id', 'class_level_id'),
            ('semester_id', 'semester_id'),
        ]:
            val = self.request.query_params.get(param)
            if val:
                qs = qs.filter(**{field: val})
        return qs

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Only the administrator can create modules.')
        module = serializer.save()
        module.teachers.add(self.request.user)

    def update(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied('Only the administrator can edit modules.')
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied('Only the administrator can delete modules.')
        m = self.get_object()
        name = m.name
        m.delete()
        return Response({'detail': f'Module "{name}" deleted.'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='claim')
    def claim(self, request, pk=None):
        try:
            m = Module.objects.get(pk=pk)
        except Module.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        m.teachers.add(request.user)
        return Response({'detail': f'Claimed: {m.name}'})

    @action(detail=True, methods=['post'], url_path='unclaim')
    def unclaim(self, request, pk=None):
        try:
            m = Module.objects.get(pk=pk)
        except Module.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        m.teachers.remove(request.user)
        return Response({'detail': f'Unclaimed: {m.name}'})


# ── STUDENTS ───────────────────────────────────────────────────────────────────

class StudentViewSet(viewsets.ModelViewSet):
    serializer_class = StudentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Student.objects.filter(
            module__in=user_modules(self.request.user)
        ).select_related('module__class_level', 'module__semester__academic_year').prefetch_related('attendance_records')

        for param, field in [
            ('module_id', 'module_id'),
            ('class_level_id', 'module__class_level_id'),
            ('semester_id', 'module__semester_id'),
        ]:
            val = self.request.query_params.get(param)
            if val:
                qs = qs.filter(**{field: val})
        return qs

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Only the administrator can add students.')
        serializer.save()

    def update(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied('Only the administrator can edit students.')
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied('Only the administrator can remove students.')
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['post'], url_path='bulk_create')
    def bulk_create(self, request):
        if not request.user.is_staff:
            raise PermissionDenied('Only the administrator can add students.')
        serializer = BulkStudentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(result, status=status.HTTP_201_CREATED)


# ── SESSIONS ───────────────────────────────────────────────────────────────────

class SessionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Session.objects.filter(
            module__in=user_modules(self.request.user)
        ).select_related('module__class_level', 'module__semester__academic_year').prefetch_related('records__student')

        for param, field in [
            ('module_id', 'module_id'),
            ('class_level_id', 'module__class_level_id'),
            ('semester_id', 'module__semester_id'),
        ]:
            val = self.request.query_params.get(param)
            if val:
                qs = qs.filter(**{field: val})
        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return SessionCreateSerializer
        return SessionSerializer

    def perform_create(self, serializer):
        """Reject new sessions whose exam period is past the semester cutoff date."""
        module      = serializer.validated_data['module']
        session_date = serializer.validated_data['date']
        period      = serializer.validated_data.get('exam_period', Session.GENERAL)
        semester    = module.semester
        today       = timezone.localdate()

        CUTOFF_MAP = {
            Session.CAT1:    ('cat1_cutoff', 'CAT 1'),
            Session.CAT2:    ('cat2_cutoff', 'CAT 2'),
            Session.GENERAL: ('end_cutoff',  'End of Semester'),
        }
        cutoff_field, period_label = CUTOFF_MAP.get(period, ('end_cutoff', 'this period'))
        cutoff = getattr(semester, cutoff_field, None)

        if cutoff and today > cutoff:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(
                f'Attendance is closed for {period_label}. '
                f'The cutoff date was {cutoff.strftime("%d %b %Y")}.'
            )

        serializer.save()


# ── DASHBOARD ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@login_required
def dashboard(request):
    today = timezone.localdate()
    my_modules = user_modules(request.user)
    sem = active_semester()

    subjects_count = my_modules.count()
    students_count = Student.objects.filter(module__in=my_modules).count()
    sessions_today = Session.objects.filter(module__in=my_modules, date=today).count()

    all_students = (
        Student.objects.filter(module__in=my_modules)
        .prefetch_related('attendance_records').select_related('module')
    )

    # Cache session counts per module to avoid N+1
    _session_cache = {}

    def _held(module_id):
        if module_id not in _session_cache:
            _session_cache[module_id] = Session.objects.filter(module_id=module_id).count()
        return _session_cache[module_id]

    total_pct, count = 0, 0
    for st in all_students:
        held = _held(st.module_id)
        if held:
            effective = st.attendance_records.filter(status__in=['P', 'S']).count()
            total_pct += round((effective / held) * 100)
            count += 1
    avg_attendance = round(total_pct / count) if count else None

    recent = []
    for sess in (
        Session.objects.filter(module__in=my_modules)
        .select_related('module__class_level', 'module__semester__academic_year')
        .prefetch_related('records').order_by('-date', '-created_at')[:8]
    ):
        p = sess.records.filter(status='P').count()
        s = sess.records.filter(status='S').count()
        a = sess.records.filter(status='A').count()
        t = p + s + a
        recent.append({
            'id': sess.id,
            'module': sess.module.name,
            'session_type': sess.session_type,
            'session_type_display': sess.get_session_type_display(),
            'class_level': sess.module.class_level.name,
            'semester': sess.module.semester.label,
            'date': str(sess.date),
            'label': sess.label,
            'topic': sess.topic,
            'present': p, 'sick': s, 'absent': a,
            'pct': round(((p + s) / t) * 100) if t else 0,
        })

    levels = []
    for lvl in ClassLevel.objects.all():
        lvl_mods = my_modules.filter(class_level=lvl)
        if not lvl_mods.exists():
            continue
        lvl_students = all_students.filter(module__in=lvl_mods)
        lp, lc = 0, 0
        for st in lvl_students:
            held = _held(st.module_id)
            if held:
                effective = st.attendance_records.filter(status__in=['P', 'S']).count()
                lp += round((effective / held) * 100)
                lc += 1
        levels.append({
            'id': lvl.id,
            'name': lvl.name,
            'modules': lvl_mods.count(),
            'students': lvl_students.count(),
            'avg_pct': round(lp / lc) if lc else None,
        })

    return Response({
        'modules': subjects_count,
        'students': students_count,
        'sessions_today': sessions_today,
        'avg_attendance': avg_attendance,
        'active_semester': SemesterSerializer(sem).data if sem else None,
        'recent_sessions': recent,
        'levels': levels,
        'is_staff': request.user.is_staff,
    })


# ── REPORT ─────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@login_required
def report(request):
    my_modules = user_modules(request.user)
    module_id = request.query_params.get('module_id')
    class_level_id = request.query_params.get('class_level_id')
    semester_id = request.query_params.get('semester_id')

    students = (
        Student.objects.filter(module__in=my_modules)
        .select_related('module__class_level', 'module__semester__academic_year')
        .prefetch_related('attendance_records__session')
    )
    if module_id:
        students = students.filter(module_id=module_id)
    if class_level_id:
        students = students.filter(module__class_level_id=class_level_id)
    if semester_id:
        students = students.filter(module__semester_id=semester_id)

    # Cache session counts per module {module_id: {'T': n, 'P': n, 'total': n}}
    _mod_cache = {}

    def _mcounts(mid):
        if mid not in _mod_cache:
            t = Session.objects.filter(module_id=mid, session_type=Session.THEORY).count()
            p = Session.objects.filter(module_id=mid, session_type=Session.PRACTICAL).count()
            _mod_cache[mid] = {'T': t, 'P': p, 'total': t + p}
        return _mod_cache[mid]

    rows = []
    total_eff, total_held, at_risk, critical, sick_total = 0, 0, 0, 0, 0

    for st in students:
        mc = _mcounts(st.module_id)
        sessions_held = mc['total']

        # Use prefetched attendance_records__session
        all_records = list(st.attendance_records.all())
        attended = sum(1 for r in all_records if r.status == 'P')
        sick = sum(1 for r in all_records if r.status == 'S')
        effective = attended + sick
        absent = max(sessions_held - effective, 0)

        # Theory / Practical breakdown
        theory_eff = sum(1 for r in all_records if r.session.session_type == Session.THEORY and r.status in ('P', 'S'))
        practical_eff = sum(1 for r in all_records if r.session.session_type == Session.PRACTICAL and r.status in ('P', 'S'))

        pct = round((effective / sessions_held) * 100) if sessions_held else 0
        theory_pct = round((theory_eff / mc['T']) * 100) if mc['T'] else None
        practical_pct = round((practical_eff / mc['P']) * 100) if mc['P'] else None

        total_eff += effective
        total_held += sessions_held
        sick_total += sick
        if 50 <= pct < 75:
            at_risk += 1
        elif pct < 50:
            critical += 1

        rows.append({
            'nactvet_reg_no': st.nactvet_reg_no,
            'name': st.name,
            'module': st.module.name,
            'module_code': st.module.code,
            'class_level': st.module.class_level.name,
            'semester': st.module.semester.label,
            'teacher': st.module.teacher,
            'sessions_held': sessions_held,
            'theory_held': mc['T'],
            'practical_held': mc['P'],
            'attended': attended,
            'sick': sick,
            'absent': absent,
            'theory_eff': theory_eff,
            'practical_eff': practical_eff,
            'pct': pct,
            'theory_pct': theory_pct,
            'practical_pct': practical_pct,
            'status': 'Good' if pct >= 75 else ('At Risk' if pct >= 50 else 'Critical'),
        })

    avg_pct = round((total_eff / total_held) * 100) if total_held else 0

    sessions_qs = (
        Session.objects.filter(module__in=my_modules)
        .select_related('module__class_level', 'module__semester__academic_year')
        .prefetch_related('records')
    )
    if module_id:
        sessions_qs = sessions_qs.filter(module_id=module_id)
    if class_level_id:
        sessions_qs = sessions_qs.filter(module__class_level_id=class_level_id)
    if semester_id:
        sessions_qs = sessions_qs.filter(module__semester_id=semester_id)

    history = []
    for sess in sessions_qs:
        p = sess.records.filter(status='P').count()
        s = sess.records.filter(status='S').count()
        a = sess.records.filter(status='A').count()
        t = p + s + a
        history.append({
            'id': sess.id,
            'date': str(sess.date),
            'module': sess.module.name,
            'session_type': sess.session_type,
            'session_type_display': sess.get_session_type_display(),
            'class_level': sess.module.class_level.name,
            'semester': sess.module.semester.label,
            'label': sess.label,
            'topic': sess.topic,
            'present': p, 'sick': s, 'absent': a,
            'pct': round(((p + s) / t) * 100) if t else 0,
        })

    return Response({
        'stats': {
            'students': len(rows),
            'avg_pct': avg_pct,
            'at_risk': at_risk,
            'critical': critical,
            'sick_total': sick_total,
        },
        'rows': rows,
        'session_history': history,
    })


# ── ALL MODULES (for claim/unclaim) ───────────────────────────────────────────

@api_view(['GET'])
@login_required
def all_modules(request):
    my_ids = set(request.user.modules_taught.values_list('id', flat=True))
    data = []
    for m in Module.objects.select_related('class_level', 'semester__academic_year').all():
        data.append({
            'id': m.id, 'name': m.name, 'code': m.code,
            'class_level': m.class_level.name,
            'semester': m.semester.label,
            'claimed': m.id in my_ids,
        })
    return Response(data)


# ── ELIGIBILITY ────────────────────────────────────────────────────────────────

ELIGIBILITY_THRESHOLD = 90


@api_view(['GET'])
@login_required
def eligibility(request):
    my_modules = user_modules(request.user)
    module_id = request.query_params.get('module_id')
    class_level_id = request.query_params.get('class_level_id')
    semester_id = request.query_params.get('semester_id')

    students = (
        Student.objects.filter(module__in=my_modules)
        .select_related('module__class_level', 'module__semester__academic_year')
        .prefetch_related('attendance_records__session')
    )
    if module_id:
        students = students.filter(module_id=module_id)
    if class_level_id:
        students = students.filter(module__class_level_id=class_level_id)
    if semester_id:
        students = students.filter(module__semester_id=semester_id)

    _mod_cache = {}

    def _period_counts(mid):
        if mid not in _mod_cache:
            cat1 = Session.objects.filter(module_id=mid, exam_period=Session.CAT1).count()
            cat2 = Session.objects.filter(module_id=mid, exam_period=Session.CAT2).count()
            total = Session.objects.filter(module_id=mid).count()
            _mod_cache[mid] = {'cat1': cat1, 'cat2': cat2, 'total': total}
        return _mod_cache[mid]

    rows = []
    for st in students:
        mc = _period_counts(st.module_id)
        all_records = list(st.attendance_records.all())

        cat1_eff = sum(1 for r in all_records if r.session.exam_period == Session.CAT1 and r.status in ('P', 'S'))
        cat2_eff = sum(1 for r in all_records if r.session.exam_period == Session.CAT2 and r.status in ('P', 'S'))
        total_eff = sum(1 for r in all_records if r.status in ('P', 'S'))

        cat1_pct = round((cat1_eff / mc['cat1']) * 100) if mc['cat1'] else None
        cat2_pct = round((cat2_eff / mc['cat2']) * 100) if mc['cat2'] else None
        end_pct = round((total_eff / mc['total']) * 100) if mc['total'] else None

        rows.append({
            'id': st.id,
            'nactvet_reg_no': st.nactvet_reg_no,
            'name': st.name,
            'module': st.module.name,
            'module_code': st.module.code,
            'class_level': st.module.class_level.name,
            'semester': st.module.semester.label,
            'cat1_sessions': mc['cat1'],
            'cat1_attended': cat1_eff,
            'cat1_pct': cat1_pct,
            'cat1_eligible': (cat1_pct >= ELIGIBILITY_THRESHOLD) if mc['cat1'] else None,
            'cat2_sessions': mc['cat2'],
            'cat2_attended': cat2_eff,
            'cat2_pct': cat2_pct,
            'cat2_eligible': (cat2_pct >= ELIGIBILITY_THRESHOLD) if mc['cat2'] else None,
            'end_sessions': mc['total'],
            'end_attended': total_eff,
            'end_pct': end_pct,
            'end_eligible': (end_pct >= ELIGIBILITY_THRESHOLD) if mc['total'] else None,
        })

    stats = {
        'total_students': len(rows),
        'cat1_eligible': sum(1 for r in rows if r['cat1_eligible'] is True),
        'cat1_ineligible': sum(1 for r in rows if r['cat1_eligible'] is False),
        'cat1_na': sum(1 for r in rows if r['cat1_eligible'] is None),
        'cat2_eligible': sum(1 for r in rows if r['cat2_eligible'] is True),
        'cat2_ineligible': sum(1 for r in rows if r['cat2_eligible'] is False),
        'cat2_na': sum(1 for r in rows if r['cat2_eligible'] is None),
        'end_eligible': sum(1 for r in rows if r['end_eligible'] is True),
        'end_ineligible': sum(1 for r in rows if r['end_eligible'] is False),
        'end_na': sum(1 for r in rows if r['end_eligible'] is None),
    }

    return Response({'stats': stats, 'rows': rows, 'threshold': ELIGIBILITY_THRESHOLD})


# ── SICK RECORDS ───────────────────────────────────────────────────────────────

@api_view(['GET'])
@login_required
def sick_records(request):
    my_modules = user_modules(request.user)
    module_id = request.query_params.get('module_id')
    semester_id = request.query_params.get('semester_id')
    class_level_id = request.query_params.get('class_level_id')

    records = (
        AttendanceRecord.objects.filter(
            status='S',
            student__module__in=my_modules,
        )
        .select_related(
            'student__module__class_level',
            'student__module__semester__academic_year',
            'session',
        )
        .order_by('-session__date')
    )

    if module_id:
        records = records.filter(student__module_id=module_id)
    if semester_id:
        records = records.filter(student__module__semester_id=semester_id)
    if class_level_id:
        records = records.filter(student__module__class_level_id=class_level_id)

    data = [
        {
            'id': r.id,
            'student_id': r.student.id,
            'student_name': r.student.name,
            'student_reg_no': r.student.nactvet_reg_no,
            'module': r.student.module.name,
            'module_code': r.student.module.code,
            'class_level': r.student.module.class_level.name,
            'semester': r.student.module.semester.label,
            'session_date': str(r.session.date),
            'session_label': r.session.label,
            'exam_period': r.session.exam_period,
            'exam_period_display': r.session.get_exam_period_display(),
            'sick_note': r.sick_note,
            'certificate_submitted': r.certificate_submitted,
        }
        for r in records
    ]

    return Response(data)


@api_view(['PATCH'])
@login_required
def update_sick_record(request, pk):
    if not request.user.is_staff:
        return Response({'detail': 'Only the administrator can update sick records.'}, status=status.HTTP_403_FORBIDDEN)

    try:
        record = AttendanceRecord.objects.select_related('student__module').get(pk=pk, status='S')
    except AttendanceRecord.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    my_mod_ids = set(user_modules(request.user).values_list('id', flat=True))
    if record.student.module_id not in my_mod_ids:
        return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

    if 'sick_note' in request.data:
        record.sick_note = str(request.data['sick_note']).strip()
    if 'certificate_submitted' in request.data:
        record.certificate_submitted = bool(request.data['certificate_submitted'])
    record.save()

    return Response({
        'id': record.id,
        'sick_note': record.sick_note,
        'certificate_submitted': record.certificate_submitted,
    })


# ── RESULTS ────────────────────────────────────────────────────────────────────

class ResultViewSet(viewsets.ModelViewSet):
    """
    Manage CA marks for students.
    Teachers can read/write results for their own modules.
    Admin can read/write all and download Excel.
    """
    serializer_class   = StudentResultSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = (
            StudentResult.objects
            .filter(student__module__in=user_modules(self.request.user))
            .select_related(
                'student__module__class_level',
                'student__module__semester__academic_year',
            )
        )
        module_id = self.request.query_params.get('module_id')
        if module_id:
            qs = qs.filter(student__module_id=module_id)
        return qs

    def update(self, request, *args, **kwargs):
        if not request.user.is_staff:
            for f in ('end_theory', 'end_practical'):
                if f in request.data:
                    raise PermissionDenied('Only the administrator can enter end-of-semester exam marks.')
        return super().update(request, *args, **kwargs)

    # ── Get or create results for every student in a module ────────────────────
    @action(detail=False, methods=['get'], url_path='module')
    def module_results(self, request):
        module_id = request.query_params.get('module_id')
        if not module_id:
            return Response({'detail': 'module_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            module = Module.objects.select_related('class_level', 'semester__academic_year').get(pk=module_id)
        except Module.DoesNotExist:
            return Response({'detail': 'Module not found.'}, status=status.HTTP_404_NOT_FOUND)

        mod_ids = set(user_modules(request.user).values_list('id', flat=True))
        if module.id not in mod_ids:
            raise PermissionDenied('You do not teach this module.')

        students = Student.objects.filter(module=module).order_by('name')
        with transaction.atomic():
            results = [StudentResult.objects.get_or_create(student=st)[0] for st in students]

        return Response({
            'module': {
                'id':           module.id,
                'name':         module.name,
                'code':         module.code,
                'has_practical': module.has_practical,
                'class_level':  module.class_level.name,
                'semester':     module.semester.label,
            },
            'results': StudentResultSerializer(results, many=True).data,
        })

    # ── Bulk-save marks submitted from the frontend ────────────────────────────
    @action(detail=False, methods=['post'], url_path='bulk_save')
    def bulk_save(self, request):
        updates = request.data if isinstance(request.data, list) else []
        if not updates:
            return Response({'detail': 'Empty list.'}, status=status.HTTP_400_BAD_REQUEST)

        mod_ids   = set(user_modules(request.user).values_list('id', flat=True))
        CA_FIELDS  = ['assign1', 'assign2', 'cat1_theory', 'cat2_theory', 'cat1_practical', 'cat2_practical']
        END_FIELDS = ['end_theory', 'end_practical']
        FIELDS     = CA_FIELDS + (END_FIELDS if request.user.is_staff else [])
        saved, errors = 0, []

        for item in updates:
            try:
                result = (
                    StudentResult.objects
                    .select_related('student__module')
                    .get(pk=item.get('id'))
                )
            except StudentResult.DoesNotExist:
                errors.append(f'Result {item.get("id")} not found')
                continue

            if result.student.module_id not in mod_ids:
                errors.append(f'Result {item.get("id")}: permission denied')
                continue

            for field in FIELDS:
                if field not in item:
                    continue
                raw = item[field]
                if raw == '' or raw is None:
                    setattr(result, field, None)
                else:
                    try:
                        v = float(raw)
                        if not (0 <= v <= 100):
                            errors.append(f'Result {item.get("id")}: {field} must be 0–100')
                            continue
                        setattr(result, field, v)
                    except (TypeError, ValueError):
                        errors.append(f'Result {item.get("id")}: invalid value for {field}')
                        continue
            result.save()
            saved += 1

        return Response({'saved': saved, 'errors': errors})


# ── RESULTS EXCEL DOWNLOAD (admin only) ────────────────────────────────────────

@login_required
def download_results(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('Administrator access required.')

    module_id       = request.GET.get('module_id')
    semester_id     = request.GET.get('semester_id')
    class_level_id  = request.GET.get('class_level_id')

    qs = (
        StudentResult.objects
        .filter(student__module__in=user_modules(request.user))
        .select_related(
            'student__module__class_level',
            'student__module__semester__academic_year',
        )
        .order_by('student__module__class_level__order', 'student__module__name', 'student__name')
    )
    if module_id:      qs = qs.filter(student__module_id=module_id)
    if semester_id:    qs = qs.filter(student__module__semester_id=semester_id)
    if class_level_id: qs = qs.filter(student__module__class_level_id=class_level_id)

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'CA Results'

    HDR_FILL = PatternFill('solid', fgColor='1E2D78')
    HDR_FONT = Font(bold=True, color='FFFFFF', size=10)
    CENTER   = Alignment(horizontal='center', vertical='center', wrap_text=True)
    GREEN_F  = Font(bold=True, color='16A34A')   # text only
    RED_F    = Font(bold=True, color='DC2626')
    BLACK_F  = Font(color='000000')

    headers = [
        '#', 'NACTVET Reg No', 'Student Name', 'Module', 'Code', 'Level', 'Semester', 'Type',
        'A1 /100', 'A2 /100', 'CAT1-T /100', 'CAT2-T /100', 'P1 /100', 'P2 /100',
        'A1 wt', 'A2 wt', 'CAT1-T wt', 'CAT2-T wt', 'P1 wt', 'P2 wt',
        'Theory CA', 'Practical CA', 'Total CA /40',
        'T-Eligible', 'P-Eligible', 'CA Eligible',
    ]
    ws.append(headers)
    for ci in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=ci)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = CENTER
    ws.row_dimensions[1].height = 30

    def wt(raw, weight):
        return round(float(raw) / 100 * weight, 2) if raw is not None else None

    def yn(v):
        if v is None: return 'Pending'
        return 'YES' if v else 'NO'

    def fmt(v):
        return float(v) if v is not None else ''

    for rn, res in enumerate(qs, 2):
        m  = res.student.module
        hp = m.has_practical
        a1, a2   = res.assign1,       res.assign2
        ct1, ct2 = res.cat1_theory,   res.cat2_theory
        cp1, cp2 = res.cat1_practical, res.cat2_practical

        if hp:
            a1w, a2w   = wt(a1, 2),   wt(a2, 2)
            ct1w, ct2w = wt(ct1, 8),  wt(ct2, 8)
            cp1w, cp2w = wt(cp1, 10), wt(cp2, 10)
            filled_t = [v for v in [a1w, a2w, ct1w, ct2w] if v is not None]
            filled_p = [v for v in [cp1w, cp2w]           if v is not None]
            t_ca     = round(sum(filled_t), 2) if filled_t else None
            p_ca     = round(sum(filled_p), 2) if filled_p else None
            tot      = round((t_ca or 0) + (p_ca or 0), 2) if (t_ca is not None or p_ca is not None) else None
            all_t    = all(v is not None for v in [a1, a2, ct1, ct2])
            all_p    = all(v is not None for v in [cp1, cp2])
            t_elig   = (t_ca >= 10) if (all_t and t_ca is not None) else None
            p_elig   = (p_ca >= 10) if (all_p and p_ca is not None) else None
            ca_elig  = (t_elig and p_elig) if (t_elig is not None and p_elig is not None) else None
        else:
            a1w, a2w   = wt(a1, 5),  wt(a2, 5)
            ct1w, ct2w = wt(ct1, 15), wt(ct2, 15)
            cp1w = cp2w = p_ca = None
            filled_t = [v for v in [a1w, a2w, ct1w, ct2w] if v is not None]
            t_ca     = round(sum(filled_t), 2) if filled_t else None
            tot      = t_ca
            all_done = all(v is not None for v in [a1, a2, ct1, ct2])
            t_elig = p_elig = None
            ca_elig  = (t_ca >= 20) if (all_done and t_ca is not None) else None

        row = [
            rn - 1,
            res.student.nactvet_reg_no, res.student.name,
            m.name, m.code, m.class_level.name, m.semester.label,
            'Theory + Practical' if hp else 'Theory Only',
            fmt(a1), fmt(a2), fmt(ct1), fmt(ct2), fmt(cp1), fmt(cp2),
            a1w or '', a2w or '', ct1w or '', ct2w or '', cp1w or '', cp2w or '',
            t_ca or '', p_ca or '', tot or '',
            yn(t_elig) if hp else 'N/A',
            yn(p_elig) if hp else 'N/A',
            yn(ca_elig),
        ]
        ws.append(row)
        # Color only the status cell text; all fills remain white
        elig_col = len(headers)
        status_font = GREEN_F if ca_elig is True else (RED_F if ca_elig is False else BLACK_F)
        ws.cell(row=rn, column=elig_col).font = status_font

    for col in ws.columns:
        width = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(width + 3, 28)

    fname = f'ca_results_{timezone.localdate()}.xlsx'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{fname}"'
    wb.save(response)
    return response


# ── FINAL RESULTS EXCEL DOWNLOAD (admin only) ──────────────────────────────────

@login_required
def download_final_results(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('Administrator access required.')

    module_id      = request.GET.get('module_id')
    semester_id    = request.GET.get('semester_id')
    class_level_id = request.GET.get('class_level_id')

    qs = (
        StudentResult.objects
        .filter(student__module__in=user_modules(request.user))
        .select_related(
            'student__module__class_level',
            'student__module__semester__academic_year',
        )
        .order_by('student__module__class_level__order', 'student__module__name', 'student__name')
    )
    if module_id:      qs = qs.filter(student__module_id=module_id)
    if semester_id:    qs = qs.filter(student__module__semester_id=semester_id)
    if class_level_id: qs = qs.filter(student__module__class_level_id=class_level_id)

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Final Results'

    HDR_FILL = PatternFill('solid', fgColor='1E2D78')
    HDR_FONT = Font(bold=True, color='FFFFFF', size=10)
    CENTER   = Alignment(horizontal='center', vertical='center', wrap_text=True)
    GREEN_F  = Font(bold=True, color='16A34A')
    RED_F    = Font(bold=True, color='DC2626')
    BLACK_F  = Font(color='000000')

    headers = [
        '#', 'NACTVET Reg No', 'Student Name', 'Module', 'Code', 'Level', 'Semester', 'Type',
        # CA marks
        'A1 /100', 'A2 /100', 'CAT1-T /100', 'CAT2-T /100', 'P1 /100', 'P2 /100',
        'Theory CA /20(or/40)', 'Practical CA /20', 'Total CA /40', 'CA Eligible',
        # End of semester
        'End Theory /100', 'End Practical /100',
        'End Theory wt', 'End Practical wt',
        'End Exam Total',
        # Grand total
        'Final Total /100', 'Pass/Fail',
    ]
    ws.append(headers)
    for ci in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=ci)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = CENTER
    ws.row_dimensions[1].height = 36

    def wt(raw, weight):
        return round(float(raw) / 100 * weight, 2) if raw is not None else None

    def yn(v):
        if v is None: return 'Pending'
        return 'YES' if v else 'NO'

    def fmt(v):
        return float(v) if v is not None else ''

    for rn, res in enumerate(qs, 2):
        m  = res.student.module
        hp = m.has_practical
        a1, a2   = res.assign1,       res.assign2
        ct1, ct2 = res.cat1_theory,   res.cat2_theory
        cp1, cp2 = res.cat1_practical, res.cat2_practical
        et       = res.end_theory
        ep       = res.end_practical

        # CA
        if hp:
            a1w, a2w   = wt(a1, 2),   wt(a2, 2)
            ct1w, ct2w = wt(ct1, 8),  wt(ct2, 8)
            cp1w, cp2w = wt(cp1, 10), wt(cp2, 10)
            filled_t   = [v for v in [a1w, a2w, ct1w, ct2w] if v is not None]
            filled_p   = [v for v in [cp1w, cp2w]           if v is not None]
            t_ca       = round(sum(filled_t), 2) if filled_t else None
            p_ca       = round(sum(filled_p), 2) if filled_p else None
            tot_ca     = round((t_ca or 0) + (p_ca or 0), 2) if (t_ca or p_ca) is not None else None
            all_t      = all(v is not None for v in [a1, a2, ct1, ct2])
            all_p      = all(v is not None for v in [cp1, cp2])
            t_elig     = (t_ca >= 10) if (all_t and t_ca is not None) else None
            p_elig     = (p_ca >= 10) if (all_p and p_ca is not None) else None
            ca_elig    = (t_elig and p_elig) if (t_elig is not None and p_elig is not None) else None
            # End exam
            etw  = wt(et, 30)
            epw  = wt(ep, 30)
        else:
            a1w, a2w   = wt(a1, 5),  wt(a2, 5)
            ct1w, ct2w = wt(ct1, 15), wt(ct2, 15)
            cp1w = cp2w = p_ca = None
            filled_t   = [v for v in [a1w, a2w, ct1w, ct2w] if v is not None]
            t_ca       = round(sum(filled_t), 2) if filled_t else None
            tot_ca     = t_ca
            all_done   = all(v is not None for v in [a1, a2, ct1, ct2])
            t_elig = p_elig = None
            ca_elig    = (t_ca >= 20) if (all_done and t_ca is not None) else None
            # End exam
            etw  = wt(et, 60)
            epw  = None

        end_exam_total = round((etw or 0) + (epw or 0), 2) if (etw is not None or epw is not None) else None
        final = round((tot_ca or 0) + (end_exam_total or 0), 2) if (tot_ca is not None or end_exam_total is not None) else None
        pass_fail = ('PASS' if final >= 50 else 'FAIL') if final is not None else 'Pending'

        row = [
            rn - 1,
            res.student.nactvet_reg_no, res.student.name,
            m.name, m.code, m.class_level.name, m.semester.label,
            'Theory + Practical' if hp else 'Theory Only',
            fmt(a1), fmt(a2), fmt(ct1), fmt(ct2), fmt(cp1), fmt(cp2),
            t_ca or '', p_ca or '', tot_ca or '', yn(ca_elig),
            fmt(et), fmt(ep) if hp else 'N/A',
            etw or '', epw or '' if hp else 'N/A',
            end_exam_total or '',
            final or '', pass_fail,
        ]
        ws.append(row)

        # Color only the Pass/Fail cell text; all fills remain white
        pf_col = len(headers)
        ws.cell(row=rn, column=pf_col).font = GREEN_F if pass_fail == 'PASS' else (RED_F if pass_fail == 'FAIL' else BLACK_F)

    for col in ws.columns:
        width = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(width + 3, 30)

    fname = f'final_results_{timezone.localdate()}.xlsx'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{fname}"'
    wb.save(response)
    return response


# ── ELIGIBILITY EXCEL DOWNLOAD (admin only) ────────────────────────────────────

@login_required
def download_eligibility_excel(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('Administrator access required.')

    module_id      = request.GET.get('module_id')
    semester_id    = request.GET.get('semester_id')
    class_level_id = request.GET.get('class_level_id')

    my_modules = user_modules(request.user)
    students = (
        Student.objects.filter(module__in=my_modules)
        .select_related('module__class_level', 'module__semester__academic_year')
        .prefetch_related('attendance_records__session')
        .order_by('module__class_level__order', 'module__name', 'name')
    )
    if module_id:      students = students.filter(module_id=module_id)
    if semester_id:    students = students.filter(module__semester_id=semester_id)
    if class_level_id: students = students.filter(module__class_level_id=class_level_id)

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Eligibility'

    HDR_FILL = PatternFill('solid', fgColor='1E2D78')
    HDR_FONT = Font(bold=True, color='FFFFFF', size=10)
    CENTER   = Alignment(horizontal='center', vertical='center', wrap_text=True)
    GREEN_F  = Font(bold=True, color='16A34A')
    RED_F    = Font(bold=True, color='DC2626')
    BLACK_F  = Font(color='000000')

    headers = [
        '#', 'NACTVET Reg No', 'Student Name', 'Module', 'Code', 'Level', 'Semester',
        'CAT1 Sessions', 'CAT1 Attended', 'CAT1 %', 'CAT1 Eligible',
        'CAT2 Sessions', 'CAT2 Attended', 'CAT2 %', 'CAT2 Eligible',
        'End Sessions', 'End Attended', 'End %', 'End Eligible',
    ]
    ws.append(headers)
    for ci in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=ci)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = CENTER
    ws.row_dimensions[1].height = 30

    _mod_cache = {}
    def _period_counts(mid):
        if mid not in _mod_cache:
            cat1  = Session.objects.filter(module_id=mid, exam_period=Session.CAT1).count()
            cat2  = Session.objects.filter(module_id=mid, exam_period=Session.CAT2).count()
            total = Session.objects.filter(module_id=mid).count()
            _mod_cache[mid] = {'cat1': cat1, 'cat2': cat2, 'total': total}
        return _mod_cache[mid]

    def yn(v):
        if v is None: return 'N/A'
        return 'YES' if v else 'NO'

    for rn, st in enumerate(students, 2):
        mc   = _period_counts(st.module_id)
        recs = list(st.attendance_records.all())

        cat1_eff  = sum(1 for r in recs if r.session.exam_period == Session.CAT1 and r.status in ('P', 'S'))
        cat2_eff  = sum(1 for r in recs if r.session.exam_period == Session.CAT2 and r.status in ('P', 'S'))
        total_eff = sum(1 for r in recs if r.status in ('P', 'S'))

        cat1_pct  = round((cat1_eff / mc['cat1']) * 100) if mc['cat1'] else None
        cat2_pct  = round((cat2_eff / mc['cat2']) * 100) if mc['cat2'] else None
        end_pct   = round((total_eff / mc['total']) * 100) if mc['total'] else None

        cat1_el = (cat1_pct >= ELIGIBILITY_THRESHOLD) if mc['cat1'] else None
        cat2_el = (cat2_pct >= ELIGIBILITY_THRESHOLD) if mc['cat2'] else None
        end_el  = (end_pct  >= ELIGIBILITY_THRESHOLD) if mc['total'] else None

        row = [
            rn - 1,
            st.nactvet_reg_no, st.name,
            st.module.name, st.module.code, st.module.class_level.name, st.module.semester.label,
            mc['cat1'], cat1_eff, cat1_pct if cat1_pct is not None else '', yn(cat1_el),
            mc['cat2'], cat2_eff, cat2_pct if cat2_pct is not None else '', yn(cat2_el),
            mc['total'], total_eff, end_pct if end_pct is not None else '', yn(end_el),
        ]
        ws.append(row)

        # Color only the three eligibility text cells
        for col_offset, elig in [(11, cat1_el), (15, cat2_el), (19, end_el)]:
            ws.cell(row=rn, column=col_offset).font = (
                GREEN_F if elig is True else (RED_F if elig is False else BLACK_F)
            )

    for col in ws.columns:
        width = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(width + 3, 24)

    fname = f'eligibility_{timezone.localdate()}.xlsx'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{fname}"'
    wb.save(response)
    return response
