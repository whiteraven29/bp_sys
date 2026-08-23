from functools import wraps
import re
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .forms import TeacherRegistrationForm, StyledAuthForm, StudentLoginForm
from .models import (
    AcademicYear, Semester, ClassLevel, Module,
    Student, Session, AttendanceRecord, TeacherProfile, AccountantProfile,
    StudentResult, PaymentCategory, StudentFinanceObligation, StudentPayment,
    StudentFinanceClearance,
    EstateOfficerProfile, InventoryLocation, AssetCategory, InventoryItemType, Asset, AssetImport,
    AssetTransfer, AssetMaintenance, InventoryInspection, InventoryInspectionItem, AssetDisposal,
)
from .serializers import (
    AcademicYearSerializer, SemesterSerializer, ClassLevelSerializer,
    ModuleSerializer, StudentSerializer,
    SessionSerializer, SessionCreateSerializer, BulkStudentSerializer,
    StudentResultSerializer, AttendanceRecordSerializer,
    FinanceStudentSerializer, PaymentCategorySerializer,
    StudentFinanceObligationSerializer, StudentPaymentSerializer,
    StudentFinanceClearanceSerializer,
    InventoryLocationSerializer, AssetCategorySerializer, InventoryItemTypeSerializer, AssetSerializer,
    AssetTransferSerializer, AssetMaintenanceSerializer, InventoryInspectionSerializer,
    InventoryInspectionItemSerializer, AssetDisposalSerializer,
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


def is_estate_officer(user):
    return bool(
        user and user.is_authenticated
        and EstateOfficerProfile.objects.filter(user=user, is_active=True).exists()
    )


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


class IsEstateOfficer(BasePermission):
    def has_permission(self, request, view):
        return is_estate_officer(request.user)


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
            next_url = request.GET.get('next', '')
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_url)
            return redirect('frontend')

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

    if role not in ('tutor', 'accountant', 'estate_officer'):
        return Response({'detail': 'Role must be tutor, accountant, or estate officer.'}, status=status.HTTP_400_BAD_REQUEST)
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
        elif role == 'estate_officer':
            EstateOfficerProfile.objects.create(user=user, full_name=full_name)
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
        # Self-service claiming is only for picking up a module nobody teaches
        # yet. Once a module has a tutor, moving/adding tutors is an admin
        # action — otherwise any teacher account could grant themselves access
        # to another module's students, attendance and CA marks at will.
        if not request.user.is_staff and m.teachers.exists() and not m.teachers.filter(pk=request.user.pk).exists():
            raise PermissionDenied('This module already has a tutor. Ask the administrator to add you.')
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
        if self.action in ('create', 'update', 'partial_update'):
            return SessionCreateSerializer
        return SessionSerializer

    @staticmethod
    def _check_attendance_cutoff(module, period):
        """Reject session writes whose exam period is past the semester cutoff date."""
        semester = module.semester
        today = timezone.localdate()

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

    def perform_create(self, serializer):
        module = serializer.validated_data['module']
        allowed_module_ids = user_modules(self.request.user).values_list('id', flat=True)
        if module.id not in allowed_module_ids:
            raise PermissionDenied('You may only record attendance for modules you teach.')
        period = serializer.validated_data.get('exam_period', Session.GENERAL)
        self._check_attendance_cutoff(module, period)
        serializer.save()

    def perform_update(self, serializer):
        module = serializer.validated_data.get('module', serializer.instance.module)
        allowed_module_ids = user_modules(self.request.user).values_list('id', flat=True)
        if module.id not in allowed_module_ids:
            raise PermissionDenied('You may only edit sessions for modules you teach.')
        period = serializer.validated_data.get('exam_period', serializer.instance.exam_period)
        self._check_attendance_cutoff(module, period)
        serializer.save()


# ── DASHBOARD ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@login_required
def dashboard(request):
    estate_user = is_estate_officer(request.user)
    if estate_user:
        return Response({
            'modules': 0, 'students': 0, 'sessions_today': 0, 'avg_attendance': None,
            'active_semester': None, 'recent_sessions': [], 'levels': [],
            'is_staff': False, 'is_accountant': False, 'is_estate_officer': True,
        })
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
        'is_estate_officer': False,
    })


# ── INVENTORY ─────────────────────────────────────────────────────────────────

class InventoryLocationViewSet(viewsets.ModelViewSet):
    queryset = InventoryLocation.objects.all()
    serializer_class = InventoryLocationSerializer
    permission_classes = [IsEstateOfficer]


class AssetCategoryViewSet(viewsets.ModelViewSet):
    queryset = AssetCategory.objects.all()
    serializer_class = AssetCategorySerializer
    permission_classes = [IsEstateOfficer]


class InventoryItemTypeViewSet(viewsets.ModelViewSet):
    queryset = InventoryItemType.objects.select_related('category')
    serializer_class = InventoryItemTypeSerializer
    permission_classes = [IsEstateOfficer]


class AssetViewSet(viewsets.ModelViewSet):
    serializer_class = AssetSerializer
    permission_classes = [IsEstateOfficer]

    def get_queryset(self):
        qs = Asset.objects.select_related('category', 'location')
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(asset_tag__icontains=search) | Q(name__icontains=search) |
                           Q(description__icontains=search) | Q(responsible_office__icontains=search))
        for field in ('category', 'location', 'condition'):
            value = self.request.query_params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class AssetTransferViewSet(viewsets.ModelViewSet):
    queryset = AssetTransfer.objects.select_related('asset', 'from_location', 'to_location', 'resulting_asset')
    serializer_class = AssetTransferSerializer
    permission_classes = [IsEstateOfficer]

    def perform_create(self, serializer):
        asset = serializer.validated_data['asset']
        to_location = serializer.validated_data['to_location']
        if asset.location_id == to_location.id:
            raise PermissionDenied('Choose a different destination location.')
        with transaction.atomic():
            quantity = serializer.validated_data.get('quantity', 1)
            responsible = serializer.validated_data['new_responsible_office']
            from_location = asset.location
            if quantity < asset.quantity:
                suffix = 1
                while Asset.objects.filter(asset_tag=f'{asset.asset_tag}/{suffix:03d}').exists():
                    suffix += 1
                moved_asset = Asset.objects.create(
                    asset_tag=f'{asset.asset_tag}/{suffix:03d}', name=asset.name,
                    description=asset.description, category=asset.category, location=to_location,
                    responsible_office=responsible, quantity=quantity, condition=asset.condition,
                    created_by=self.request.user, updated_by=self.request.user,
                )
                asset.quantity -= quantity
                asset.updated_by = self.request.user
                asset.save(update_fields=['quantity', 'updated_by', 'updated_at'])
            else:
                moved_asset = asset
                asset.location = to_location
                asset.responsible_office = responsible
                asset.updated_by = self.request.user
                asset.save(update_fields=['location', 'responsible_office', 'updated_by', 'updated_at'])
            serializer.save(from_location=from_location, resulting_asset=moved_asset, recorded_by=self.request.user)


