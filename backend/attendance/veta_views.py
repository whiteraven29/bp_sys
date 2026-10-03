"""The API for VETA courses and for expenses.

Kept beside views.py rather than in it; the office rules are the same ones
(roles.py, and views.can_work_desk for the admission desks).
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from . import admissions, expenses, finance, veta
from .models import (
    AcademicYear, BankAccount, ChargeType, CourseApplication, CourseEnrollment, CourseFee,
    CourseFeeInstalment, CourseModule, Expense, ExpenseCategory, Intake, NextOfKin, PettyCashTopUp, Programme,
)
from .roles import (
    can_approve_expenses, can_manage_exams, can_view_expenses, is_accountant, is_admission_officer,
    is_break_glass, is_dparc, is_principal, is_records_officer, runs_veta_courses,
)
from .views import (
    active_academic_year, can_see_admissions, can_work_desk, full_name_for,
    visible_application_states,
)


def _bad(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': str(message)}, status=code)


def _year(request, field='academic_year_id'):
    raw = request.data.get(field) if request.method == 'POST' else request.query_params.get(field)
    return AcademicYear.objects.filter(id=raw).first() or active_academic_year()


def _money(raw):
    try:
        return finance.money(Decimal(str(raw)))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('That is not an amount.')


# ── who may do what, for these screens ───────────────────────────────────────

def _opens_intakes(user):
    return is_break_glass(user) or is_admission_officer(user) or is_principal(user)


def _reads_veta(user):
    return (can_see_admissions(user) or runs_veta_courses(user) or is_accountant(user)
            or can_manage_exams(user))


def _hides_money(user):
    """DPARC and the course coordinator read admissions, not the money in them."""
    return not (is_principal(user) or is_accountant(user) or is_admission_officer(user)
                or is_records_officer(user) or is_break_glass(user))


# ── intakes ───────────────────────────────────────────────────────────────────

def _intake_row(intake):
    return {
        'id': intake.id, 'name': str(intake), 'programme': intake.programme_id,
        'programme_name': intake.programme.name, 'programme_code': intake.programme.code,
        'structure': intake.programme.structure,
        'structure_display': intake.programme.get_structure_display(),
        'academic_year': intake.academic_year_id, 'academic_year_name': intake.academic_year.name,
        'starts_on': intake.starts_on, 'ends_on': intake.ends_on, 'capacity': intake.capacity,
        'is_open': intake.is_open, 'note': intake.note,
        'enrolled': veta.places_taken(intake),
        'applying': intake.applications.filter(state__in=CourseApplication.OPEN_STATES).count(),
    }


@api_view(['GET', 'POST'])
def intakes(request):
    if not _reads_veta(request.user):
        raise PermissionDenied('For the admission, records and finance offices and the course coordinator.')
    if request.method == 'GET':
        qs = Intake.objects.select_related('programme', 'academic_year')
        if request.query_params.get('programme_id'):
            qs = qs.filter(programme_id=request.query_params['programme_id'])
        if request.query_params.get('open') == '1':
            qs = qs.filter(is_open=True)
        return Response([_intake_row(i) for i in qs])
    if not _opens_intakes(request.user):
        raise PermissionDenied('The admission officer opens an intake.')
    programme = Programme.objects.filter(id=request.data.get('programme')).first()
    starts_on = parse_date(str(request.data.get('starts_on') or ''))
    if programme is None or starts_on is None:
        return _bad('Pick the course and the start date.')
    ends_on = parse_date(str(request.data.get('ends_on') or '')) if request.data.get('ends_on') else None
    capacity = request.data.get('capacity') or None
    try:
        intake = veta.open_intake(
            programme=programme, academic_year=_year(request), starts_on=starts_on, ends_on=ends_on,
            name=request.data.get('name') or '', capacity=int(capacity) if capacity else None,
            note=request.data.get('note') or '', actor=request.user)
    except (veta.VetaError, ValueError) as exc:
        return _bad(exc)
    return Response(_intake_row(intake), status=status.HTTP_201_CREATED)


@api_view(['PATCH'])
def intake_detail(request, pk):
    if not _opens_intakes(request.user):
        raise PermissionDenied('The admission officer keeps the intakes.')
    intake = get_object_or_404(Intake, pk=pk)
    for field in ('is_open',):
        if field in request.data:
            setattr(intake, field, bool(request.data[field]))
    if 'capacity' in request.data:
        intake.capacity = int(request.data['capacity']) if request.data['capacity'] else None
    if 'note' in request.data:
        intake.note = str(request.data['note'])[:300]
    intake.save()
    return Response(_intake_row(intake))


# ── course fees ───────────────────────────────────────────────────────────────

def _fee_row(fee):
    account = fee.charge_type.bank_account
    return {
        'id': fee.id, 'programme': fee.programme_id, 'academic_year': fee.academic_year_id,
        'charge_type': fee.charge_type_id, 'charge_type_name': fee.charge_type.name,
        'account': f'{account.bank_name} · {account.account_number}' if account else '',
        'account_purpose': account.purpose if account else '',
        'amount': str(fee.amount), 'hostel_only': fee.hostel_only,
        'instalments': [{'number': p.number, 'amount': str(p.amount), 'due_after_days': p.due_after_days,
                         'before_admission': p.before_admission}
                        for p in fee.instalments.all()],
    }


@api_view(['GET', 'POST'])
def course_fees(request):
    """A VETA course's fees for one year, set by the accountant."""
    if request.method == 'GET':
        if not _reads_veta(request.user):
            raise PermissionDenied('For the offices that run admissions and fees.')
        qs = (CourseFee.objects.select_related('charge_type__bank_account')
              .prefetch_related('instalments').filter(academic_year=_year(request)))
        if request.query_params.get('programme_id'):
            qs = qs.filter(programme_id=request.query_params['programme_id'])
        return Response([_fee_row(f) for f in qs])

    if not is_accountant(request.user):
        raise PermissionDenied('Course fees are set by the accountant.')
    programme = Programme.objects.filter(id=request.data.get('programme')).first()
    charge_type = ChargeType.objects.filter(id=request.data.get('charge_type'), is_active=True).first()
    if programme is None or not programme.is_veta:
        return _bad('Pick a VETA course.')
    if charge_type is None:
        return _bad('Pick what is charged (a charge type; it says which account is paid into).')
    try:
        amount = _money(request.data.get('amount'))
        parts = [(int(p.get('due_after_days') or 0), _money(p.get('amount')), bool(p.get('before_admission')))
                 for p in (request.data.get('instalments') or [])]
    except ValueError as exc:
        return _bad(exc)
    if amount <= 0:
        return _bad('The amount must be more than zero.')
    if parts and sum(p[1] for p in parts) != amount:
        return _bad(f'The instalments add up to {sum(p[1] for p in parts):,.2f}, not {amount:,.2f}.')
    if not parts:
        parts = [(0, amount, False)]          # all at the start
    if request.data.get('first_before_admission'):
        parts = sorted(parts)
        parts[0] = (parts[0][0], parts[0][1], True)
    year = _year(request)
    with transaction.atomic():
        fee, _ = CourseFee.objects.update_or_create(
            programme=programme, academic_year=year, charge_type=charge_type,
            defaults={'amount': amount, 'hostel_only': bool(request.data.get('hostel_only')),
                      'updated_by': request.user})
        fee.instalments.all().delete()
        for number, (days, part, before) in enumerate(sorted(parts), start=1):
            CourseFeeInstalment.objects.create(fee=fee, number=number, amount=part, due_after_days=days,
                                               before_admission=before)
        finance.audit('course_fee.set', 'CourseFee', actor=request.user, entity_id=fee.id,
                      summary=f'{programme.code} {year}: {charge_type} {amount} in {len(parts) or 1}')
    fee.refresh_from_db()
    return Response(_fee_row(fee), status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
def course_fee_detail(request, pk):
    if not is_accountant(request.user):
        raise PermissionDenied('Course fees are set by the accountant.')
    fee = get_object_or_404(CourseFee, pk=pk)
    finance.audit('course_fee.remove', 'CourseFee', actor=request.user, entity_id=fee.id,
                  summary=f'{fee.programme.code} {fee.academic_year}: {fee.charge_type} removed')
    fee.delete()          # charges already raised are separate rows and stay
    return Response(status=status.HTTP_204_NO_CONTENT)


# ── course modules ────────────────────────────────────────────────────────────

def _module_row(m):
    return {'id': m.id, 'programme': m.programme_id, 'code': m.code, 'name': m.name,
            'nva_level': m.nva_level, 'order': m.order, 'is_active': m.is_active}


@api_view(['GET', 'POST'])
def course_modules(request):
    if request.method == 'GET':
        if not _reads_veta(request.user):
            raise PermissionDenied('For the course coordinator and the offices above.')
        qs = CourseModule.objects.all()
        if request.query_params.get('programme_id'):
            qs = qs.filter(programme_id=request.query_params['programme_id'])
        return Response([_module_row(m) for m in qs])
    if not runs_veta_courses(request.user):
        raise PermissionDenied('The course coordinator keeps the modules.')
    programme = Programme.objects.filter(id=request.data.get('programme')).first()
    code = str(request.data.get('code') or '').strip().upper()
    name = str(request.data.get('name') or '').strip()
    if programme is None or not programme.is_veta or not code or not name:
        return _bad('Pick a VETA course and give the module a code and a name.')
    level = request.data.get('nva_level') or None
    if programme.structure == Programme.NVA:
        if not level or not 1 <= int(level) <= (programme.nva_top_level or 3):
            return _bad(f'Which NVA level is it at (1 to {programme.nva_top_level or 3})?')
    else:
        level = None
    module, created = CourseModule.objects.update_or_create(
        programme=programme, code=code,
        defaults={'name': name, 'nva_level': int(level) if level else None,
                  'order': int(request.data.get('order') or 0),
                  'is_active': request.data.get('is_active', True) not in (False, 'false', '0')})
    return Response(_module_row(module), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


# ── applications through the desks ────────────────────────────────────────────

def _visible_applications(user):
    qs = CourseApplication.objects.select_related('profile', 'intake__programme')
    if runs_veta_courses(user):
        return qs
    states = visible_application_states(user)
    return qs if states is None else qs.filter(state__in=sorted(states))


def _application_row(app, user, *, full=False):
    row = {
        'id': app.id, 'state': app.state, 'state_display': app.get_state_display(),
        'is_open': app.is_open, 'intake': app.intake_id, 'intake_name': str(app.intake),
        'programme_code': app.intake.programme.code, 'profile': app.profile_id,
        'name': app.profile.name, 'reg_no': app.profile.nactvet_reg_no,
        'college_id': app.profile.college_id, 'residence': app.residence,
        'fees_raised': app.fees_raised_at is not None, 'created_at': app.created_at,
        'can_work': app.is_open and can_work_desk(user, app.state),
    }
    if not full:
        return row
    profile = app.profile
    row.update({
        'phone': profile.phone, 'gender': profile.gender, 'date_of_birth': profile.date_of_birth,
        'next_of_kin': [{'position': k.position, 'name': k.name, 'phone': k.phone,
                         'relationship': k.relationship} for k in profile.next_of_kin.all()],
        'blockers': veta.desk_blockers(app) if app.is_open else [],
        'can_refuse': app.is_open and (is_admission_officer(user) or is_principal(user)
                                       or is_break_glass(user)),
        'steps': [{'desk': s.desk, 'action': s.get_action_display(), 'note': s.note,
                   'by': full_name_for(s.done_by) if s.done_by_id else '', 'at': s.done_at}
                  for s in app.steps.select_related('done_by')],
        'note': app.note,
    })
    if not _hides_money(user):
        row['money'] = veta.money_position(app)
        row['fees'] = [_fee_row(f) for f in veta.fees_for(app.intake)]
    return row


@api_view(['GET', 'POST'])
def course_applications(request):
    user = request.user
    if request.method == 'GET':
        if not _reads_veta(user):
            raise PermissionDenied('For the admission desks.')
        qs = _visible_applications(user)
        for param, field in (('state', 'state'), ('intake_id', 'intake_id')):
            if request.query_params.get(param):
                qs = qs.filter(**{field: request.query_params[param]})
        return Response([_application_row(a, user) for a in qs[:500]])
    if not can_work_desk(user, CourseApplication.INTAKE):
        raise PermissionDenied('The admission office takes a student on.')
    intake = Intake.objects.filter(id=request.data.get('intake')).first()
    if intake is None:
        return _bad('Pick the intake.')
    try:
        app = veta.open_application(
            intake=intake, name=str(request.data.get('name') or ''),
            phone=str(request.data.get('phone') or ''),
            registration_number=str(request.data.get('registration_number') or ''),
            note=str(request.data.get('note') or ''), actor=user)
    except (veta.VetaError, admissions.AdmissionError) as exc:
        return _bad(exc)
    return Response(_application_row(app, user, full=True), status=status.HTTP_201_CREATED)


@api_view(['GET', 'POST'])
def course_application_action(request, pk, action=None):
    user = request.user
    app = get_object_or_404(_visible_applications(user), pk=pk) if _reads_veta(user) else None
    if app is None:
        raise Http404
    if request.method == 'GET':
        return Response(_application_row(app, user, full=True))
    note = str(request.data.get('note') or '')
    try:
        if action == 'details':
            if not (app.state == CourseApplication.INFORMATION and can_work_desk(user, app.state)
                    or is_principal(user)):
                raise PermissionDenied('The records desk takes the details.')
            kin = request.data.get('next_of_kin') or []
            for entry in kin:
                if entry.get('relationship') and entry['relationship'] not in dict(NextOfKin.RELATIONSHIP_CHOICES):
                    return _bad(f"{entry['relationship']!r} is not a relationship the form knows.")
            admissions.record_details(
                app.profile, name=request.data.get('name'), phone=request.data.get('phone'),
                gender=request.data.get('gender'),
                date_of_birth=parse_date(str(request.data['date_of_birth'])) if request.data.get('date_of_birth') else None,
                next_of_kin=kin, actor=user)
        elif action == 'fees':
            if not can_work_desk(user, CourseApplication.FINANCE) or app.state != CourseApplication.FINANCE:
                raise PermissionDenied('Finance raises the fees at its own desk.')
            veta.raise_fees(app, residence=request.data.get('residence'), actor=user)
        elif action == 'clear':
            if not can_work_desk(user, app.state):
                raise PermissionDenied('This desk is worked by another office.')
            cleared = veta.clear_desk(app, actor=user, note=note)
            app.refresh_from_db()
            row = _application_row(app, user, full=True)
            # Shown once, for the admission slip; only the hash is kept.
            row['portal_password'] = getattr(cleared, 'issued_pin', None)
            return Response(row)
        elif action == 'send-back':
            if not can_work_desk(user, app.state):
                raise PermissionDenied('This desk is worked by another office.')
            veta.send_back(app, actor=user, note=note)
        elif action == 'refuse':
            if not (is_admission_officer(user) or is_principal(user) or is_break_glass(user)):
                raise PermissionDenied('Refusing is the admission office\'s decision.')
            veta.refuse(app, actor=user, note=note)
        elif action == 'reset-pin':
            if app.state != CourseApplication.ADMITTED or not (
                    is_admission_officer(user) or is_records_officer(user) or is_principal(user)):
                raise PermissionDenied('The admission or records office resets a portal password.')
            pin = veta.reset_portal_pin(app.profile)
            return Response({'portal_password': pin, 'username': app.profile.college_id or app.profile.nactvet_reg_no})
        else:
            raise Http404
    except (veta.VetaError, admissions.AdmissionError) as exc:
        return _bad(exc)
    app.refresh_from_db()
    return Response(_application_row(app, user, full=True))


@api_view(['GET'])
def course_enrollments(request):
    if not _reads_veta(request.user):
        raise PermissionDenied('For the course coordinator and the offices above.')
    qs = CourseEnrollment.objects.select_related('profile', 'intake__programme')
    if request.query_params.get('intake_id'):
        qs = qs.filter(intake_id=request.query_params['intake_id'])
    return Response([{
        'id': e.id, 'profile': e.profile_id, 'name': e.profile.name,
        'reg_no': e.profile.nactvet_reg_no, 'college_id': e.profile.college_id,
        'phone': e.profile.phone, 'intake': e.intake_id, 'intake_name': str(e.intake),
        'status': e.status, 'status_display': e.get_status_display(), 'residence': e.residence,
        'nva_level': e.nva_level, 'admitted_at': e.admitted_at,
    } for e in qs[:1000]])


# ── expenses ──────────────────────────────────────────────────────────────────

def _expense_row(e, user):
    over = expenses.would_exceed(e) if e.status == Expense.PENDING else Decimal('0')
    return {
        'id': e.id, 'academic_year': e.academic_year_id, 'academic_year_name': e.academic_year.name,
        'category': e.category_id, 'category_name': e.category.name,
        'description': e.description, 'payee': e.payee, 'amount': str(e.amount),
        'incurred_on': e.incurred_on, 'supplier_reference': e.supplier_reference,
        'has_attachment': bool(e.attachment),
        'status': e.status, 'status_display': e.get_status_display(),
        'recorded_by': full_name_for(e.recorded_by) if e.recorded_by_id else '',
        'recorded_at': e.recorded_at,
        'decided_by': full_name_for(e.decided_by) if e.decided_by_id else '',
        'decided_at': e.decided_at, 'decision_note': e.decision_note,
        'paid_on': e.paid_on, 'payment_method': e.get_payment_method_display() if e.payment_method else '',
        'bank_account': (f'{e.bank_account.bank_name} · {e.bank_account.account_number}'
                         if e.bank_account_id else ''),
        'payment_reference': e.payment_reference, 'cancelled_reason': e.cancelled_reason,
        'source': e.source, 'source_display': e.get_source_display(),
        'query_note': e.query_note, 'answer_note': e.answer_note,
        'queried_by': full_name_for(e.queried_by) if e.queried_by_id else '',
        'queried_at': e.queried_at,
        'over_budget_by': str(finance.money(over)) if over > 0 else None,
        'can_decide': (e.status == Expense.PENDING and can_approve_expenses(user)
                       and e.recorded_by_id != user.id),
        'can_query': e.status == Expense.PENDING and can_approve_expenses(user),
        'can_answer': e.status == Expense.QUERIED and is_accountant(user),
        'can_pay': e.status == Expense.APPROVED and is_accountant(user),
        'can_cancel': e.status in (Expense.PENDING, Expense.QUERIED) and is_accountant(user),
    }


@api_view(['GET', 'POST'])
def expense_categories(request):
    if not can_view_expenses(request.user):
        raise PermissionDenied('For the accountant, the Principal and DPFPA.')
    if request.method == 'POST':
        if not is_accountant(request.user):
            raise PermissionDenied('The accountant keeps the categories.')
        name = str(request.data.get('name') or '').strip()
        if not name:
            return _bad('Give the category a name.')
        fields = {'name': name[:120], 'code': str(request.data.get('code') or '')[:20],
                  'description': str(request.data.get('description') or '')[:300],
                  'is_active': request.data.get('is_active', True) not in (False, 'false', '0')}
        category = ExpenseCategory.objects.filter(id=request.data.get('id')).first() \
            if request.data.get('id') else None
        if category is None:
            if ExpenseCategory.objects.filter(name__iexact=fields['name']).exists():
                return _bad(f'There is already a category called {fields["name"]}.')
            category = ExpenseCategory.objects.create(**fields)
        else:
            for key, value in fields.items():
                setattr(category, key, value)
            category.save()
        return Response({'id': category.id, 'name': category.name, 'is_active': category.is_active},
                        status=status.HTTP_201_CREATED)
    return Response([{'id': c.id, 'name': c.name, 'code': c.code, 'description': c.description,
                      'is_active': c.is_active} for c in ExpenseCategory.objects.all()])


@api_view(['GET', 'POST'])
def expense_budgets(request):
    """GET: where every category stands for the year. POST: set one budget."""
    if not can_view_expenses(request.user):
        raise PermissionDenied('For the accountant, the Principal and DPFPA.')
    year = _year(request)
    if year is None:
        return _bad('No academic year is active.')
    if request.method == 'POST':
        category = ExpenseCategory.objects.filter(id=request.data.get('category')).first()
        if category is None:
            return _bad('Pick a category.')
        try:
            expenses.set_budget(category, year, _money(request.data.get('amount')),
                                note=str(request.data.get('note') or ''), actor=request.user,
                                ip=finance.client_ip(request))
        except (expenses.ExpenseError, ValueError) as exc:
            return _bad(exc, status.HTTP_403_FORBIDDEN if 'accountant' in str(exc) else status.HTTP_400_BAD_REQUEST)
    return Response({'academic_year': {'id': year.id, 'name': year.name},
                     **expenses.budget_position(year)})


@api_view(['GET', 'POST'])
def expense_list(request):
    user = request.user
    if not can_view_expenses(user):
        raise PermissionDenied('For the accountant, the Principal and DPFPA.')
    if request.method == 'GET':
        qs = Expense.objects.select_related('category', 'academic_year', 'recorded_by',
                                            'decided_by', 'bank_account')
        year = _year(request)
        if year is not None:
            qs = qs.filter(academic_year=year)
        for param in ('status', 'category'):
            if request.query_params.get(param):
                qs = qs.filter(**{param: request.query_params[param]})
        return Response([_expense_row(e, user) for e in qs[:1000]])
    category = ExpenseCategory.objects.filter(id=request.data.get('category')).first()
    incurred_on = parse_date(str(request.data.get('incurred_on') or '')) or date.today()
    if category is None:
        return _bad('Pick a category.')
    try:
        expense = expenses.record(
            academic_year=_year(request), category=category,
            description=str(request.data.get('description') or ''),
            payee=str(request.data.get('payee') or ''), amount=_money(request.data.get('amount')),
            incurred_on=incurred_on, supplier_reference=str(request.data.get('supplier_reference') or ''),
            attachment=request.FILES.get('attachment'),
            source=str(request.data.get('source') or Expense.OFFICE), actor=user,
            ip=finance.client_ip(request))
    except expenses.ExpenseError as exc:
        return _bad(exc, status.HTTP_403_FORBIDDEN if 'recorded by the accountant' in str(exc)
                    else status.HTTP_400_BAD_REQUEST)
    except ValueError as exc:
        return _bad(exc)
    return Response(_expense_row(expense, user), status=status.HTTP_201_CREATED)


@api_view(['POST'])
def expense_action(request, pk, action):
    user = request.user
    if not can_view_expenses(user):
        raise PermissionDenied('For the accountant, the Principal and DPFPA.')
    expense = get_object_or_404(Expense, pk=pk)
    ip = finance.client_ip(request)
    try:
        if action == 'decide':
            decision = request.data.get('decision')
            if decision not in ('approve', 'reject'):
                return _bad('Approve or reject?')
            expense = expenses.decide(expense, approve=decision == 'approve', actor=user,
                                      note=str(request.data.get('note') or ''), ip=ip)
        elif action == 'pay':
            account = BankAccount.objects.filter(id=request.data.get('bank_account')).first()
            expense = expenses.pay(
                expense, paid_on=parse_date(str(request.data.get('paid_on') or '')),
                method=request.data.get('method'), bank_account=account,
                reference=str(request.data.get('reference') or ''), actor=user, ip=ip)
        elif action == 'query':
            expense = expenses.query(expense, note=str(request.data.get('note') or ''), actor=user, ip=ip)
        elif action == 'answer':
            changes = {field: request.data.get(field) for field in expenses.CORRECTABLE
                       if field not in ('category', 'attachment', 'amount', 'incurred_on')
                       and request.data.get(field) not in (None, '')}
            if request.data.get('amount') not in (None, ''):
                changes['amount'] = _money(request.data.get('amount'))
            if request.data.get('incurred_on'):
                changes['incurred_on'] = parse_date(str(request.data['incurred_on']))
            if request.data.get('category'):
                category = ExpenseCategory.objects.filter(id=request.data.get('category')).first()
                if category is None:
                    return _bad('Pick a category.')
                changes['category'] = category
            if request.FILES.get('attachment'):
                changes['attachment'] = request.FILES['attachment']
            expense = expenses.answer(expense, note=str(request.data.get('note') or ''),
                                      changes=changes, actor=user, ip=ip)
        elif action == 'cancel':
            expense = expenses.cancel(expense, reason=str(request.data.get('reason') or ''),
                                      actor=user, ip=ip)
        else:
            raise Http404
    except expenses.ExpenseError as exc:
        message = str(exc)
        forbidden = any(word in message for word in ('approves expenses', 'records the payment',
                                                      'cancels a wrong entry', 'recorded themselves',
                                                      'answers a query'))
        return _bad(message, status.HTTP_403_FORBIDDEN if forbidden else status.HTTP_400_BAD_REQUEST)
    expense.refresh_from_db()
    return Response(_expense_row(expense, user))


@api_view(['GET', 'POST'])
def petty_cash(request):
    """GET: the float and its top-ups. POST: the accountant tops it up."""
    if not can_view_expenses(request.user):
        raise PermissionDenied('For the accountant, the Principal and DPFPA.')
    year = _year(request)
    if year is None:
        return _bad('No academic year is active.')
    if request.method == 'POST':
        account = BankAccount.objects.filter(id=request.data.get('bank_account')).first()
        try:
            expenses.top_up_petty_cash(
                academic_year=year, amount=_money(request.data.get('amount')),
                received_on=parse_date(str(request.data.get('received_on') or '')),
                bank_account=account, reference=str(request.data.get('reference') or ''),
                note=str(request.data.get('note') or ''), actor=request.user,
                ip=finance.client_ip(request))
        except expenses.ExpenseError as exc:
            return _bad(exc, status.HTTP_403_FORBIDDEN if 'keeps the petty cash' in str(exc)
                        else status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            return _bad(exc)
    rows = PettyCashTopUp.objects.filter(academic_year=year).select_related('bank_account', 'recorded_by')
    return Response({
        **expenses.petty_cash_position(year),
        'topups': [{'id': t.id, 'amount': str(t.amount), 'received_on': t.received_on,
                    'bank_account': (f'{t.bank_account.bank_name} · {t.bank_account.account_number}'
                                     if t.bank_account_id else ''),
                    'reference': t.reference, 'note': t.note,
                    'recorded_by': full_name_for(t.recorded_by) if t.recorded_by_id else ''}
                   for t in rows[:500]],
    })


@api_view(['GET'])
def expense_attachment(request, pk):
    if not can_view_expenses(request.user):
        raise PermissionDenied('For the accountant, the Principal and DPFPA.')
    expense = get_object_or_404(Expense, pk=pk)
    if not expense.attachment:
        raise Http404
    return FileResponse(expense.attachment.open('rb'), as_attachment=False,
                        filename=expense.attachment.name.rsplit('/', 1)[-1])
