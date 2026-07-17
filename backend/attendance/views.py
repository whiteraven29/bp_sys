from functools import wraps
import re

from django.contrib.auth import get_user_model
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response

from .forms import TeacherRegistrationForm, StyledAuthForm, StudentLoginForm
from .models import (
    AcademicYear, Semester, ClassLevel, Module,
    Student, Session, AttendanceRecord, TeacherProfile, AccountantProfile,
    StudentResult, PaymentCategory, StudentFinanceObligation, StudentPayment,
    StudentFinanceClearance,
)
from .serializers import (
    AcademicYearSerializer, SemesterSerializer, ClassLevelSerializer,
    ModuleSerializer, StudentSerializer,
    SessionSerializer, SessionCreateSerializer, BulkStudentSerializer,
    StudentResultSerializer, AttendanceRecordSerializer,
    FinanceStudentSerializer, PaymentCategorySerializer,
    StudentFinanceObligationSerializer, StudentPaymentSerializer,
    StudentFinanceClearanceSerializer,
)
from .grading import grade_for_mark, gpa_classification, parse_authority_grade

User = get_user_model()

STUDENT_PASSWORD_MESSAGE = (
    'Password must be at least 8 characters and include an uppercase letter, '
    'a lowercase letter, a number, and a symbol.'
)


def student_password_is_strong(password):
    return (
        len(password) >= 8
        and re.search(r'[A-Z]', password)
        and re.search(r'[a-z]', password)
        and re.search(r'\d', password)
        and re.search(r'[^A-Za-z0-9]', password)
    )


# ── HELPERS ────────────────────────────────────────────────────────────────────

def user_modules(user):
    if user.is_staff:
        return Module.objects.all()
    return user.modules_taught.all()


def is_accountant(user):
    return bool(
        user
        and user.is_authenticated
        and AccountantProfile.objects.filter(user=user, is_active=True).exists()
    )


def can_manage_finance(user):
    return bool(user and user.is_authenticated and (user.is_staff or is_accountant(user)))


def active_semester():
    return Semester.objects.filter(is_active=True).select_related('academic_year').first()


def attendance_is_effective(record):
    """Present counts; sick counts for eligibility only with a certificate."""
    return record.status == AttendanceRecord.PRESENT or (
        record.status == AttendanceRecord.SICK and record.certificate_submitted
    )


class IsAuthenticatedReadOnlyOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.method in SAFE_METHODS or request.user.is_staff)
        )


class IsFinanceUser(BasePermission):
    def has_permission(self, request, view):
        return can_manage_finance(request.user)


def _make_both_semesters(year):
    """Ensure Semester 1 and 2 both exist for a given AcademicYear."""
    for num in (1, 2):
        Semester.objects.get_or_create(
            academic_year=year, number=num,
            defaults={'is_active': False},
        )


def _student_scope_for_request(request):
    qs = Student.objects.filter(module__in=user_modules(request.user))
    module_id = request.data.get('module_id') or request.query_params.get('module_id')
    class_level_id = request.data.get('class_level_id') or request.query_params.get('class_level_id')
    semester_id = request.data.get('semester_id') or request.query_params.get('semester_id')
    search = str(request.data.get('search') or request.query_params.get('search') or '').strip()
    if module_id:
        qs = qs.filter(module_id=module_id)
    if class_level_id:
        qs = qs.filter(module__class_level_id=class_level_id)
    if semester_id:
        qs = qs.filter(module__semester_id=semester_id)
    if search:
        qs = qs.filter(Q(nactvet_reg_no__icontains=search) | Q(name__icontains=search))
    return qs


# ── AUTH VIEWS ─────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('frontend')
    error = None
    identifier = ''

    if request.method == 'POST':
        identifier = str(request.POST.get('identifier', '')).strip()
        secret = str(request.POST.get('secret', '')).strip()

        # Try teacher/user authentication first
        user = authenticate(request, username=identifier, password=secret)
        if user is not None:
            login(request, user)
            return redirect(request.GET.get('next', 'frontend'))

        # Otherwise try student login using registration number + portal PIN.
        reg_no = identifier
        student = None
        students = Student.objects.filter(nactvet_reg_no__iexact=reg_no).select_related('module')
        for st in students:
            if st.check_portal_pin(secret):
                student = st
                break

        if student is None:
            error = 'Invalid credentials.'
        else:
            request.session['student_id'] = student.id
            request.session['student_reg_no'] = student.nactvet_reg_no
            return redirect('student-dashboard')

    return render(request, 'login.html', {'error': error, 'identifier': identifier})


def student_login_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.session.get('student_id'):
            return view_func(request, *args, **kwargs)
        return redirect('login')
    return _wrapped


def get_logged_student(request):
    student_id = request.session.get('student_id')
    if not student_id:
        return None
    return Student.objects.filter(
        id=student_id
    ).select_related(
        'module__class_level', 'module__semester__academic_year'
    ).first()


def student_logout_view(request):
    # Invalidate the complete server-side session and rotate the cookie. Merely
    # removing the student keys would leave the old session identifier reusable.
    request.session.flush()
    return redirect('login')