class AssetMaintenanceViewSet(viewsets.ModelViewSet):
    queryset = AssetMaintenance.objects.select_related('asset')
    serializer_class = AssetMaintenanceSerializer
    permission_classes = [IsEstateOfficer]

    def perform_create(self, serializer):
        asset = serializer.validated_data['asset']
        quantity = serializer.validated_data.get('quantity', 1)
        with transaction.atomic():
            if quantity < asset.quantity:
                suffix = 1
                while Asset.objects.filter(asset_tag=f'{asset.asset_tag}/{suffix:03d}').exists():
                    suffix += 1
                maintenance_asset = Asset.objects.create(
                    asset_tag=f'{asset.asset_tag}/{suffix:03d}', name=asset.name,
                    description=asset.description, category=asset.category, location=asset.location,
                    responsible_office=asset.responsible_office, quantity=quantity,
                    condition=asset.condition, created_by=self.request.user, updated_by=self.request.user,
                )
                asset.quantity -= quantity
                asset.updated_by = self.request.user
                asset.save(update_fields=['quantity', 'updated_by', 'updated_at'])
                serializer.save(asset=maintenance_asset, recorded_by=self.request.user)
            else:
                serializer.save(recorded_by=self.request.user)


class InventoryInspectionViewSet(viewsets.ModelViewSet):
    queryset = InventoryInspection.objects.select_related('location').prefetch_related('items')
    serializer_class = InventoryInspectionSerializer
    permission_classes = [IsEstateOfficer]

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)


class InventoryInspectionItemViewSet(viewsets.ModelViewSet):
    queryset = InventoryInspectionItem.objects.select_related('inspection', 'asset')
    serializer_class = InventoryInspectionItemSerializer
    permission_classes = [IsEstateOfficer]


class AssetDisposalViewSet(viewsets.ModelViewSet):
    queryset = AssetDisposal.objects.select_related('asset')
    serializer_class = AssetDisposalSerializer
    permission_classes = [IsEstateOfficer]

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)


INVENTORY_HEADERS = [
    'Office/Location *', 'Responsible Person/Office *', 'Item Type *',
    'Tag Prefix *', 'Starting Number *', 'Quantity *', 'Condition *', 'Description',
]


