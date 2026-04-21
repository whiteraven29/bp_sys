from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, redirect
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .forms import TeacherRegistrationForm, StyledAuthForm
from .models import (
    AcademicYear, Semester, ClassLevel, Module,
    Student, Session, AttendanceRecord, TeacherProfile,
)
from .serializers import (
    AcademicYearSerializer, SemesterSerializer, ClassLevelSerializer,
    ModuleSerializer, StudentSerializer,
    SessionSerializer, SessionCreateSerializer, BulkStudentSerializer,
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
        module = serializer.save()
        module.teachers.add(self.request.user)

    def destroy(self, request, *args, **kwargs):
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

    @action(detail=False, methods=['post'], url_path='bulk_create')
    def bulk_create(self, request):
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