@api_view(['POST'])
def change_password(request):
    current_password = str(request.data.get('current_password', '')).strip()
    new_password = str(request.data.get('new_password', '')).strip()

    if request.user.is_authenticated:
        if len(new_password) < 6:
            return Response({'detail': 'New password must be at least 6 characters.'}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.check_password(current_password):
            return Response({'detail': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
        request.user.set_password(new_password)
        request.user.save(update_fields=['password'])
        update_session_auth_hash(request, request.user)
        return Response({'detail': 'Password updated.'})

    student = get_logged_student(request)
    if student is None:
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_403_FORBIDDEN)
    if not student.check_portal_pin(current_password):
        return Response({'detail': 'Current PIN is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
    if not student_password_is_strong(new_password):
        return Response({'detail': STUDENT_PASSWORD_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
    if current_password == new_password:
        return Response({'detail': 'Choose a new password different from the generated PIN.'}, status=status.HTTP_400_BAD_REQUEST)

    updated = Student.objects.filter(nactvet_reg_no__iexact=student.nactvet_reg_no).count()
    for enrollment in Student.objects.filter(nactvet_reg_no__iexact=student.nactvet_reg_no):
        enrollment.set_portal_pin(new_password, require_change=False)
        enrollment.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
    return Response({'detail': 'Portal password updated.', 'updated': updated})


@student_login_required
def student_dashboard(request):
    student = get_logged_student(request)
    if not student:
        return redirect('login')
    if student.must_change_portal_password:
        return render(request, 'student_password_change.html', {
            'student_name': student.name,
            'registration_number': student.nactvet_reg_no,
        })

    active_sem = active_semester()
    enrollments = Student.objects.filter(nactvet_reg_no=student.nactvet_reg_no)
    if active_sem is not None:
        enrollments = enrollments.filter(
            module__semester__academic_year=active_sem.academic_year
        )
    enrollments = enrollments.select_related(
        'module__class_level', 'module__semester__academic_year'
    ).prefetch_related('attendance_records__session', 'result').order_by(
        'module__semester__number', 'module__name'
    )

    modules = []
    attendance_sum = 0
    attendance_count = 0
    for enrollment in enrollments:
        serializer = StudentSerializer(enrollment)
        data = serializer.data
        result = getattr(enrollment, 'result', None)
        result_data = StudentResultSerializer(result).data if result else None
        ca_approved = bool(result and result.ca_approved)
        final_approved = bool(result and result.final_approved)
        if result_data and not ca_approved:
            for field in (
                'assign1', 'assign2', 'cat1_theory', 'cat2_theory',
                'cat1_practical', 'cat2_practical',
                'assign1_w', 'assign2_w', 'cat1_theory_w', 'cat2_theory_w',
                'cat1_prac_w', 'cat2_prac_w',
                'theory_ca', 'practical_ca',
                'theory_eligible', 'practical_eligible', 'ca_eligible',
            ):
                result_data[field] = None
            if not final_approved:
                result_data['total_ca'] = None
        if result_data and not final_approved:
            for field in (
                'end_theory', 'end_practical',
                'end_theory_w', 'end_prac_w', 'end_exam_total', 'final_total',
                'supplementary_mark', 'end_exam_mark', 'supplementary_required',
                'result_status', 'grade', 'grade_point', 'grade_description',
            ):
                result_data[field] = None
        if result_data and ca_approved:
            ca_fields = [
                'assign1', 'assign2', 'cat1_theory', 'cat2_theory',
            ] + (
                ['cat1_practical', 'cat2_practical']
                if enrollment.module.has_practical else []
            )
            if any(result_data.get(f'{field}_absent') for field in ca_fields):
                result_data['ca_display'] = 'ABS'
            elif any(result_data.get(field) is None for field in ca_fields):
                result_data['ca_display'] = 'INC'
            else:
                result_data['ca_display'] = result_data.get('total_ca')
            for field in ('assign1', 'assign2', 'cat1_theory', 'cat2_theory',
                          'cat1_practical', 'cat2_practical'):
                if result_data.get(f'{field}_absent'):
                    result_data[field] = None
        if result_data and final_approved:
            for field in ('end_theory', 'end_practical'):
                if result_data.get(f'{field}_absent'):
                    result_data[field] = 'ABS'
        has_final_result = bool(
            final_approved
            and result
            and (bool(result.authority_grade)
                 or result.end_theory is not None or result.end_theory_absent
                 or result.end_practical is not None or result.end_practical_absent)
        )

        if data['sessions_total']:
            attendance_sum += data['attendance_pct']
            attendance_count += 1

        modules.append({
            'module_name': data['module_name'],
            'module_code': data['module_code'],
            'teacher': enrollment.module.teacher,
            'class_level': data['class_level_name'],
            'semester': data['semester_label'],
            'semester_number': enrollment.module.semester.number,
            'credits': enrollment.module.credits,
            'sessions_attended': data['sessions_attended'],
            'sessions_sick': data['sessions_sick'],
            'sessions_absent': data['sessions_absent'],
            'sessions_total': data['sessions_total'],
            'attendance_pct': data['attendance_pct'],
            'attendance_status': (
                'good' if data['attendance_pct'] >= 75
                else 'warning' if data['attendance_pct'] >= 50
                else 'critical'
            ) if data['sessions_total'] else 'pending',
            'result': result_data,
            'has_ca_result': bool(ca_approved and result),
            'has_final_result': has_final_result,
        })

    published_points = [
        (module['result']['grade_point'], module['credits'])
        for module in modules
        if module['has_final_result']
        and module['result']
        and module['result']['grade_point'] is not None
    ]
    gpa = (
        round(
            sum(float(points) * credits for points, credits in published_points)
            / sum(credits for _, credits in published_points),
            2,
        )
        if published_points else None
    )
    gpa_class = gpa_classification(
        gpa, student.module.class_level
    )

    overall_attendance = round(attendance_sum / attendance_count) if attendance_count else None
    total_present = sum(module['sessions_attended'] for module in modules)
    total_sick = sum(module['sessions_sick'] for module in modules)
    total_absent = sum(module['sessions_absent'] for module in modules)
    total_sessions = sum(module['sessions_total'] for module in modules)
    semester1_modules = [module for module in modules if module['semester_number'] == 1]
    semester2_modules = [module for module in modules if module['semester_number'] == 2]
    semester1_theory_modules = [
        module for module in semester1_modules
        if not module['result'] or not module['result']['has_practical']
    ]
    semester1_practical_modules = [
        module for module in semester1_modules
        if module['result'] and module['result']['has_practical']
    ]
    semester2_theory_modules = [
        module for module in semester2_modules
        if not module['result'] or not module['result']['has_practical']
    ]
    semester2_practical_modules = [
        module for module in semester2_modules
        if module['result'] and module['result']['has_practical']
    ]
    active_modules = [
        module for module in modules
        if active_sem is None or module['semester_number'] == active_sem.number
    ]
    eligibility_rows = []
    for enrollment in enrollments:
        counts = {
            Session.CAT1: Session.objects.filter(module=enrollment.module, exam_period=Session.CAT1).count(),
            Session.CAT2: Session.objects.filter(module=enrollment.module, exam_period=Session.CAT2).count(),
            'all': Session.objects.filter(module=enrollment.module).count(),
        }
        records = list(enrollment.attendance_records.select_related('session'))
        cat1_eff = sum(1 for r in records if r.session.exam_period == Session.CAT1 and attendance_is_effective(r))
        cat2_eff = sum(1 for r in records if r.session.exam_period == Session.CAT2 and attendance_is_effective(r))
        all_eff = sum(1 for r in records if attendance_is_effective(r))
        cat1_pct = round((cat1_eff / counts[Session.CAT1]) * 100) if counts[Session.CAT1] else None
        cat2_pct = round((cat2_eff / counts[Session.CAT2]) * 100) if counts[Session.CAT2] else None
        end_pct = round((all_eff / counts['all']) * 100) if counts['all'] else None
        cat1_att = (cat1_pct >= ELIGIBILITY_THRESHOLD) if counts[Session.CAT1] else None
        cat2_att = (cat2_pct >= ELIGIBILITY_THRESHOLD) if counts[Session.CAT2] else None
        end_att = (end_pct >= ELIGIBILITY_THRESHOLD) if counts['all'] else None
        total_ca, ca_ok, ca_note = ca_eligibility_for_student(enrollment)
        clearances = {c.period: c for c in enrollment.finance_clearances.filter(semester=enrollment.module.semester)}
        cat1_fin = bool(clearances.get(StudentFinanceClearance.CAT1) and clearances[StudentFinanceClearance.CAT1].is_cleared)
        cat2_fin = bool(clearances.get(StudentFinanceClearance.CAT2) and clearances[StudentFinanceClearance.CAT2].is_cleared)
        end_fin = bool(clearances.get(StudentFinanceClearance.END) and clearances[StudentFinanceClearance.END].is_cleared)
        cat1_parts = [
            (cat1_att, attendance_eligibility_reason(cat1_pct, counts[Session.CAT1], 'CAT 1')),
            (cat1_fin, finance_clearance_reason(cat1_fin, 'CAT 1')),
        ]
        cat2_parts = [
            (cat2_att, attendance_eligibility_reason(cat2_pct, counts[Session.CAT2], 'CAT 2')),
            (cat2_fin, finance_clearance_reason(cat2_fin, 'CAT 2')),
        ]
        end_parts = [
            (end_att, attendance_eligibility_reason(end_pct, counts['all'], 'End-of-semester')),
            (ca_ok, ca_note),
            (end_fin, finance_clearance_reason(end_fin, 'end-of-semester exam')),
        ]
        eligibility_rows.append({
            'module_code': enrollment.module.code,
            'module_name': enrollment.module.name,
            'semester': enrollment.module.semester.label,
            'cat1_pct': cat1_pct,
            'cat1_ok': combined_eligibility(cat1_parts),
            'cat1_note': combined_reason(cat1_parts),
            'cat2_pct': cat2_pct,
            'cat2_ok': combined_eligibility(cat2_parts),
            'cat2_note': combined_reason(cat2_parts),
            'ese_pct': end_pct,
            'ca_total': total_ca,
            'ese_ok': combined_eligibility(end_parts),
            'ese_note': combined_reason(end_parts),
        })
    finance_obligations = list(
        StudentFinanceObligation.objects
        .filter(student__in=enrollments)
        .select_related('category', 'module', 'semester')
        .prefetch_related('payments')
        .order_by('-created_at')
    )
    finance_payments = list(
        StudentPayment.objects
        .filter(student__in=enrollments)
        .select_related('category')
        .order_by('-payment_date', '-created_at')
    )
    finance_required = sum(o.amount_required for o in finance_obligations) + sum(
        p.amount_required for p in finance_payments if p.obligation_id is None
    )
    finance_paid = sum(p.amount_paid for p in finance_payments)
    finance_balance = finance_required - finance_paid
    return render(request, 'student_dashboard.html', {
        'student_name': student.name,
        'registration_number': student.nactvet_reg_no,
        'modules': modules,
        'module_count': len(modules),
        'active_module_count': len(active_modules),
        'semester1_modules': semester1_modules,
        'semester2_modules': semester2_modules,
        'semester1_theory_modules': semester1_theory_modules,
        'semester1_practical_modules': semester1_practical_modules,
        'semester2_theory_modules': semester2_theory_modules,
        'semester2_practical_modules': semester2_practical_modules,
        'has_ca_results_sem1': any(module['has_ca_result'] for module in semester1_modules),
        'has_ca_results_sem2': any(module['has_ca_result'] for module in semester2_modules),
        'has_final_results': any(module['has_final_result'] for module in modules),
        'gpa': gpa,
        'gpa_classification': gpa_class,
        'overall_attendance': overall_attendance,
        'total_present': total_present,
        'total_sick': total_sick,
        'total_absent': total_absent,
        'total_sessions': total_sessions,
        'eligibility_rows': eligibility_rows,
        'finance_obligations': finance_obligations,
        'finance_payments': finance_payments,
        'finance_required': finance_required,
        'finance_paid': finance_paid,
        'finance_balance': finance_balance,
        'academic_year': (
            active_sem.academic_year.name
            if active_sem else student.module.semester.academic_year.name
        ),
        'active_semester': active_sem.label if active_sem else 'N/A',
    })


def logout_view(request):
    # Django logout flushes the complete session and rotates the session cookie.
    logout(request)
    return redirect('login')


def register_view(request):
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.is_staff:
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
        return redirect('frontend')
    return render(request, 'register.html', {'form': form, 'levels': levels})


@api_view(['POST'])
@login_required
def create_staff_account(request):
    if not request.user.is_staff:
        return Response({'detail': 'Only the administrator can create staff accounts.'}, status=status.HTTP_403_FORBIDDEN)

    role = str(request.data.get('role', '')).strip()
    full_name = str(request.data.get('full_name', '')).strip()
    username = str(request.data.get('username', '')).strip()
    password = str(request.data.get('password', '')).strip()
    module_ids = request.data.get('module_ids') or []

    if role not in ('tutor', 'accountant'):
        return Response({'detail': 'Role must be tutor or accountant.'}, status=status.HTTP_400_BAD_REQUEST)
    if not full_name or not username or len(password) < 6:
        return Response({'detail': 'Full name, username, and a 6+ character password are required.'}, status=status.HTTP_400_BAD_REQUEST)
    if User.objects.filter(username__iexact=username).exists():
        return Response({'detail': 'That username is already taken.'}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        parts = full_name.split(None, 1)
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=parts[0],
            last_name=parts[1] if len(parts) > 1 else '',
        )
        if role == 'accountant':
            AccountantProfile.objects.create(user=user, full_name=full_name)
        else:
            TeacherProfile.objects.create(user=user, full_name=full_name)
            modules = Module.objects.filter(id__in=module_ids)
            for module in modules:
                module.teachers.add(user)

    return Response({
        'id': user.id,
        'username': user.username,
        'full_name': full_name,
        'role': role,
    }, status=status.HTTP_201_CREATED)


# ── ACADEMIC YEAR ──────────────────────────────────────────────────────────────

class AcademicYearViewSet(viewsets.ModelViewSet):
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearSerializer
    permission_classes = [IsAuthenticatedReadOnlyOrAdmin]

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
    permission_classes = [IsAuthenticatedReadOnlyOrAdmin]


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
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(nactvet_reg_no__icontains=search) | Q(name__icontains=search))
        return qs

    def perform_create(self, serializer):
        module = serializer.validated_data.get('module')
        if not self.request.user.is_staff:
            allowed_module_ids = user_modules(self.request.user).values_list('id', flat=True)
            if module is None or module.id not in allowed_module_ids:
                raise PermissionDenied('Only tutors for this module can add students.')
        serializer.save()

    def update(self, request, *args, **kwargs):
        if not request.user.is_staff:
            obj = self.get_object()
            allowed_module_ids = set(user_modules(self.request.user).values_list('id', flat=True))
            if obj.module_id not in allowed_module_ids:
                raise PermissionDenied('Only tutors for this module can edit students.')
            target_module = request.data.get('module')
            if target_module:
                try:
                    target_module_id = int(target_module)
                except (TypeError, ValueError):
                    target_module_id = None
                if target_module_id not in allowed_module_ids:
                    raise PermissionDenied('You may only move students to modules you teach.')
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if not request.user.is_staff:
            allowed_module_ids = user_modules(self.request.user).values_list('id', flat=True)
            if obj.module_id not in allowed_module_ids:
                raise PermissionDenied('Only tutors for this module can remove students.')
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['post'], url_path='bulk_create')
    def bulk_create(self, request):
        serializer = BulkStudentSerializer(data=request.data, context={'user': request.user})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(result, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='bulk_set_pin')
    def bulk_set_pin(self, request):
        if not request.user.is_staff:
            raise PermissionDenied('Only the administrator can set student passwords in bulk.')
        portal_pin = str(request.data.get('portal_pin', '')).strip()
        if len(portal_pin) < 6:
            return Response({'detail': 'Password/PIN must be at least 6 characters.'}, status=status.HTTP_400_BAD_REQUEST)

        qs = _student_scope_for_request(request)
        updated = 0
        with transaction.atomic():
            for student in qs.select_for_update():
                student.set_portal_pin(portal_pin)
                student.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
                updated += 1
        return Response({'updated': updated})


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
        allowed_module_ids = user_modules(self.request.user).values_list('id', flat=True)
        if module.id not in allowed_module_ids:
            raise PermissionDenied('You may only record attendance for modules you teach.')
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

    def perform_update(self, serializer):
        module = serializer.validated_data.get('module', serializer.instance.module)
        allowed_module_ids = user_modules(self.request.user).values_list('id', flat=True)
        if module.id not in allowed_module_ids:
            raise PermissionDenied('You may only edit sessions for modules you teach.')
        serializer.save()


# ── DASHBOARD ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@login_required
def dashboard(request):
    today = timezone.localdate()
    my_modules = user_modules(request.user)
    sem = active_semester()

    subjects_count = my_modules.count()
    # Count distinct students per class level (by registration number) and sum
    # those per-class totals for an overall student count. This prevents
    # double-counting when modules in the same class have different student lists.
    students_qs = Student.objects.filter(module__in=my_modules)
    per_class = (
        students_qs
        .values('module__class_level_id')
        .annotate(student_count=Count('nactvet_reg_no', distinct=True))
    )
    students_count = int(per_class.aggregate(total=Sum('student_count'))['total'] or 0)
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
        # Distinct students in this class level by registration number
        lvl_students_count = (
            Student.objects.filter(module__in=lvl_mods)
            .values('nactvet_reg_no')
            .distinct()
            .count()
        )
        lp, lc = 0, 0
        # Use students per level for attendance pct calculation
        lvl_students = all_students.filter(module__in=lvl_mods)
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
            'students': lvl_students_count,
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
        'is_accountant': is_accountant(request.user),
    })


# ── FINANCE ───────────────────────────────────────────────────────────────────

class FinanceStudentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FinanceStudentSerializer
    permission_classes = [IsFinanceUser]

    def get_queryset(self):
        qs = Student.objects.select_related(
            'module__class_level', 'module__semester__academic_year'
        )
        semester_id = self.request.query_params.get('semester_id')
        if not semester_id:
            sem = active_semester()
            if sem:
                qs = qs.filter(module__semester=sem)
        for param, field in [
            ('class_level_id', 'module__class_level_id'),
            ('semester_id', 'module__semester_id'),
        ]:
            val = self.request.query_params.get(param)
            if val:
                qs = qs.filter(**{field: val})
        search = str(self.request.query_params.get('search', '')).strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(nactvet_reg_no__icontains=search)
                | Q(module__name__icontains=search)
                | Q(module__code__icontains=search)
            )
        return qs.order_by('module__class_level__order', 'name')


class PaymentCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentCategorySerializer
    permission_classes = [IsFinanceUser]

    def get_queryset(self):
        qs = PaymentCategory.objects.select_related('created_by')
        semester_id = self.request.query_params.get('semester_id')
        if semester_id:
            qs = qs.filter(Q(semester_id=semester_id) | Q(semester__isnull=True))
        else:
            sem = active_semester()
            if sem:
                qs = qs.filter(Q(semester=sem) | Q(semester__isnull=True))
        class_level_id = self.request.query_params.get('class_level_id')
        if class_level_id:
            qs = qs.filter(Q(class_level_id=class_level_id) | Q(class_level__isnull=True))
        active = self.request.query_params.get('is_active')
        if active == 'true':
            qs = qs.filter(is_active=True)
        category_type = self.request.query_params.get('category_type')
        if category_type:
            qs = qs.filter(category_type=category_type)
        return qs.order_by('category_type', 'name')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class StudentFinanceObligationViewSet(viewsets.ModelViewSet):
    serializer_class = StudentFinanceObligationSerializer
    permission_classes = [IsFinanceUser]

    def get_queryset(self):
        qs = StudentFinanceObligation.objects.select_related(
            'student__module__class_level',
            'student__module__semester__academic_year',
            'semester__academic_year',
            'module',
            'category',
            'declared_by',
        ).prefetch_related('payments')
        semester_id = self.request.query_params.get('semester_id')
        if semester_id:
            qs = qs.filter(semester_id=semester_id)
        else:
            sem = active_semester()
            if sem:
                qs = qs.filter(semester=sem)
        for param, field in [
            ('student_id', 'student_id'),
            ('class_level_id', 'student__module__class_level_id'),
            ('obligation_type', 'obligation_type'),
        ]:
            val = self.request.query_params.get(param)
            if val:
                qs = qs.filter(**{field: val})
        return qs.order_by('-created_at')

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Only the administrator can declare special, supplementary, and repeat-module obligations.')
        serializer.save(declared_by=self.request.user)


class StudentPaymentViewSet(viewsets.ModelViewSet):
    serializer_class = StudentPaymentSerializer
    permission_classes = [IsFinanceUser]

    def get_queryset(self):
        qs = StudentPayment.objects.select_related(
            'student__module__class_level',
            'student__module__semester__academic_year',
            'category',
            'obligation',
            'recorded_by',
        )
        semester_id = self.request.query_params.get('semester_id')
        if semester_id:
            qs = qs.filter(student__module__semester_id=semester_id)
        else:
            sem = active_semester()
            if sem:
                qs = qs.filter(student__module__semester=sem)
        for param, field in [
            ('student_id', 'student_id'),
            ('category_id', 'category_id'),
            ('class_level_id', 'student__module__class_level_id'),
            ('obligation_id', 'obligation_id'),
        ]:
            val = self.request.query_params.get(param)
            if val:
                qs = qs.filter(**{field: val})
        return qs.order_by('-payment_date', '-created_at')

    def perform_create(self, serializer):
        category = serializer.validated_data.get('category')
        installment_number = serializer.validated_data.get('installment_number') or 1
        if category and installment_number > category.installment_count:
            raise PermissionDenied(f'This category allows only {category.installment_count} installment(s).')
        if serializer.validated_data.get('amount_required') in (None, 0) and category:
            serializer.validated_data['amount_required'] = category.default_amount
        serializer.save(recorded_by=self.request.user)


class StudentFinanceClearanceViewSet(viewsets.ModelViewSet):
    serializer_class = StudentFinanceClearanceSerializer
    permission_classes = [IsFinanceUser]

    def get_queryset(self):
        qs = StudentFinanceClearance.objects.select_related(
            'student__module__class_level',
            'student__module__semester__academic_year',
            'semester__academic_year',
            'approved_by',
        )
        semester_id = self.request.query_params.get('semester_id')
        if semester_id:
            qs = qs.filter(semester_id=semester_id)
        else:
            sem = active_semester()
            if sem:
                qs = qs.filter(semester=sem)
        for param, field in [
            ('student_id', 'student_id'),
            ('class_level_id', 'student__module__class_level_id'),
            ('period', 'period'),
        ]:
            val = self.request.query_params.get(param)
            if val:
                qs = qs.filter(**{field: val})
        return qs.order_by('student__name', 'period')

    def perform_create(self, serializer):
        serializer.save(approved_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(approved_by=self.request.user)


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
CA_ELIGIBILITY_THRESHOLD = 20


def ca_eligibility_for_student(student):
    result = getattr(student, 'result', None)
    has_practical = student.module.has_practical
    required_fields = [
        ('assign1', 'Assignment 1'),
        ('assign2', 'Assignment 2'),
        ('cat1_theory', 'CAT 1 Theory'),
        ('cat2_theory', 'CAT 2 Theory'),
    ]
    if has_practical:
        required_fields.extend([
            ('cat1_practical', 'Practical Test 1'),
            ('cat2_practical', 'Practical Test 2'),
        ])

    if result is None:
        return None, None, 'Pending CA result record'

    missing = [label for field, label in required_fields
               if getattr(result, field) is None and not getattr(result, f'{field}_absent')]
    serializer = StudentResultSerializer()
    total_ca = serializer.get_total_ca(result)

    if missing:
        return total_ca, None, f"Pending CA marks: {', '.join(missing)}"
    if total_ca is None:
        return None, None, 'Pending CA marks'
    if total_ca < CA_ELIGIBILITY_THRESHOLD:
        return total_ca, False, f'Total CA {total_ca}/40 is below required {CA_ELIGIBILITY_THRESHOLD}/40'
    return total_ca, True, f'Total CA {total_ca}/40 meets required {CA_ELIGIBILITY_THRESHOLD}/40'


def attendance_eligibility_reason(pct, sessions, label):
    if not sessions:
        return f'Pending {label} attendance sessions'
    if pct is None:
        return f'Pending {label} attendance'
    if pct < ELIGIBILITY_THRESHOLD:
        return f'{label} attendance {pct}% is below required {ELIGIBILITY_THRESHOLD}%'
    return f'{label} attendance {pct}% meets required {ELIGIBILITY_THRESHOLD}%'


def finance_clearance_reason(is_cleared, label):
    if is_cleared:
        return f'Finance cleared for {label}'
    return f'Pending finance clearance for {label}'


def combined_eligibility(parts):
    if any(value is False for value, _note in parts):
        return False
    if any(value is None for value, _note in parts):
        return None
    return True


def combined_reason(parts):
    return '; '.join(note for _value, note in parts if note)


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
        .prefetch_related('finance_clearances')
        .select_related('result')
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

        cat1_eff = sum(1 for r in all_records if r.session.exam_period == Session.CAT1 and attendance_is_effective(r))
        cat2_eff = sum(1 for r in all_records if r.session.exam_period == Session.CAT2 and attendance_is_effective(r))
        total_eff = sum(1 for r in all_records if attendance_is_effective(r))

        cat1_pct = round((cat1_eff / mc['cat1']) * 100) if mc['cat1'] else None
        cat2_pct = round((cat2_eff / mc['cat2']) * 100) if mc['cat2'] else None
        end_pct = round((total_eff / mc['total']) * 100) if mc['total'] else None
        cat1_eligible = (cat1_pct >= ELIGIBILITY_THRESHOLD) if mc['cat1'] else None
        cat2_eligible = (cat2_pct >= ELIGIBILITY_THRESHOLD) if mc['cat2'] else None
        end_eligible = (end_pct >= ELIGIBILITY_THRESHOLD) if mc['total'] else None
        total_ca, ca_eligible, ca_note = ca_eligibility_for_student(st)
        finance_by_period = {
            c.period: c for c in st.finance_clearances.all()
            if c.semester_id == st.module.semester_id
        }
        cat1_finance_eligible = bool(finance_by_period.get(StudentFinanceClearance.CAT1) and finance_by_period[StudentFinanceClearance.CAT1].is_cleared)
        cat2_finance_eligible = bool(finance_by_period.get(StudentFinanceClearance.CAT2) and finance_by_period[StudentFinanceClearance.CAT2].is_cleared)
        end_finance_eligible = bool(finance_by_period.get(StudentFinanceClearance.END) and finance_by_period[StudentFinanceClearance.END].is_cleared)
        cat1_att_note = attendance_eligibility_reason(cat1_pct, mc['cat1'], 'CAT 1')
        cat2_att_note = attendance_eligibility_reason(cat2_pct, mc['cat2'], 'CAT 2')
        end_att_note = attendance_eligibility_reason(end_pct, mc['total'], 'End-of-semester')
        cat1_exam_parts = [
            (cat1_eligible, cat1_att_note),
            (cat1_finance_eligible, finance_clearance_reason(cat1_finance_eligible, 'CAT 1')),
        ]
        cat2_exam_parts = [
            (cat2_eligible, cat2_att_note),
            (cat2_finance_eligible, finance_clearance_reason(cat2_finance_eligible, 'CAT 2')),
        ]
        end_exam_parts = [
            (end_eligible, end_att_note),
            (ca_eligible, ca_note),
            (end_finance_eligible, finance_clearance_reason(end_finance_eligible, 'end-of-semester exam')),
        ]

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
            'cat1_eligible': cat1_eligible,
            'cat1_note': cat1_att_note,
            'cat1_finance_eligible': cat1_finance_eligible,
            'cat1_finance_note': finance_clearance_reason(cat1_finance_eligible, 'CAT 1'),
            'cat1_exam_eligible': combined_eligibility(cat1_exam_parts),
            'cat1_exam_note': combined_reason(cat1_exam_parts),
            'cat2_sessions': mc['cat2'],
            'cat2_attended': cat2_eff,
            'cat2_pct': cat2_pct,
            'cat2_eligible': cat2_eligible,
            'cat2_note': cat2_att_note,
            'cat2_finance_eligible': cat2_finance_eligible,
            'cat2_finance_note': finance_clearance_reason(cat2_finance_eligible, 'CAT 2'),
            'cat2_exam_eligible': combined_eligibility(cat2_exam_parts),
            'cat2_exam_note': combined_reason(cat2_exam_parts),
            'end_sessions': mc['total'],
            'end_attended': total_eff,
            'end_pct': end_pct,
            'end_eligible': end_eligible,
            'end_note': end_att_note,
            'end_finance_eligible': end_finance_eligible,
            'end_finance_note': finance_clearance_reason(end_finance_eligible, 'end-of-semester exam'),
            'end_exam_eligible': combined_eligibility(end_exam_parts),
            'end_exam_note': combined_reason(end_exam_parts),
            'ca_total': total_ca,
            'ca_eligible': ca_eligible,
            'ca_note': ca_note,
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
        'ca_eligible': sum(1 for r in rows if r['ca_eligible'] is True),
        'ca_ineligible': sum(1 for r in rows if r['ca_eligible'] is False),
        'ca_na': sum(1 for r in rows if r['ca_eligible'] is None),
        'cat1_exam_eligible': sum(1 for r in rows if r['cat1_exam_eligible'] is True),
        'cat1_exam_ineligible': sum(1 for r in rows if r['cat1_exam_eligible'] is False),
        'cat1_exam_na': sum(1 for r in rows if r['cat1_exam_eligible'] is None),
        'cat2_exam_eligible': sum(1 for r in rows if r['cat2_exam_eligible'] is True),
        'cat2_exam_ineligible': sum(1 for r in rows if r['cat2_exam_eligible'] is False),
        'cat2_exam_na': sum(1 for r in rows if r['cat2_exam_eligible'] is None),
        'end_exam_eligible': sum(1 for r in rows if r['end_exam_eligible'] is True),
        'end_exam_ineligible': sum(1 for r in rows if r['end_exam_eligible'] is False),
        'end_exam_na': sum(1 for r in rows if r['end_exam_eligible'] is None),
    }

    return Response({
        'stats': stats,
        'rows': rows,
        'threshold': ELIGIBILITY_THRESHOLD,
        'ca_threshold': CA_ELIGIBILITY_THRESHOLD,
    })


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


@api_view(['PATCH'])
@login_required
def update_attendance_status(request, pk):
    """Admin-only: change the status of an attendance record.

    Allows setting status to 'P', 'A' or 'S'. When marking as 'S', an optional
    `sick_note` and `certificate_submitted` may be provided. Non-staff users
    are forbidden.
    """
    if not request.user.is_staff:
        return Response({'detail': 'Only the administrator can change attendance status.'}, status=status.HTTP_403_FORBIDDEN)

    try:
        record = AttendanceRecord.objects.select_related('student__module').get(pk=pk)
    except AttendanceRecord.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    my_mod_ids = set(user_modules(request.user).values_list('id', flat=True))
    if record.student.module_id not in my_mod_ids:
        return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

    # Validate and apply status change
    if 'status' in request.data:
        new_status = str(request.data.get('status')).upper()
        if new_status not in (AttendanceRecord.PRESENT, AttendanceRecord.ABSENT, AttendanceRecord.SICK):
            return Response({'detail': 'Invalid status.'}, status=status.HTTP_400_BAD_REQUEST)
        record.status = new_status
        if new_status != AttendanceRecord.SICK:
            # Clear sick-related fields when not sick
            record.sick_note = ''
            record.certificate_submitted = False

    if 'sick_note' in request.data and record.status == AttendanceRecord.SICK:
        record.sick_note = str(request.data['sick_note']).strip()

    if 'certificate_submitted' in request.data:
        record.certificate_submitted = bool(request.data['certificate_submitted'])

    record.save()

    return Response(AttendanceRecordSerializer(record).data)


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
            for f in ('end_theory', 'end_practical', 'supplementary_mark', 'end_theory_absent',
                      'end_practical_absent', 'ca_approved', 'final_approved'):
                if f in request.data:
                    raise PermissionDenied('Only the administrator can approve or enter restricted result fields.')
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        student = serializer.validated_data['student']
        allowed_module_ids = user_modules(self.request.user).values_list('id', flat=True)
        if student.module_id not in allowed_module_ids:
            raise PermissionDenied('You may only create results for modules you tutor.')
        if not self.request.user.is_staff:
            for field in ('ca_approved', 'final_approved'):
                if field in serializer.validated_data:
                    raise PermissionDenied('Only the administrator can approve or enter restricted result fields.')
            for field in ('end_theory', 'end_practical', 'supplementary_mark',
                          'end_theory_absent', 'end_practical_absent'):
                if serializer.validated_data.get(field) is not None:
                    raise PermissionDenied('Only the administrator can approve or enter restricted result fields.')
        serializer.save()

    @action(detail=False, methods=['post'], url_path='authority-grades')
    def authority_grades(self, request):
        if not request.user.is_staff:
            raise PermissionDenied('Only the administrator can upload authority grades.')
        rows = request.data.get('rows', []) if isinstance(request.data, dict) else []
        if not rows:
            return Response({'detail': 'No grade rows supplied.'}, status=status.HTTP_400_BAD_REQUEST)

        saved, errors = 0, []
        with transaction.atomic():
            for index, row in enumerate(rows, start=2):
                reg_no = str(row.get('reg_no', '')).strip()
                module_code = str(row.get('module_code', '')).strip()
                raw = str(row.get('grade', '')).strip().upper()
                if not reg_no or not module_code or not raw:
                    continue
                student = Student.objects.filter(
                    nactvet_reg_no__iexact=reg_no, module__code__iexact=module_code,
                ).select_related('module').first()
                if student is None:
                    errors.append(f'Row {index}: no enrollment for {reg_no} / {module_code}')
                    continue
                try:
                    parsed = parse_authority_grade(raw, student.module.class_level)
                except ValueError as exc:
                    errors.append(f'Row {index}: {exc} for {reg_no} / {module_code}')
                    continue
                result, _ = StudentResult.objects.get_or_create(student=student)
                result.authority_grade = parsed['raw']
                result.authority_status = parsed['status']
                result.final_approved = True
                result.save(update_fields=['authority_grade', 'authority_status', 'final_approved', 'updated_at'])
                saved += 1
        return Response({'saved': saved, 'errors': errors})

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
            raise PermissionDenied('You do not tutor this module.')

        students = Student.objects.filter(module=module).order_by('name')
        with transaction.atomic():
            results = [StudentResult.objects.get_or_create(student=st)[0] for st in students]

        return Response({
            'module': {
                'id':           module.id,
                'name':         module.name,
                'code':         module.code,
                'has_practical': module.has_practical,
                'credits':       module.credits,
                'class_level':  module.class_level.name,
                'semester_id':   module.semester_id,
                'semester':     module.semester.label,
            },
            'results': StudentResultSerializer(results, many=True).data,
        })

    @action(detail=False, methods=['get'], url_path='cat-analysis')
    def cat_analysis(self, request):
        cat = str(request.query_params.get('cat', '1'))
        if cat not in ('1', '2'):
            return Response(
                {'detail': 'cat must be 1 or 2.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        semester_id = request.query_params.get('semester_id')
        if not semester_id:
            return Response(
                {'detail': 'Select a semester for CAT analysis.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        semester = Semester.objects.select_related('academic_year').filter(
            pk=semester_id
        ).first()
        if semester is None:
            return Response(
                {'detail': 'Semester not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        qs = (
            StudentResult.objects
            .filter(student__module__in=user_modules(request.user))
            .select_related(
                'student__module__class_level',
                'student__module__semester__academic_year',
            )
            .order_by('student__module__class_level__order', 'student__module__code')
        )
        for param, field in (
            ('module_id', 'student__module_id'),
            ('semester_id', 'student__module__semester_id'),
            ('class_level_id', 'student__module__class_level_id'),
        ):
            value = request.query_params.get(param)
            if value:
                qs = qs.filter(**{field: value})

        theory_field = f'cat{cat}_theory'
        practical_field = f'cat{cat}_practical'
        modules = {}
        all_grade_counts = {}
        overall_assessed = overall_passed = overall_failed = overall_incomplete = 0

        for result in qs:
            module = result.student.module
            row = modules.setdefault(module.id, {
                'module_id': module.id,
                'module_code': module.code,
                'module_name': module.name,
                'class_level': module.class_level.name,
                'semester': module.semester.label,
                'has_practical': module.has_practical,
                'assessed': 0, 'passed': 0, 'failed': 0, 'incomplete': 0,
                'grade_counts': {},
            })

            theory_complete = (
                getattr(result, theory_field) is not None
                or getattr(result, f'{theory_field}_absent')
            )
            practical_complete = (
                not module.has_practical
                or getattr(result, practical_field) is not None
                or getattr(result, f'{practical_field}_absent')
            )
            if not theory_complete or not practical_complete:
                row['incomplete'] += 1
                overall_incomplete += 1
                continue

            theory = (
                0.0 if getattr(result, f'{theory_field}_absent')
                else float(getattr(result, theory_field))
            )
            if module.has_practical:
                practical = (
                    0.0 if getattr(result, f'{practical_field}_absent')
                    else float(getattr(result, practical_field))
                )
                mark = round((theory + practical) / 2, 2)
            else:
                mark = theory

            grade, _, _ = grade_for_mark(mark, module.class_level)
            row['assessed'] += 1
            row['grade_counts'][grade] = row['grade_counts'].get(grade, 0) + 1
            all_grade_counts[grade] = all_grade_counts.get(grade, 0) + 1
            overall_assessed += 1
            if mark >= 50:
                row['passed'] += 1
                overall_passed += 1
            else:
                row['failed'] += 1
                overall_failed += 1

        rows = list(modules.values())
        for row in rows:
            row['pass_rate'] = (
                round(row['passed'] / row['assessed'] * 100, 1)
                if row['assessed'] else 0
            )

        return Response({
            'cat': int(cat),
            'semester': semester.label,
            'stats': {
                'modules': len(rows),
                'assessed': overall_assessed,
                'passed': overall_passed,
                'failed': overall_failed,
                'incomplete': overall_incomplete,
                'pass_rate': (
                    round(overall_passed / overall_assessed * 100, 1)
                    if overall_assessed else 0
                ),
                'grade_counts': all_grade_counts,
            },
            'rows': rows,
        })

    @action(detail=False, methods=['get'], url_path='cat-analysis/download')
    def download_cat_analysis(self, request):
        analysis_response = self.cat_analysis(request)
        if analysis_response.status_code != status.HTTP_200_OK:
            return analysis_response
        data = analysis_response.data

        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = f'CAT {data["cat"]} Analysis'
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = 'A8'
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = '7:7'

        navy = '1E2D78'
        blue = 'DCE6F1'
        green = 'DCFCE7'
        red = 'FEE2E2'
        yellow = 'FEF3C7'
        white = 'FFFFFF'
        thin = Side(style='thin', color='B8C0CC')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        headers = [
            'Module Code', 'Module Name', 'Level', 'Assessed',
            'A', 'B+', 'B', 'C', 'D', 'F',
            'Passed', 'Failed', 'Incomplete', 'Pass Rate',
        ]
        last_col = len(headers)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
        title = ws.cell(1, 1, f'EDUTRACK — CAT {data["cat"]} MODULE ANALYSIS')
        title.font = Font(bold=True, size=16, color=white)
        title.fill = PatternFill('solid', fgColor=navy)
        title.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 30

        filters = []
        for param, label, model in (
            ('semester_id', 'Semester', Semester),
            ('class_level_id', 'Class Level', ClassLevel),
            ('module_id', 'Module', Module),
        ):
            value = request.query_params.get(param)
            if value:
                obj = model.objects.filter(pk=value).first()
                filters.append(f'{label}: {obj or value}')
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
        ws.cell(2, 1, ' | '.join(filters) if filters else 'Scope: All accessible modules').alignment = Alignment(horizontal='center')

        stats = data['stats']
        summary = [
            ('Modules', stats['modules']), ('Assessed', stats['assessed']),
            ('Passed', stats['passed']), ('Failed', stats['failed']),
            ('Incomplete', stats['incomplete']), ('Total Pass Rate', f'{stats["pass_rate"]}%'),
        ]
        for index, (label, value) in enumerate(summary, 1):
            column = 1 + (index - 1) * 2
            ws.merge_cells(start_row=4, start_column=column, end_row=4, end_column=column + 1)
            ws.merge_cells(start_row=5, start_column=column, end_row=5, end_column=column + 1)
            ws.cell(4, column, label).font = Font(bold=True, color=navy)
            ws.cell(4, column).alignment = Alignment(horizontal='center')
            ws.cell(5, column, value).font = Font(bold=True, size=13)
            ws.cell(5, column).alignment = Alignment(horizontal='center')
            for row_number in (4, 5):
                for col in range(column, column + 2):
                    ws.cell(row_number, col).fill = PatternFill('solid', fgColor=blue)
                    ws.cell(row_number, col).border = border

        for col, header in enumerate(headers, 1):
            cell = ws.cell(7, col, header)
            cell.font = Font(bold=True, color=white)
            cell.fill = PatternFill('solid', fgColor=navy)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = border

        grades = ['A', 'B+', 'B', 'C', 'D', 'F']
        for row_number, row in enumerate(data['rows'], 8):
            values = [
                row['module_code'], row['module_name'], row['class_level'],
                row['assessed'],
                *[row['grade_counts'].get(grade, 0) for grade in grades],
                row['passed'], row['failed'], row['incomplete'],
                row['pass_rate'] / 100,
            ]
            for col, value in enumerate(values, 1):
                cell = ws.cell(row_number, col, value)
                cell.border = border
                cell.alignment = Alignment(
                    horizontal='left' if col in (1, 2, 3) else 'center',
                    vertical='center',
                    wrap_text=True,
                )
                if row_number % 2 == 0:
                    cell.fill = PatternFill('solid', fgColor='F8FAFC')
            ws.cell(row_number, 11).fill = PatternFill('solid', fgColor=green)
            ws.cell(row_number, 12).fill = PatternFill('solid', fgColor=red)
            ws.cell(row_number, 13).fill = PatternFill('solid', fgColor=yellow)
            ws.cell(row_number, 14).number_format = '0.0%'

        widths = [14, 30, 18, 10, 7, 7, 7, 7, 7, 7, 10, 10, 12, 12]
        for col, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.auto_filter.ref = f'A7:{get_column_letter(last_col)}{max(7, 7 + len(data["rows"]))}'
        ws.print_area = f'A1:{get_column_letter(last_col)}{max(7, 7 + len(data["rows"]))}'

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = (
            f'attachment; filename="cat_{data["cat"]}_module_analysis_{timezone.localdate()}.xlsx"'
        )
        wb.save(response)
        return response

    # ── Bulk-save marks submitted from the frontend ────────────────────────────
    @action(detail=False, methods=['post'], url_path='bulk_save')
    def bulk_save(self, request):
        updates = request.data if isinstance(request.data, list) else []
        if not updates:
            return Response({'detail': 'Empty list.'}, status=status.HTTP_400_BAD_REQUEST)

        mod_ids   = set(user_modules(request.user).values_list('id', flat=True))
        CA_MARKS = ['assign1', 'assign2', 'cat1_theory', 'cat2_theory', 'cat1_practical', 'cat2_practical']
        CA_FIELDS = CA_MARKS + [f'{field}_absent' for field in CA_MARKS]
        END_MARKS = ['end_theory', 'end_practical', 'supplementary_mark']
        END_FIELDS = END_MARKS + [f'{field}_absent' for field in END_MARKS] + ['ca_approved', 'final_approved']
        FIELDS     = CA_FIELDS + (END_FIELDS if request.user.is_staff else [])
        saved, errors = 0, []

        for item in updates:
            if not request.user.is_staff:
                restricted = {'end_theory', 'end_practical', 'supplementary_mark', 'end_theory_absent',
                              'end_practical_absent', 'ca_approved', 'final_approved'}
                blocked = restricted.intersection(item.keys())
                if blocked:
                    errors.append(f'Result {item.get("id")}: administrator approval required')
                    continue

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
                if field in ('ca_approved', 'final_approved') or field.endswith('_absent'):
                    setattr(result, field, bool(raw))
                    if field.endswith('_absent') and bool(raw):
                        setattr(result, field[:-7], None)
                    continue
                if raw == '' or raw is None:
                    setattr(result, field, None)
                else:
                    try:
                        v = float(raw)
                        if not (0 <= v <= 100):
                            errors.append(f'Result {item.get("id")}: {field} must be 0–100')
                            continue
                        setattr(result, field, v)
                        setattr(result, f'{field}_absent', False)
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
        'Final Total /100', 'Supplementary /100', 'Grade', 'Grade Point', 'Result Status',
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
        outcome = StudentResultSerializer(res).data
        pass_fail = outcome['result_status']

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
            final or '', fmt(res.supplementary_mark), outcome['grade'] or '',
            outcome['grade_point'] if outcome['grade_point'] is not None else '',
            pass_fail,
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

        cat1_eff  = sum(1 for r in recs if r.session.exam_period == Session.CAT1 and attendance_is_effective(r))
        cat2_eff  = sum(1 for r in recs if r.session.exam_period == Session.CAT2 and attendance_is_effective(r))
        total_eff = sum(1 for r in recs if attendance_is_effective(r))

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
