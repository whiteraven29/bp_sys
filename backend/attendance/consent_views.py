"""The supplementary students' consent form (said 28 October 2026): the
student fills it in and prints it from the portal; the admission office keeps
the wording and records the signed hard copy. See consent.py."""

import re

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from . import consent, pdf
from .models import CollegeProfile, SuppConsent
from .registers import LOGO
from .roles import is_admission_officer, is_break_glass, is_dparc, is_principal, is_records_officer
from .views import _student_profile, full_name_for, portal_signed_in


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


NAVY, INK, MUTED, LINE = '#123F80', '#1E293B', '#6B7785', '#DCE2EA'


def _dashed_box(doc, x, y, w, h, *, dash=3):
    """The stamp's box: dashes, so it reads as a place to stamp."""
    for start in range(0, int(w), 2 * dash):
        end = min(start + dash, w)
        doc.line(x + start, y, x + end, y, colour=MUTED, thickness=.7)
        doc.line(x + start, y + h, x + end, y + h, colour=MUTED, thickness=.7)
    for start in range(0, int(h), 2 * dash):
        end = min(start + dash, h)
        doc.line(x, y + start, x, y + end, colour=MUTED, thickness=.7)
        doc.line(x + w, y + start, x + w, y + end, colour=MUTED, thickness=.7)


def consent_pdf(record):
    """The form as the student agreed to it, as a one-page PDF (the same writer
    as the invoice download, pdf.py): the college, the four parts, two places
    to sign, the stamp."""
    college = CollegeProfile.get()
    wording = record.wording or {}
    doc = pdf.Document()
    margin, top, bottom = 48, 30, 30
    width = doc.page_w - 2 * margin
    y = top

    def room(height):
        nonlocal y
        if y + height > doc.page_h - bottom:
            doc.new_page()
            y = top

    def lines(text, *, font='regular', size=9.2, lead=11.8, align='left', colour=INK):
        nonlocal y
        for line in pdf.wrap(text, size, width, font):
            room(lead)
            x = doc.page_w / 2 if align == 'center' else margin
            doc.text(x, y + size, line, size=size, font=font, align=align, colour=colour)
            y += lead

    def paragraphs(block):
        nonlocal y
        for line in [part for part in (block or '').splitlines() if part.strip()]:
            lines(line)
            y += 2

    def heading(text):
        nonlocal y
        y += 5
        room(40)
        lines(text, font='bold', size=10, lead=14)

    def sign_here(stamp=False):
        nonlocal y
        room(58 if stamp else 28)
        y += 18
        doc.text(margin, y, 'Saini:', size=9.5, colour=INK)
        doc.line(margin + 34, y + 2, margin + 200, y + 2, colour=MUTED)
        doc.text(margin + 214, y, 'Tarehe:', size=9.5, colour=INK)
        doc.line(margin + 254, y + 2, margin + 330, y + 2, colour=MUTED)
        if stamp:      # the office's stamp beside the Principal's signature
            _dashed_box(doc, margin + width - 140, y - 26, 140, 56)
            doc.text(margin + width - 70, y + 6, 'Muhuri wa ofisi', size=9, colour=MUTED, align='center')
            y += 30
        y += 8

    lines((college.name or '').upper(), font='bold', size=13, lead=18, align='center', colour=NAVY)
    try:
        doc.image(pdf.png_image(LOGO), doc.page_w / 2 - 25, y + 2, 50, 50)
        y += 56
    except (OSError, ValueError):
        pass
    doc.line(margin, y + 4, margin + width, y + 4, colour=NAVY, thickness=1.6)
    y += 16
    lines(wording.get('title', ''), font='bold', size=10.5, lead=14, align='center')
    y += 2
    heading('Sehemu A: Uelewa kuhusiana na Miongozo')
    paragraphs(wording.get('part_a'))
    heading('Sehemu B: Masharti ya kuendelea na ngazi inayofuata')
    paragraphs(wording.get('part_b'))
    heading('Sehemu C: Ridhaa ya Mwanafunzi')
    paragraphs(wording.get('part_c'))
    sign_here()
    heading('Sehemu D: Ruhusa ya Mkuu wa Chuo')
    paragraphs(wording.get('part_d'))
    sign_here(stamp=True)
    # The footer sits at the foot of the last page, below the margin the text keeps.
    profile = record.profile
    agreed = timezone.localtime(record.agreed_at).strftime('%d %b %Y %H:%M') if record.agreed_at else ''
    foot = doc.page_h - 24
    doc.line(margin, foot - 10, margin + width, foot - 10, colour=LINE, thickness=.5)
    doc.text(doc.page_w / 2, foot, f'{profile.nactvet_reg_no} · {profile.name} · {record.next_level} · Imejazwa '
             f'mtandaoni (filled in online) {agreed} · {record.form.academic_year.name}',
             size=7.5, colour=MUTED, align='center')
    return doc.output(title=wording.get('title', ''))


def supp_consent_print(request, pk):
    """The form as filled in, downloaded as a PDF to print, sign and bring
    back — for the student who filled it in, and for the offices that keep it.
    One download, no print dialog (said 17 November 2026)."""
    record = get_object_or_404(SuppConsent.objects.select_related('profile', 'form__academic_year'), pk=pk)
    if portal_signed_in(request):
        mine = _student_profile(request)
        if mine is None or mine.id != record.profile_id:
            raise Http404('No such form.')
    elif not (request.user.is_authenticated and _reads_the_list(request.user)):
        return redirect('login')
    response = HttpResponse(consent_pdf(record), content_type='application/pdf')
    name = re.sub(r'[^A-Za-z0-9]+', '-', record.profile.nactvet_reg_no).strip('-') or 'student'
    response['Content-Disposition'] = f'attachment; filename="Supplementary-consent-{name}.pdf"'
    return response


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
