"""The supplementary students' consent form (said 28 October 2026): the
student fills it in and prints it from the portal; the admission office keeps
the wording and records the signed hard copy. See consent.py."""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from . import consent
from .models import CollegeProfile, SuppConsent
from .roles import is_admission_officer, is_break_glass, is_dparc, is_principal, is_records_officer
from .views import _student_profile, _pass_logo, full_name_for, portal_signed_in


def _keeps_the_form(user):
    """The wording, and the signed copies coming back."""
    return is_admission_officer(user) or is_principal(user) or is_break_glass(user)


def _reads_the_list(user):
    return _keeps_the_form(user) or is_dparc(user) or is_records_officer(user)


def _bad(message):
    return Response({'detail': str(message)}, status=status.HTTP_400_BAD_REQUEST)


# ── the student's portal ──────────────────────────────────────────────────────

def _mine(view):
    return {
        'shown': True, 'wording': view['wording'], 'level': view['level'],
        'filled_in': view['consent'] is not None,
        'consent_id': view['consent'].id if view['consent'] else None,
        'agreed_at': view['consent'].agreed_at if view['consent'] else None,
        'paper_received_at': view['consent'].paper_received_at if view['consent'] else None,
        'decision': (view['consent'].get_principal_decision_display()
                     if view['consent'] and view['consent'].principal_decision else ''),
    }


@api_view(['GET', 'POST'])
def my_supp_consent(request):
    """GET: the form, if my results ask for a supplementary. POST: I agree."""
    profile = _student_profile(request)
    if profile is None:
        return Response({'detail': 'Authentication required.'}, status=status.HTTP_403_FORBIDDEN)
    if request.method == 'POST':
        try:
            consent.agree(profile)
        except consent.ConsentError as exc:
            return _bad(exc)
    view = consent.portal_view(profile)
    if view is None:
        return Response({'shown': False})
    return Response(_mine(view), status=status.HTTP_201_CREATED if request.method == 'POST' else status.HTTP_200_OK)


def supp_consent_print(request, pk):
    """The form as filled in, to print, sign and bring back — for the student
    who filled it in, and for the offices that keep it."""
    record = get_object_or_404(SuppConsent.objects.select_related('profile', 'form__academic_year'), pk=pk)
    if portal_signed_in(request):
        mine = _student_profile(request)
        if mine is None or mine.id != record.profile_id:
            raise Http404('No such form.')
    elif not (request.user.is_authenticated and _reads_the_list(request.user)):
        return redirect('login')
    return render(request, 'supp_consent_print.html', {
        'consent': record, 'wording': record.wording, 'college': CollegeProfile.get(), 'logo': _pass_logo(),
        'part_a': [line for line in record.wording.get('part_a', '').splitlines() if line.strip()],
        'part_b': [line for line in record.wording.get('part_b', '').splitlines() if line.strip()],
        'part_d': [line for line in record.wording.get('part_d', '').splitlines() if line.strip()],
        'auto_print': request.GET.get('download') == '1',
    })


# ── the offices ───────────────────────────────────────────────────────────────

def _form_row(form):
    return {'id': form.id, 'academic_year': form.academic_year.name, 'title': form.title,
            'part_a': form.part_a, 'part_b': form.part_b, 'part_c': form.part_c, 'part_d': form.part_d,
            'is_open': form.is_open, 'placeholders': list(consent.PLACEHOLDERS),
            'updated_by': full_name_for(form.updated_by) if form.updated_by_id else '',
            'updated_at': form.updated_at}


@api_view(['GET', 'PUT'])
@login_required
def supp_consent_form(request):
    """The year's wording. The admission office and the Principal change it."""
    user = request.user
    if not _reads_the_list(user):
        raise PermissionDenied('For the admission office, the Principal, DPARC and records.')
    form = consent.form_for(actor=user)
    if form is None:
        return _bad('No semester is active.')
    if request.method == 'PUT':
        if not _keeps_the_form(user):
            raise PermissionDenied('The admission office or the Principal keeps the wording.')
        for field in ('title', 'part_a', 'part_b', 'part_c', 'part_d'):
            if field in request.data:
                setattr(form, field, str(request.data[field] or '').strip())
        if not form.title:
            return _bad('Give the form its title.')
        if 'is_open' in request.data:
            form.is_open = bool(request.data['is_open'])
        form.updated_by = user
        form.save()
    return Response(_form_row(form))


@api_view(['GET'])
@login_required
def supp_consents(request):
    """Every supplementary student: not filled in, filled in online, signed copy back."""
    if not _reads_the_list(request.user):
        raise PermissionDenied('For the admission office, the Principal, DPARC and records.')
    return Response(consent.office_list(request.query_params.get('q', ''), request.query_params.get('state', '')))


@api_view(['POST'])
@login_required
def supp_consent_paper(request, pk):
    """The signed hard copy is back: the Principal's answer on it."""
    if not _keeps_the_form(request.user):
        raise PermissionDenied('The admission office records the signed copy.')
    record = get_object_or_404(SuppConsent, pk=pk)
    try:
        consent.record_paper(record, decision=str(request.data.get('decision') or ''),
                             note=str(request.data.get('note') or ''), actor=request.user)
    except consent.ConsentError as exc:
        return _bad(exc)
    return Response({'ok': True})