@api_view(['GET'])
def inventory_template(request):
    if not is_estate_officer(request.user):
        return Response({'detail': 'Estate Officer access required.'}, status=403)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Assets'
    ws.append(INVENTORY_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='C0392B')
    ws.freeze_panes = 'A2'
    widths = [30, 32, 32, 22, 18, 14, 20, 38]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + idx)].width = width
    lists = wb.create_sheet('Lists')
    categories = list(AssetCategory.objects.filter(is_active=True).values_list('name', flat=True))
    locations = list(InventoryLocation.objects.filter(is_active=True).values_list('name', flat=True))
    item_types = [f'{item.name} — {item.category.name}' for item in InventoryItemType.objects.filter(is_active=True).select_related('category')]
    conditions = [label for _, label in Asset.CONDITION_CHOICES]
    for row, value in enumerate(categories, 1): lists.cell(row, 1, value)
    for row, value in enumerate(locations, 1): lists.cell(row, 2, value)
    for row, value in enumerate(conditions, 1): lists.cell(row, 3, value)
    for row, value in enumerate(item_types, 1): lists.cell(row, 4, value)
    if locations:
        dv = DataValidation(type='list', formula1=f"'Lists'!$B$1:$B${len(locations)}")
        ws.add_data_validation(dv); dv.add('A2:A5000')
    if item_types:
        dv = DataValidation(type='list', formula1=f"'Lists'!$D$1:$D${len(item_types)}")
        ws.add_data_validation(dv); dv.add('C2:C5000')
    dv = DataValidation(type='list', formula1=f"'Lists'!$C$1:$C${len(conditions)}")
    ws.add_data_validation(dv); dv.add('G2:G5000')
    instructions = wb.create_sheet('Instructions', 0)
    instructions.append(['COLLEGE INVENTORY IMPORT TEMPLATE — Version 1'])
    instructions.append(['Every column marked * is required. Do not rename or reorder headings.'])
    instructions.append(['Choose one office and item type per row. Quantity is expanded into separate tags.'])
    instructions.append(['Example: prefix BPCH/CH, start 1, quantity 30 creates BPCH/CH/1 through BPCH/CH/30.'])
    instructions.append(['Validate the file in the system before confirming import.'])
    output = BytesIO(); wb.save(output)
    response = HttpResponse(output.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="college_inventory_template.xlsx"'
    return response


def _parse_inventory_upload(upload):
    try:
        wb = load_workbook(upload, read_only=True, data_only=True)
    except Exception:
        return [], [{'row': 0, 'field': 'File', 'message': 'Upload a valid .xlsx workbook.'}]
    if 'Assets' not in wb.sheetnames:
        return [], [{'row': 0, 'field': 'Worksheet', 'message': 'The Assets worksheet is missing.'}]
    ws = wb['Assets']
    headers = [str(c.value or '').strip() for c in ws[1]]
    if headers[:len(INVENTORY_HEADERS)] != INVENTORY_HEADERS:
        return [], [{'row': 1, 'field': 'Headings', 'message': 'Use the latest system template without changing headings.'}]
    locations = {x.name.casefold(): x for x in InventoryLocation.objects.filter(is_active=True)}
    item_types = {f'{x.name} — {x.category.name}'.casefold(): x for x in InventoryItemType.objects.filter(is_active=True).select_related('category')}
    conditions = {label.casefold(): value for value, label in Asset.CONDITION_CHOICES}
    existing = {x.casefold() for x in Asset.objects.values_list('asset_tag', flat=True)}
    seen, rows, errors = set(), [], []
    fields = ['location', 'responsible_office', 'item_type', 'tag_prefix', 'start_number', 'quantity', 'condition', 'description']
    for number, values in enumerate(ws.iter_rows(min_row=2, max_col=8, values_only=True), 2):
        if not any(v not in (None, '') for v in values): continue
        data = dict(zip(fields, values))
        for key, value in data.items():
            if key == 'description':
                continue
            if value is None or str(value).strip() == '': errors.append({'row': number, 'field': key, 'message': 'Required.'})
        location = locations.get(str(data['location'] or '').strip().casefold())
        item_type = item_types.get(str(data['item_type'] or '').strip().casefold())
        condition = conditions.get(str(data['condition'] or '').strip().casefold())
        if data['location'] and not location: errors.append({'row': number, 'field': 'location', 'message': 'Choose a location from the template list.'})
        if data['item_type'] and not item_type: errors.append({'row': number, 'field': 'item_type', 'message': 'Choose an item type from the template list.'})
        if data['condition'] and not condition: errors.append({'row': number, 'field': 'condition', 'message': 'Choose a condition from the template list.'})
        try:
            quantity = int(data['quantity'])
            start = int(data['start_number'])
            if quantity < 1 or quantity > 500 or start < 1 or float(data['quantity']) != quantity or float(data['start_number']) != start: raise ValueError
        except (TypeError, ValueError):
            quantity, start = 0, 0; errors.append({'row': number, 'field': 'quantity', 'message': 'Use quantity 1–500 and a positive starting number.'})
        prefix = str(data['tag_prefix'] or '').strip().rstrip('/')
        if item_type and location and condition and quantity:
            for sequence in range(start, start + quantity):
                tag = f'{prefix}/{sequence}'
                if tag.casefold() in existing or tag.casefold() in seen:
                    errors.append({'row': number, 'field': 'tag_prefix', 'message': f'Generated tag {tag} already exists or is duplicated.'})
                seen.add(tag.casefold())
                rows.append({'asset_tag': tag, 'name': item_type.name,
                             'description': str(data['description'] or item_type.description).strip(),
                             'category': item_type.category, 'item_type': item_type, 'location': location,
                             'responsible_office': str(data['responsible_office'] or '').strip(),
                             'quantity': 1, 'condition': condition})
    if not rows: errors.append({'row': 0, 'field': 'File', 'message': 'No item rows were found.'})
    return rows, errors


@api_view(['POST'])
def inventory_import(request):
    if not is_estate_officer(request.user):
        return Response({'detail': 'Estate Officer access required.'}, status=403)
    upload = request.FILES.get('file')
    if not upload:
        return Response({'detail': 'Choose an Excel file.'}, status=400)
    rows, errors = _parse_inventory_upload(upload)
    if errors:
        return Response({'valid': False, 'row_count': len(rows), 'errors': errors}, status=400)
    if str(request.data.get('confirm', '')).lower() not in ('1', 'true', 'yes'):
        return Response({'valid': True, 'row_count': len(rows), 'errors': []})
    with transaction.atomic():
        for row in rows:
            Asset.objects.create(**row, created_by=request.user, updated_by=request.user)
        AssetImport.objects.create(uploaded_by=request.user, file_name=upload.name, imported_rows=len(rows))
    return Response({'valid': True, 'imported': len(rows)}, status=201)


@api_view(['POST'])
def inventory_bulk_create(request):
    """Create individually tagged assets from a shared tag prefix and quantity."""
    if not is_estate_officer(request.user):
        return Response({'detail': 'Estate Officer access required.'}, status=403)

    try:
        count = int(request.data.get('count'))
        start_number = int(request.data.get('start_number', 1))
    except (TypeError, ValueError):
        return Response({'detail': 'Quantity and starting number must be positive whole numbers.'}, status=400)
    if count < 1 or count > 500 or start_number < 1:
        return Response({'detail': 'Enter a quantity from 1 to 500 and a starting number of at least 1.'}, status=400)

    prefix = str(request.data.get('asset_tag_prefix', '')).strip().rstrip('/')
    if not prefix:
        return Response({'asset_tag_prefix': ['Enter an asset tag prefix.']}, status=400)
    tags = [f'{prefix}/{number}' for number in range(start_number, start_number + count)]
    conflicts = list(Asset.objects.filter(asset_tag__in=tags).values_list('asset_tag', flat=True))
    if conflicts:
        return Response({
            'detail': 'One or more generated asset tags are already registered.',
            'conflicting_tags': conflicts[:20],
        }, status=400)

    shared = {
        'name': request.data.get('name'),
        'description': request.data.get('description', ''),
        'category': request.data.get('category'),
        'location': request.data.get('location'),
        'responsible_office': request.data.get('responsible_office'),
        'condition': request.data.get('condition'),
        'quantity': 1,
    }
    validated = []
    for tag in tags:
        serializer = AssetSerializer(data={**shared, 'asset_tag': tag})
        serializer.is_valid(raise_exception=True)
        validated.append(serializer.validated_data)

    with transaction.atomic():
        # Lock matching records so concurrent requests cannot both pass validation.
        if Asset.objects.select_for_update().filter(asset_tag__in=tags).exists():
            return Response({'detail': 'A generated tag was registered by another request. Please try again.'}, status=409)
        Asset.objects.bulk_create([
            Asset(**data, created_by=request.user, updated_by=request.user)
            for data in validated
        ])
    return Response({
        'created': count,
        'first_tag': tags[0],
        'last_tag': tags[-1],
    }, status=201)


@api_view(['POST'])
def inventory_office_register(request):
    """Register multiple item types for one office and expand each quantity into tagged assets."""
    if not is_estate_officer(request.user):
        return Response({'detail': 'Estate Officer access required.'}, status=403)
    location_id = request.data.get('location')
    responsible = str(request.data.get('responsible_office', '')).strip()
    rows = request.data.get('items')
    if not InventoryLocation.objects.filter(pk=location_id, is_active=True).exists():
        return Response({'location': ['Choose an active office/location.']}, status=400)
    if not responsible:
        return Response({'responsible_office': ['Enter the responsible person or office.']}, status=400)
    if not isinstance(rows, list) or not rows:
        return Response({'items': ['Add at least one item type.']}, status=400)

    generated, prepared, errors = [], [], []
    # Numbering is global per prefix, not per office. Work inside one transaction and
    # lock the asset rows so two simultaneous office submissions cannot reuse a tag.
    with transaction.atomic():
        existing_tags = list(Asset.objects.select_for_update().values_list('asset_tag', flat=True))
        next_numbers = {}

        def next_number_for(prefix):
            key = prefix.casefold()
            if key not in next_numbers:
                pattern = re.compile(rf'^{re.escape(prefix)}/(\d+)$', re.IGNORECASE)
                numbers = [int(match.group(1)) for tag in existing_tags if (match := pattern.match(tag))]
                next_numbers[key] = max(numbers, default=0) + 1
            number = next_numbers[key]
            next_numbers[key] += 1
            return number

        for index, row in enumerate(rows, 1):
            try:
                item_type = InventoryItemType.objects.select_related('category').get(pk=row.get('item_type'), is_active=True)
                count = int(row.get('quantity'))
            except InventoryItemType.DoesNotExist:
                errors.append({'row': index, 'field': 'item_type', 'message': 'Choose an active item type.'})
                continue
            except (TypeError, ValueError):
                errors.append({'row': index, 'field': 'quantity', 'message': 'Quantity must be a whole number.'})
                continue
            if count < 1 or count > 500:
                errors.append({'row': index, 'field': 'quantity', 'message': 'Use quantity 1–500.'})
                continue
            prefix = str(row.get('tag_prefix') or item_type.default_tag_prefix).strip().rstrip('/')
            condition = row.get('condition')
            tags = [f'{prefix}/{next_number_for(prefix)}' for _ in range(count)]
            generated.extend(tags)
            for tag in tags:
                prepared.append({
                    'asset_tag': tag, 'name': item_type.name, 'description': item_type.description,
                    'category': item_type.category_id, 'item_type': item_type.id,
                    'location': location_id, 'responsible_office': responsible,
                    'quantity': 1, 'condition': condition,
                })
        if len(generated) > 1000:
            errors.append({'row': 0, 'field': 'items', 'message': 'One office submission may create at most 1,000 asset records.'})
        if errors:
            return Response({'detail': 'Correct the office stock rows.', 'errors': errors}, status=400)

        serializers = [AssetSerializer(data=data) for data in prepared]
        for serializer in serializers:
            serializer.is_valid(raise_exception=True)
        if Asset.objects.filter(asset_tag__in=generated).exists():
            return Response({'detail': 'A generated tag was registered by another request. Please try again.'}, status=409)
        Asset.objects.bulk_create([
            Asset(**serializer.validated_data, created_by=request.user, updated_by=request.user)
            for serializer in serializers
        ])
    return Response({
        'created': len(prepared), 'item_types': len(rows),
        'first_tag': generated[0] if generated else None,
        'last_tag': generated[-1] if generated else None,
    }, status=201)


@api_view(['GET'])
def inventory_report(request):
    if not is_estate_officer(request.user):
        return Response({'detail': 'Estate Officer access required.'}, status=403)
    report_type = request.query_params.get('type', 'assets')
    wb = Workbook(); ws = wb.active
    if report_type == 'transfers':
        ws.title = 'Transfers'; ws.append(['Source Tag', 'Resulting Tag', 'Asset', 'Quantity', 'From', 'To', 'Responsible Office', 'Date', 'Reason'])
        for row in AssetTransfer.objects.select_related('asset', 'from_location', 'to_location'):
            ws.append([row.asset.asset_tag, row.resulting_asset.asset_tag if row.resulting_asset else '', row.asset.name, row.quantity, row.from_location.name, row.to_location.name,
                       row.new_responsible_office, row.transferred_at, row.reason])
    elif report_type == 'maintenance':
        ws.title = 'Maintenance'; ws.append(['Asset Tag', 'Asset', 'Quantity', 'Issue', 'Status', 'Provider', 'Cost', 'Reported', 'Completed'])
        for row in AssetMaintenance.objects.select_related('asset'):
            ws.append([row.asset.asset_tag, row.asset.name, row.quantity, row.issue, row.get_status_display(), row.provider,
                       row.cost, row.reported_date, row.completed_date])
    elif report_type == 'inspections':
        ws.title = 'Inspections'; ws.append(['Inspection Date', 'Location', 'Inspector', 'Status', 'Asset Tag', 'Asset', 'Result', 'Note'])
        for inspection in InventoryInspection.objects.select_related('location').prefetch_related('items__asset'):
            if inspection.items.exists():
                for item in inspection.items.all():
                    ws.append([inspection.inspection_date, inspection.location.name, inspection.inspector_name,
                               inspection.get_status_display(), item.asset.asset_tag, item.asset.name,
                               item.get_result_display(), item.note])
            else:
                ws.append([inspection.inspection_date, inspection.location.name, inspection.inspector_name,
                           inspection.get_status_display(), '', '', 'No items checked', ''])
    elif report_type == 'disposals':
        ws.title = 'Disposals'; ws.append(['Asset Tag', 'Asset', 'Status', 'Reason', 'Method', 'Proposed', 'Disposed', 'Reference'])
        for row in AssetDisposal.objects.select_related('asset'):
            ws.append([row.asset.asset_tag, row.asset.name, row.get_status_display(), row.reason, row.method,
                       row.proposed_date, row.disposal_date, row.reference])
    else:
        report_type = 'assets'; ws.title = 'Asset Register'
        ws.append(['Asset Tag', 'Asset Name', 'Description', 'Category', 'Location', 'Responsible Office', 'Quantity', 'Condition'])
        for row in Asset.objects.select_related('category', 'location'):
            ws.append([row.asset_tag, row.name, row.description, row.category.name, row.location.name,
                       row.responsible_office, row.quantity, row.get_condition_display()])
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF'); cell.fill = PatternFill('solid', fgColor='1F4E78')
    ws.freeze_panes = 'A2'; output = BytesIO(); wb.save(output)
    response = HttpResponse(output.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="inventory_{report_type}_report.xlsx"'
    return response


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
    if student.module.is_field_module:
        if result is None or result.field_ca is None:
            return None, None, 'Pending Field CA mark'
        total_ca = round(float(result.field_ca) * 0.4, 2)
        eligible = float(result.field_ca) >= 50
        return total_ca, eligible, (
            f'Field CA {result.field_ca}/100 meets the requirement'
            if eligible else f'Field CA {result.field_ca}/100 is below the required 50/100'
        )
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
                'is_field_module': module.is_field_module,
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
        CA_FIELDS = ['field_ca'] + CA_MARKS + [f'{field}_absent' for field in CA_MARKS]
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
                        absent_field = f'{field}_absent'
                        if hasattr(result, absent_field):
                            setattr(result, absent_field, False)
                    except (TypeError, ValueError):
                        errors.append(f'Result {item.get("id")}: invalid value for {field}')
                        continue
            result.save()
            saved += 1

        return Response({'saved': saved, 'errors': errors})


# ── RESULTS EXCEL DOWNLOAD (admin only) ────────────────────────────────────────

@login_required
def download_ca_signoff(request):
    """Create a module CA acknowledgement sheet for student signatures."""
    module_id = request.GET.get('module_id')
    if not module_id:
        return HttpResponse('module_id is required.', status=400)

    try:
        module = (
            user_modules(request.user)
            .select_related('class_level', 'semester__academic_year')
            .get(pk=module_id)
        )
    except (Module.DoesNotExist, ValueError):
        return HttpResponseForbidden('You do not have access to this module.')

    students = list(
        Student.objects.filter(module=module)
        .select_related('result')
        .order_by('name', 'nactvet_reg_no')
    )

    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    section.top_margin = section.bottom_margin = Inches(0.55)
    section.left_margin = section.right_margin = Inches(0.55)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run('CONTINUOUS ASSESSMENT RESULTS ACKNOWLEDGEMENT')
    run.bold = True
    run.font.size = Pt(14)
    details = document.add_paragraph()
    details.alignment = WD_ALIGN_PARAGRAPH.CENTER
    details.add_run(
        f'{module.code} — {module.name}\n'
        f'{module.class_level.name} · {module.semester.label} · '
        f'{module.semester.academic_year.name}'
    ).bold = True
    document.add_paragraph(
        'Each student should verify the CA shown below, then sign in the Student Signature column.'
    )

    headers = [
        '#', 'NACTVET Reg. No.', 'Student Name',
        'A1 /100', 'A2 /100', 'CAT 1 /100', 'CAT 2 /100',
    ]
    if module.has_practical:
        headers += ['Practical 1 /100', 'Practical 2 /100']
    headers += ['Total CA Average /40', 'Student Signature']
    table = document.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = 'Table Grid'
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = header
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(9)

    serializer = StudentResultSerializer()

    def mark_text(result, field):
        if not result:
            return '—'
        if getattr(result, f'{field}_absent'):
            return 'ABS'
        value = getattr(result, field)
        return '—' if value is None else f'{value:.2f}'

    for number, student in enumerate(students, 1):
        result = getattr(student, 'result', None)
        total_ca = serializer.get_total_ca(result) if result else None
        row = table.add_row()
        values = [
            number, student.nactvet_reg_no, student.name,
            mark_text(result, 'assign1'), mark_text(result, 'assign2'),
            mark_text(result, 'cat1_theory'), mark_text(result, 'cat2_theory'),
        ]
        if module.has_practical:
            values += [
                mark_text(result, 'cat1_practical'), mark_text(result, 'cat2_practical'),
            ]
        values += ['—' if total_ca is None else f'{total_ca:.2f}', '']
        for index, value in enumerate(values):
            row.cells[index].text = str(value)
            row.cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            row.cells[index].paragraphs[0].paragraph_format.space_after = Pt(0)
            row.cells[index].paragraphs[0].runs[0].font.size = Pt(7.5)
        row.height = Inches(0.42)

    widths = [0.3, 1.15, 1.7] + [0.62] * (len(headers) - 4) + [1.35]
    for row in table.rows:
        for index, width in enumerate(widths):
            row.cells[index].width = Inches(width)

    document.add_paragraph()
    footer = document.add_paragraph('Tutor/Verifier Name: __________________________   Signature: __________________')
    footer.runs[0].bold = True

    safe_code = re.sub(r'[^A-Za-z0-9_-]+', '_', module.code).strip('_') or 'module'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )
    response['Content-Disposition'] = f'attachment; filename="{safe_code}_ca_signoff.docx"'
    document.save(response)
    return response


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

    def nz(v):
        """Display substitution that keeps a real 0 distinct from 'not entered'."""
        return v if v is not None else ''

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
            nz(a1w), nz(a2w), nz(ct1w), nz(ct2w), nz(cp1w), nz(cp2w),
            nz(t_ca), nz(p_ca), nz(tot),
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


# ── FIELD RESULTS EXCEL DOWNLOAD (admin only) ──────────────────────────────────

@login_required
def download_field_results(request):
    """Export one field module in a compact CA 40% + report 60% workbook."""
    if not request.user.is_staff:
        return HttpResponseForbidden('Administrator access required.')

    module_id = request.GET.get('module_id')
    if not module_id:
        return HttpResponse('module_id is required.', status=400)
    try:
        module = (
            user_modules(request.user)
            .select_related('class_level', 'semester__academic_year')
            .get(pk=module_id, is_field_module=True)
        )
    except (Module.DoesNotExist, ValueError):
        return HttpResponseForbidden('Select a valid field results module.')

    students = (
        Student.objects.filter(module=module)
        .select_related('result')
        .order_by('name', 'nactvet_reg_no')
    )
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Field Results'
    navy, blue = '1E2D78', 'D9EAF7'
    thin = Side(style='thin', color='808080')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)

    title_rows = (
        'BLUE PHARMA COLLEGE OF HEALTH', 'FIELD RESULTS',
        f'{module.code} — {module.name}',
        f'{module.class_level.name} · {module.semester.label} · {module.semester.academic_year.name}',
    )
    for row_number, value in enumerate(title_rows, 1):
        ws.merge_cells(start_row=row_number, start_column=1, end_row=row_number, end_column=9)
        cell = ws.cell(row=row_number, column=1, value=value)
        cell.font = Font(bold=True, size=14 if row_number == 1 else 11, color=navy)
        cell.alignment = center

    headers = (
        'SN', 'NACTVET Registration Number', 'Student Name',
        'PPB/LOGBOOK', 'AV', 'REPORT', 'AV', 'TOTAL', 'STATUS',
    )
    for column, heading in enumerate(headers, 1):
        cell = ws.cell(row=6, column=column, value=heading)
        cell.fill = PatternFill('solid', fgColor=navy)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.alignment = center
        cell.border = border
    for column, heading in enumerate(('Raw /100', '40%', 'Raw /100', '60%'), 4):
        cell = ws.cell(row=5, column=column, value=heading)
        cell.fill = PatternFill('solid', fgColor=blue)
        cell.font = Font(bold=True, color='000000')
        cell.alignment = center
        cell.border = border

    serializer = StudentResultSerializer()
    for number, student in enumerate(students, 1):
        result = getattr(student, 'result', None)
        values = (
            number, student.nactvet_reg_no, student.name,
            result.field_ca if result else None,
            serializer.get_total_ca(result) if result else None,
            result.end_theory if result else None,
            serializer.get_end_theory_w(result) if result else None,
            serializer.get_final_total(result) if result else None,
            serializer.get_result_status(result) if result else 'INCOMPLETE',
        )
        for column, value in enumerate(values, 1):
            cell = ws.cell(row=6 + number, column=column, value='' if value is None else value)
            cell.alignment = Alignment(horizontal='left' if column == 3 else 'center', vertical='center')
            cell.border = border

    for column, width in enumerate((7, 26, 28, 16, 11, 14, 11, 11, 16), 1):
        ws.column_dimensions[get_column_letter(column)].width = width
    ws.freeze_panes = 'D7'
    ws.auto_filter.ref = f'A6:I{max(ws.max_row, 6)}'
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    safe_code = re.sub(r'[^A-Za-z0-9_-]+', '_', module.code).strip('_') or 'field'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = (
        f'attachment; filename="{safe_code}_field_results_{timezone.localdate()}.xlsx"'
    )
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

    def nz(v):
        """Display substitution that keeps a real 0 distinct from 'not entered'."""
        return v if v is not None else ''

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
            tot_ca     = round((t_ca or 0) + (p_ca or 0), 2) if (t_ca is not None or p_ca is not None) else None
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
            nz(t_ca), nz(p_ca), nz(tot_ca), yn(ca_elig),
            fmt(et), fmt(ep) if hp else 'N/A',
            nz(etw), nz(epw) if hp else 'N/A',
            nz(end_exam_total),
            nz(final), fmt(res.supplementary_mark), outcome['grade'] or '',
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
    basis           = request.GET.get('basis', 'all')

    my_modules = user_modules(request.user)
    students = (
        Student.objects.filter(module__in=my_modules)
        .select_related('module__class_level', 'module__semester__academic_year', 'result')
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

    if basis == 'performance':
        from collections import defaultdict

        enrollments = list(students)
        levels = defaultdict(list)
        for student in enrollments:
            levels[student.module.class_level.name].append(student)

        wb.remove(ws)
        used_titles = set()
        for level_name, level_students in levels.items():
            base_title = ''.join(c for c in level_name if c not in '[]:*?/\\')[:31] or 'Class'
            title = base_title
            suffix = 2
            while title in used_titles:
                title = f'{base_title[:27]} {suffix}'
                suffix += 1
            used_titles.add(title)
            level_ws = wb.create_sheet(title)
            module_codes = sorted({student.module.code for student in level_students})
            headers = ['Student Reg. No.', 'Name', *module_codes]
            level_ws.append(headers)
            for cell in level_ws[1]:
                cell.fill = HDR_FILL
                cell.font = HDR_FONT
                cell.alignment = CENTER

            student_rows = defaultdict(dict)
            student_names = {}
            module_summary = {code: {'eligible': 0, 'ineligible': 0} for code in module_codes}
            for student in level_students:
                _total_ca, eligible, _note = ca_eligibility_for_student(student)
                is_eligible = eligible is True
                student_names[student.nactvet_reg_no] = student.name
                student_rows[student.nactvet_reg_no][student.module.code] = is_eligible
                module_summary[student.module.code]['eligible' if is_eligible else 'ineligible'] += 1

            for reg_no in sorted(student_rows, key=lambda key: student_names[key].lower()):
                values = [reg_no, student_names[reg_no]]
                for code in module_codes:
                    values.append(
                        'Eligible' if student_rows[reg_no].get(code) is True
                        else ('Ineligible' if code in student_rows[reg_no] else '')
                    )
                level_ws.append(values)
                row_number = level_ws.max_row
                for column, code in enumerate(module_codes, 3):
                    cell = level_ws.cell(row=row_number, column=column)
                    if cell.value == 'Eligible':
                        cell.font = GREEN_F
                    elif cell.value == 'Ineligible':
                        cell.font = RED_F
                    cell.alignment = CENTER

            summary_start = level_ws.max_row + 3
            level_ws.cell(row=summary_start, column=1, value='Summary by Module').font = Font(bold=True, size=12)
            summary_headers = ['Module Code', 'Eligible', 'Ineligible', 'Total']
            for column, value in enumerate(summary_headers, 1):
                cell = level_ws.cell(row=summary_start + 1, column=column, value=value)
                cell.fill = HDR_FILL
                cell.font = HDR_FONT
                cell.alignment = CENTER

            class_eligible = class_ineligible = 0
            for offset, code in enumerate(module_codes, summary_start + 2):
                counts = module_summary[code]
                class_eligible += counts['eligible']
                class_ineligible += counts['ineligible']
                level_ws.append([code, counts['eligible'], counts['ineligible'], counts['eligible'] + counts['ineligible']])
                level_ws.cell(row=offset, column=2).font = GREEN_F
                level_ws.cell(row=offset, column=3).font = RED_F

            class_row = level_ws.max_row + 2
            level_ws.cell(row=class_row, column=1, value=f'{level_name} Total').font = Font(bold=True)
            level_ws.cell(row=class_row, column=2, value=class_eligible).font = GREEN_F
            level_ws.cell(row=class_row, column=3, value=class_ineligible).font = RED_F
            level_ws.cell(row=class_row, column=4, value=class_eligible + class_ineligible).font = Font(bold=True)
            level_ws.freeze_panes = 'C2'

            for column_cells in level_ws.columns:
                width = max(len(str(cell.value or '')) for cell in column_cells)
                level_ws.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(width + 3, 12), 28)

        if not wb.worksheets:
            empty_ws = wb.create_sheet('Eligibility')
            empty_ws.append(['No eligibility records found'])

        fname = f'module_eligibility_{timezone.localdate()}.xlsx'
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{fname}"'
        wb.save(response)
        return response

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


