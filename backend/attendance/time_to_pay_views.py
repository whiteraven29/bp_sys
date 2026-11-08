"""Waivers' dates and monthly tuition plans: finance asks, DPFPA decides, both
follow the money until it is paid (said 6 November 2026). See time_to_pay.py."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from . import finance, time_to_pay
from .models import AcademicYear, MonthlyPlan, StudentProfile, WaiverRequest
from .roles import is_dpfpa, is_principal
from .views import _a_date, _waiver_row, active_academic_year, can_manage_finance, pk_or_none


def _reads(user):
    return can_manage_finance(user) or is_dpfpa(user) or is_principal(user)


def _bad(message):
    return Response({'detail': str(message)}, status=status.HTTP_400_BAD_REQUEST)


def _year(source):
    return AcademicYear.objects.filter(id=pk_or_none(source.get('academic_year_id'))).first() or active_academic_year()


@api_view(['GET'])
def summary(request):
    """Every agreement of the year, and the money under it."""
    if not _reads(request.user):
        raise PermissionDenied('For the accountant, DPFPA and the Principal.')
    year = _year(request.query_params)
    if year is None:
        return _bad('No academic year is active.')
    return Response({**time_to_pay.summary(year), 'can_extend': is_dpfpa(request.user)})


@api_view(['POST'])
def extend_waiver(request, pk):
    """More time, when the student did not pay by the date: DPFPA alone."""
    if not is_dpfpa(request.user):
        raise PermissionDenied('DPFPA gives more time.')
    waiver = get_object_or_404(WaiverRequest, pk=pk)
    try:
        done = time_to_pay.extend_waiver(waiver, _a_date(request.data.get('pay_by')),
                                         str(request.data.get('reason') or ''), actor=request.user)
    except finance.WaiverError as exc:
        return _bad(exc)
    return Response(_waiver_row(done))


@api_view(['GET', 'POST'])
def monthly_plans(request):
    """GET: plans, by status or student. POST: the accountant asks for one."""
    user = request.user
    if request.method == 'POST':
        if not can_manage_finance(user):
            raise PermissionDenied('The accountant asks for a monthly plan.')
        profile = get_object_or_404(StudentProfile, pk=pk_or_none(request.data.get('profile_id')))
        year = _year(request.data)
        if year is None:
            return _bad('No academic year is active.')
        month = str(request.data.get('first_month') or '')
        restore = request.data.get('restore') or []
        try:
            first = _a_date(f'{month}-01' if len(month) == 7 else month)
            amount = Decimal(str(request.data.get('monthly_amount') or '0'))
            months = int(request.data.get('months') or 0) or None
            plan = time_to_pay.propose_plan(profile, year, amount, first, str(request.data.get('reason') or ''),
                                            actor=user, months=months,
                                            restore=restore if isinstance(restore, list) else [restore])
        except (InvalidOperation, TypeError, ValueError):
            return _bad('Write how much they pay each month, or over how many months.')
        except time_to_pay.PlanError as exc:
            return _bad(exc)
        detail = ('Sent to DPFPA for approval. Agreed, the old waiver goes back on the bill, into the plan.'
                  if plan.restores.exists() else 'Sent to DPFPA for approval. The bill does not change.')
        return Response({'detail': detail,
                         'plan': time_to_pay.plan_row(plan)}, status=status.HTTP_201_CREATED)
    if not _reads(user):
        raise PermissionDenied('For the accountant, DPFPA and the Principal.')
    plans = MonthlyPlan.objects.select_related('profile', 'academic_year', 'requested_by', 'decided_by')
    if request.query_params.get('status'):
        plans = plans.filter(status=request.query_params['status'])
    profile_id = pk_or_none(request.query_params.get('profile_id'))
    if profile_id:
        time_to_pay.sync_plans(profile=StudentProfile.objects.filter(id=profile_id).first())
        plans = plans.filter(profile_id=profile_id)
    today = date.today()
    return Response([time_to_pay.plan_row(plan, today) for plan in plans[:300]])


@api_view(['GET'])
def plan_options(request):
    """For the plan form: what is owed, and the waivers it would touch."""
    if not can_manage_finance(request.user):
        raise PermissionDenied('Finance access required.')
    profile = get_object_or_404(StudentProfile, pk=pk_or_none(request.query_params.get('profile_id')))
    year = _year(request.query_params)
    if year is None:
        return _bad('No academic year is active.')
    return Response(time_to_pay.plan_options(profile, year))


@api_view(['POST'])
def decide_plan(request, pk):
    """DPFPA agrees or refuses a monthly plan — nobody else."""
    if not is_dpfpa(request.user):
        raise PermissionDenied('DPFPA agrees monthly plans.')
    plan = get_object_or_404(MonthlyPlan, pk=pk)
    try:
        done = time_to_pay.decide_plan(plan, approve=bool(request.data.get('approve')),
                                       note=str(request.data.get('note') or ''), actor=request.user)
    except time_to_pay.PlanError as exc:
        return _bad(exc)
    return Response(time_to_pay.plan_row(done))