@login_required
def download_final_eligibility_excel(request):
    """Export attendance, CA marks and combined eligibility for NTA 4–6."""
    if not request.user.is_staff:
        return HttpResponseForbidden('Administrator access required.')

    from collections import defaultdict
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    semester_id = request.GET.get('semester_id')
    scope = request.GET.get('scope', '').strip().lower()
    modules = (
        user_modules(request.user)
        .filter(class_level__order__in=(4, 5, 6))
        .select_related('class_level', 'semester__academic_year')
        .order_by('class_level__order', 'code')
    )
    if semester_id:
        modules = modules.filter(semester_id=semester_id)
    if scope == 'semester_1':
        modules = modules.filter(semester__number=Semester.SEM1)
    elif scope == 'semester_2':
        modules = modules.filter(semester__number=Semester.SEM2)
    elif scope == 'field':
        modules = modules.filter(is_field_module=True)
    modules = list(modules)

    enrollments = list(
        Student.objects.filter(module__in=modules)
        .select_related('module', 'result')
        .prefetch_related('attendance_records__session')
        .order_by('name', 'nactvet_reg_no', 'module__code')
    )

    module_session_counts = {
        module.id: Session.objects.filter(module=module).count()
        for module in modules
    }
    enrollment_data = {}
    students_by_level = defaultdict(dict)
    for student in enrollments:
        records = list(student.attendance_records.all())
        total_sessions = module_session_counts[student.module_id]
        attended = sum(1 for record in records if attendance_is_effective(record))
        attendance_pct = round(attended / total_sessions * 100, 1) if total_sessions else None
        ca_total, ca_eligible, _ca_note = ca_eligibility_for_student(student)
        attendance_eligible = (
            attendance_pct >= ELIGIBILITY_THRESHOLD
            if attendance_pct is not None else None
        )
        final_eligible = (
            None if ca_eligible is None or attendance_eligible is None
            else ca_eligible and attendance_eligible
        )
        enrollment_data[(student.nactvet_reg_no, student.module_id)] = {
            'student': student,
            'sessions': total_sessions,
            'attended': attended,
            'attendance_pct': attendance_pct,
            'ca_total': ca_total,
            'ca_eligible': ca_eligible,
            'final_eligible': final_eligible,
        }
        students_by_level[student.module.class_level.order][student.nactvet_reg_no] = student.name

    level_modules = defaultdict(list)
    for module in modules:
        level_modules[module.class_level.order].append(module)

    navy = '1E2D78'
    blue = 'D9EAF7'
    green = 'C6EFCE'
    green_text = '006100'
    red = 'FFC7CE'
    red_text = '9C0006'
    yellow = 'FFF2CC'
    yellow_text = '7F6000'
    white = 'FFFFFF'
    thin = Side(style='thin', color='808080')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)

    def style_header(cell, fill=navy):
        cell.fill = PatternFill('solid', fgColor=fill)
        cell.font = Font(bold=True, color=white)
        cell.alignment = center
        cell.border = border

    def style_status(cell, status):
        if status is True or status == 'Eligible':
            cell.fill = PatternFill('solid', fgColor=green)
            cell.font = Font(bold=True, color=green_text)
        elif status is False or status == 'Ineligible':
            cell.fill = PatternFill('solid', fgColor=red)
            cell.font = Font(bold=True, color=red_text)
        else:
            cell.fill = PatternFill('solid', fgColor=yellow)
            cell.font = Font(bold=True, color=yellow_text)
        cell.alignment = center
        cell.border = border

    def finish_sheet(ws):
        ws.freeze_panes = 'D8'
        ws.auto_filter.ref = f'A7:{get_column_letter(ws.max_column)}{ws.max_row}'
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        for column in range(1, ws.max_column + 1):
            width = max(len(str(ws.cell(row=row, column=column).value or '')) for row in range(1, ws.max_row + 1))
            ws.column_dimensions[get_column_letter(column)].width = min(max(width + 2, 11), 24)
        ws.column_dimensions['B'].width = max(ws.column_dimensions['B'].width, 24)
        ws.column_dimensions['C'].width = max(ws.column_dimensions['C'].width, 23)

    def prepare_sheet(ws, level, title, last_column):
        last_letter = get_column_letter(max(last_column, 3))
        headings = [
            'BLUE PHARMA COLLEGE OF HEALTH',
            'DEPARTMENT OF PHARMACEUTICAL SCIENCES',
            f'NTA LEVEL {level}',
            title,
        ]
        for row, heading in enumerate(headings, 1):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(last_column, 3))
            cell = ws.cell(row=row, column=1, value=heading)
            cell.font = Font(bold=True, size=14 if row == 1 else 11, color=navy)
            cell.alignment = center
        for column, value in enumerate(('SN', 'Name', 'NACTVET Registration Number'), 1):
            style_header(ws.cell(row=5, column=column, value=value))
            ws.merge_cells(start_row=5, start_column=column, end_row=7, end_column=column)
        ws.row_dimensions[5].height = 36
        ws.print_title_rows = '1:7'
        ws.print_area = f'A1:{last_letter}{max(ws.max_row, 5)}'

    def result_values(data):
        result = getattr(data['student'], 'result', None) if data else None
        if result is None:
            return result, ('', '', '', ''), '', '', ''
        serializer = StudentResultSerializer()
        theory = serializer.get_theory_ca(result)
        practical = serializer.get_practical_ca(result)
        return result, (
            result.cat1_theory, result.cat2_theory,
            result.assign1, result.assign2,
        ), theory, practical, data['ca_total']

    def field_result_values(data):
        result = getattr(data['student'], 'result', None) if data else None
        if result is None:
            return '', '', '', ''
        serializer = StudentResultSerializer()
        return (
            result.field_ca,
            serializer.get_total_ca(result),
            result.end_theory,
            serializer.get_end_theory_w(result),
        )

    def write_mark(cell, value, pass_mark=50):
        cell.value = value if value is not None else ''
        cell.alignment = center
        cell.border = border
        if value is not None and value != '':
            style_status(cell, float(value) >= pass_mark)

    wb = Workbook()
    wb.remove(wb.active)

    for level in (4, 5, 6):
        level_module_list = level_modules[level]
        student_map = students_by_level[level]

        assignments_ws = wb.create_sheet(f'NTA {level} Assignments')
        assignment_width = 2 * len(level_module_list)
        prepare_sheet(assignments_ws, level, 'ASSIGNMENT RESULTS', 3 + assignment_width)
        assignment_starts = {}
        column = 4
        for module in level_module_list:
            assignment_starts[module.id] = column
            assignments_ws.merge_cells(start_row=5, start_column=column, end_row=5, end_column=column + 1)
            style_header(assignments_ws.cell(row=5, column=column, value=f'{module.name} ({module.code})'))
            assignments_ws.merge_cells(start_row=6, start_column=column, end_row=6, end_column=column + 1)
            style_header(assignments_ws.cell(row=6, column=column, value='Assignments'), blue)
            assignments_ws.cell(row=6, column=column).font = Font(bold=True, color='000000')
            for offset, heading in enumerate(('ASS1', 'ASS2')):
                style_header(assignments_ws.cell(row=7, column=column + offset, value=heading), blue)
                assignments_ws.cell(row=7, column=column + offset).font = Font(bold=True, color='000000')
            column += 2
        for row_number, (reg_no, name) in enumerate(sorted(student_map.items(), key=lambda item: item[1].lower()), 8):
            assignments_ws.append([row_number - 7, name, reg_no])
            for module in level_module_list:
                data = enrollment_data.get((reg_no, module.id))
                result = getattr(data['student'], 'result', None) if data else None
                a1 = result.assign1 if result else None
                a2 = result.assign2 if result else None
                start = assignment_starts[module.id]
                for offset, value in enumerate((a1, a2)):
                    write_mark(assignments_ws.cell(row=row_number, column=start + offset), value)
        finish_sheet(assignments_ws)

        cats_ws = wb.create_sheet(f'NTA {level} CATs')
        cat_width = sum(
            2 if module.is_field_module else (9 if module.has_practical else 5)
            for module in level_module_list
        )
        prepare_sheet(cats_ws, level, 'CONTINUOUS ASSESSMENT RESULTS', 3 + cat_width)
        cat_starts = {}
        column = 4
        for module in level_module_list:
            width = 2 if module.is_field_module else (9 if module.has_practical else 5)
            cat_starts[module.id] = column
            cats_ws.merge_cells(start_row=5, start_column=column, end_row=5, end_column=column + width - 1)
            style_header(cats_ws.cell(row=5, column=column, value=f'{module.name} ({module.code})'))
            if module.is_field_module:
                for offset, heading in enumerate(('PPB/LOGBOOK', 'AV')):
                    style_header(cats_ws.cell(row=6, column=column + offset, value=heading), blue)
                    cats_ws.cell(row=6, column=column + offset).font = Font(bold=True, color='000000')
                    cats_ws.merge_cells(start_row=6, start_column=column + offset, end_row=7, end_column=column + offset)
                column += width
                continue
            cats_ws.merge_cells(start_row=6, start_column=column, end_row=6, end_column=column + 4)
            style_header(cats_ws.cell(row=6, column=column, value='Theory'), blue)
            cats_ws.cell(row=6, column=column).font = Font(bold=True, color='000000')
            for offset, heading in enumerate(('WR1', 'WR2', 'AS1', 'ASS2', 'AV')):
                style_header(cats_ws.cell(row=7, column=column + offset, value=heading), blue)
                cats_ws.cell(row=7, column=column + offset).font = Font(bold=True, color='000000')
            if module.has_practical:
                cats_ws.merge_cells(start_row=6, start_column=column + 5, end_row=6, end_column=column + 7)
                style_header(cats_ws.cell(row=6, column=column + 5, value='Practical'), blue)
                cats_ws.cell(row=6, column=column + 5).font = Font(bold=True, color='000000')
                for offset, heading in enumerate(('PRAC1', 'PRAC2', 'AV'), 5):
                    style_header(cats_ws.cell(row=7, column=column + offset, value=heading), blue)
                    cats_ws.cell(row=7, column=column + offset).font = Font(bold=True, color='000000')
                style_header(cats_ws.cell(row=6, column=column + 8, value='Total'), blue)
                cats_ws.cell(row=6, column=column + 8).font = Font(bold=True, color='000000')
                style_header(cats_ws.cell(row=7, column=column + 8, value='AV'), blue)
                cats_ws.cell(row=7, column=column + 8).font = Font(bold=True, color='000000')
            column += width
        for row_number, (reg_no, name) in enumerate(sorted(student_map.items(), key=lambda item: item[1].lower()), 8):
            cats_ws.append([row_number - 7, name, reg_no])
            for module in level_module_list:
                data = enrollment_data.get((reg_no, module.id))
                if module.is_field_module:
                    field_ca, field_ca_av, _report, _report_av = field_result_values(data)
                    start = cat_starts[module.id]
                    write_mark(cats_ws.cell(row=row_number, column=start), field_ca)
                    write_mark(cats_ws.cell(row=row_number, column=start + 1), field_ca_av, 20)
                    continue
                result, theory_raw, theory_av, practical_av, total_av = result_values(data)
                start = cat_starts[module.id]
                for offset, value in enumerate(theory_raw):
                    write_mark(cats_ws.cell(row=row_number, column=start + offset), value)
                write_mark(cats_ws.cell(row=row_number, column=start + 4), theory_av, 10 if module.has_practical else 20)
                if module.has_practical:
                    practical_raw = (result.cat1_practical, result.cat2_practical) if result else ('', '')
                    write_mark(cats_ws.cell(row=row_number, column=start + 5), practical_raw[0])
                    write_mark(cats_ws.cell(row=row_number, column=start + 6), practical_raw[1])
                    write_mark(cats_ws.cell(row=row_number, column=start + 7), practical_av, 10)
                    write_mark(cats_ws.cell(row=row_number, column=start + 8), total_av, CA_ELIGIBILITY_THRESHOLD)
        finish_sheet(cats_ws)

        eligibility_ws = wb.create_sheet(f'NTA {level} Eligibility')
        eligibility_width = sum(
            6 if module.is_field_module else (11 if module.has_practical else 7)
            for module in level_module_list
        )
        prepare_sheet(eligibility_ws, level, 'FINAL ELIGIBILITY TO END-OF-SEMESTER EXAMINATION', 3 + eligibility_width)
        eligibility_starts = {}
        column = 4
        for module in level_module_list:
            if module.is_field_module:
                mark_width = 4
                width = 6
                eligibility_starts[module.id] = column
                eligibility_ws.merge_cells(start_row=5, start_column=column, end_row=5, end_column=column + width - 1)
                style_header(eligibility_ws.cell(row=5, column=column, value=f'{module.name} ({module.code})'))
                for offset, heading in enumerate(('PPB/LOGBOOK', 'AV', 'REPORT', 'AV')):
                    style_header(eligibility_ws.cell(row=6, column=column + offset, value=heading), blue)
                    eligibility_ws.cell(row=6, column=column + offset).font = Font(bold=True, color='000000')
                    eligibility_ws.merge_cells(start_row=6, start_column=column + offset, end_row=7, end_column=column + offset)
                for offset, heading in enumerate(('Attendance (%)', 'Final Eligibility'), mark_width):
                    style_header(eligibility_ws.cell(row=6, column=column + offset, value=heading), blue)
                    eligibility_ws.cell(row=6, column=column + offset).font = Font(bold=True, color='000000')
                    eligibility_ws.merge_cells(start_row=6, start_column=column + offset, end_row=7, end_column=column + offset)
                column += width
                continue
            mark_width = 9 if module.has_practical else 5
            width = mark_width + 2
            eligibility_starts[module.id] = column
            eligibility_ws.merge_cells(start_row=5, start_column=column, end_row=5, end_column=column + width - 1)
            style_header(eligibility_ws.cell(row=5, column=column, value=f'{module.name} ({module.code})'))
            eligibility_ws.merge_cells(start_row=6, start_column=column, end_row=6, end_column=column + 4)
            style_header(eligibility_ws.cell(row=6, column=column, value='Theory'), blue)
            eligibility_ws.cell(row=6, column=column).font = Font(bold=True, color='000000')
            for offset, heading in enumerate(('WR1', 'WR2', 'AS1', 'ASS2', 'AV')):
                style_header(eligibility_ws.cell(row=7, column=column + offset, value=heading), blue)
                eligibility_ws.cell(row=7, column=column + offset).font = Font(bold=True, color='000000')
            if module.has_practical:
                eligibility_ws.merge_cells(start_row=6, start_column=column + 5, end_row=6, end_column=column + 7)
                style_header(eligibility_ws.cell(row=6, column=column + 5, value='Practical'), blue)
                eligibility_ws.cell(row=6, column=column + 5).font = Font(bold=True, color='000000')
                for offset, heading in enumerate(('PRAC1', 'PRAC2', 'AV'), 5):
                    style_header(eligibility_ws.cell(row=7, column=column + offset, value=heading), blue)
                    eligibility_ws.cell(row=7, column=column + offset).font = Font(bold=True, color='000000')
                style_header(eligibility_ws.cell(row=6, column=column + 8, value='Total'), blue)
                eligibility_ws.cell(row=6, column=column + 8).font = Font(bold=True, color='000000')
                style_header(eligibility_ws.cell(row=7, column=column + 8, value='AV'), blue)
                eligibility_ws.cell(row=7, column=column + 8).font = Font(bold=True, color='000000')
            for offset, heading in enumerate(('Attendance (%)', 'Final Eligibility'), mark_width):
                style_header(eligibility_ws.cell(row=6, column=column + offset, value=heading), blue)
                eligibility_ws.cell(row=6, column=column + offset).font = Font(bold=True, color='000000')
                eligibility_ws.merge_cells(start_row=6, start_column=column + offset, end_row=7, end_column=column + offset)
            column += width
        for row_number, (reg_no, name) in enumerate(sorted(student_map.items(), key=lambda item: item[1].lower()), 8):
            eligibility_ws.append([row_number - 7, name, reg_no])
            for module in level_module_list:
                data = enrollment_data.get((reg_no, module.id))
                start = eligibility_starts[module.id]
                if module.is_field_module:
                    mark_width = 4
                    field_marks = field_result_values(data)
                    for offset, value in enumerate(field_marks):
                        pass_mark = 20 if offset == 1 else (30 if offset == 3 else 50)
                        write_mark(eligibility_ws.cell(row=row_number, column=start + offset), value, pass_mark)
                else:
                    result, theory_raw, theory_av, practical_av, total_av = result_values(data)
                    for offset, value in enumerate(theory_raw):
                        write_mark(eligibility_ws.cell(row=row_number, column=start + offset), value)
                    write_mark(eligibility_ws.cell(row=row_number, column=start + 4), theory_av, 10 if module.has_practical else 20)
                    mark_width = 5
                    if module.has_practical:
                        practical_raw = (result.cat1_practical, result.cat2_practical) if result else ('', '')
                        write_mark(eligibility_ws.cell(row=row_number, column=start + 5), practical_raw[0])
                        write_mark(eligibility_ws.cell(row=row_number, column=start + 6), practical_raw[1])
                        write_mark(eligibility_ws.cell(row=row_number, column=start + 7), practical_av, 10)
                        write_mark(eligibility_ws.cell(row=row_number, column=start + 8), total_av, CA_ELIGIBILITY_THRESHOLD)
                        mark_width = 9
                attendance_cell = eligibility_ws.cell(row=row_number, column=start + mark_width, value=data['attendance_pct'] if data and data['attendance_pct'] is not None else '')
                style_status(attendance_cell, None if not data or data['attendance_pct'] is None else data['attendance_pct'] >= ELIGIBILITY_THRESHOLD)
                status = 'INCOMPLETE' if not data or data['final_eligible'] is None else ('Eligible' if data['final_eligible'] else 'Ineligible')
                eligibility_cell = eligibility_ws.cell(row=row_number, column=start + mark_width + 1, value=status)
                style_status(eligibility_cell, data['final_eligible'] if data else None)
        summary_start = eligibility_ws.max_row + 2
        summary_labels = ('Number of Eligible Students', 'Number of Ineligible Students', 'Number of Incomplete Students')
        for offset, label in enumerate(summary_labels):
            cell = eligibility_ws.cell(row=summary_start + offset, column=2, value=label)
            cell.font = Font(bold=True)
            cell.border = border
        for module in level_module_list:
            statuses = [
                enrollment_data[(reg_no, module.id)]['final_eligible']
                for reg_no in student_map
                if (reg_no, module.id) in enrollment_data
            ]
            counts = (
                sum(status is True for status in statuses),
                sum(status is False for status in statuses),
                sum(status is None for status in statuses),
            )
            mark_width = 4 if module.is_field_module else (9 if module.has_practical else 5)
            status_column = eligibility_starts[module.id] + mark_width + 1
            for offset, count in enumerate(counts):
                cell = eligibility_ws.cell(row=summary_start + offset, column=status_column, value=count)
                style_status(cell, True if offset == 0 else (False if offset == 1 else None))
        finish_sheet(eligibility_ws)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="final_eligibility_{timezone.localdate()}.xlsx"'
    wb.save(response)
    return response
