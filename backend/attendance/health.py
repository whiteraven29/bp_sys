"""A student's health problems, each backed by a verified medical certificate.

The way it runs:

    the student uploads the hospital's document from the portal (condition,
    hospital, the date on it) — or hands the paper in to records
        │
        ▼
    records checks it against the original: not accepted, the student is told
    why; accepted, it is filed as a verified medical form
        │
        ▼
    student hands in a medical certificate
        │
        ▼
    records scans it under the student's documents (kind: medical form)
    and marks it seen  ──►  records writes the condition down against it
        │
        ▼
    the Dean of Students is told, and reads it for reference from then on
    (welfare, allowances, a student taken ill); the Principal reads it too
        │
        ▼
    a condition that no longer applies is closed with a note — never deleted

Nobody else sees these: not finance, not tutors, not the exam office.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import notifications
from .models import (
    DeanOfStudentsProfile, HealthRecord, HealthReport, Notification, PrincipalProfile,
    RecordsOfficerProfile, StudentDocument,
)
from .roles import can_keep_health_records


class HealthRecordError(ValueError):
    """Something about this record the rules do not allow; the message says what."""


def deans():
    """Every account holding the Dean of Students' office."""
    return [profile.user for profile in
            DeanOfStudentsProfile.objects.filter(is_active=True).select_related('user')]


def keepers():
    """Who checks a student's health report: the records office (the Principal
    may, and is told too)."""
    return [profile.user for model in (RecordsOfficerProfile, PrincipalProfile)
            for profile in model.objects.filter(is_active=True).select_related('user')]


def _valid_document_details(facility, issued_on):
    """A valid document names the hospital that issued it and bears a date
    that has come (said 16 October 2026)."""
    facility = (facility or '').strip()
    if not facility:
        raise HealthRecordError('Name the hospital or health facility that issued the document.')
    if isinstance(issued_on, str):
        try:
            issued_on = date.fromisoformat(issued_on.strip())
        except ValueError:
            issued_on = None
    if issued_on is None:
        raise HealthRecordError('Give the date on the document.')
    if issued_on > timezone.localdate():
        raise HealthRecordError('The date on the document cannot be in the future.')
    return facility[:160], issued_on


@transaction.atomic
def add_record(profile, *, condition, certificate, facility='', issued_on=None, notes='', actor=None):
    """Records writes a health problem down against a verified certificate
    from a hospital: who issued it, and when."""
    if not can_keep_health_records(actor):
        raise HealthRecordError('Health records are kept by the records office.')
    condition = (condition or '').strip()
    if not condition:
        raise HealthRecordError('Name the health problem, as the certificate does.')
    if certificate is None or certificate.profile_id != profile.id:
        raise HealthRecordError("Choose the medical certificate from this student's documents.")
    if certificate.kind != StudentDocument.MEDICAL:
        raise HealthRecordError('The certificate must be filed as a medical form.')
    if not certificate.is_verified:
        raise HealthRecordError('Mark the certificate seen (verified) first: records checks the '
                                'original before the condition is written down.')
    facility, issued_on = _valid_document_details(facility, issued_on)
    record = HealthRecord.objects.create(
        profile=profile, condition=condition[:200], notes=(notes or '').strip()[:500],
        certificate=certificate, facility=facility, issued_on=issued_on, recorded_by=actor)
    notifications.notify(
        deans(), kind=Notification.HEALTH_RECORDED,
        title=f'Health record: {profile.name}',
        body=f'{profile.nactvet_reg_no} · {record.condition}. For reference, on a verified certificate.',
        link='health')
    return record


# ── the student's own report, from the portal ─────────────────────────────────

@transaction.atomic
def report_condition(profile, *, condition, facility, issued_on, upload):
    """A student reports a health problem with the hospital's document. It
    waits for the records office to check it."""
    condition = (condition or '').strip()
    if not condition:
        raise HealthRecordError('Say what the health problem is, as the document names it.')
    facility, issued_on = _valid_document_details(facility, issued_on)
    if not upload:
        raise HealthRecordError("Upload the hospital's document — a scan or a clear photo (PDF, JPG or PNG).")
    document = StudentDocument(profile=profile, kind=StudentDocument.MEDICAL, file=upload,
                               original_name=getattr(upload, 'name', '')[:255], uploaded_by_student=True,
                               note=f'Health report: {condition}'[:200])
    try:
        document.full_clean(exclude=['uploaded_by', 'verified_by'])
    except ValidationError as exc:
        raise HealthRecordError(' '.join(exc.messages))
    document.save()
    report = HealthReport.objects.create(profile=profile, condition=condition[:200], facility=facility,
                                         issued_on=issued_on, document=document)
    notifications.notify(
        keepers(), kind=Notification.HEALTH_REPORTED,
        title=f'Health report to check: {profile.name}',
        body=f'{profile.nactvet_reg_no} · {report.condition} ({facility}, {issued_on:%d %b %Y}). '
             f'Check the document against the original.',
        link='health')
    return report


def _waiting(report):
    report = HealthReport.objects.select_for_update().select_related('profile', 'document').get(pk=report.pk)
    if report.status != HealthReport.WAITING:
        raise HealthRecordError(f'This report is already {report.get_status_display().lower()}.')
    return report


@transaction.atomic
def accept_report(report, *, actor, condition=None, notes=''):
    """Records has seen the original: the document is filed as verified and
    the condition recorded (the Dean of Students is told)."""
    if not can_keep_health_records(actor):
        raise HealthRecordError('Health records are kept by the records office.')
    report = _waiting(report)
    document = report.document
    if not document.is_verified:
        document.verified_by = actor
        document.verified_at = timezone.now()
        document.save(update_fields=['verified_by', 'verified_at'])
    record = add_record(report.profile, condition=condition or report.condition, certificate=document,
                        facility=report.facility, issued_on=report.issued_on, notes=notes, actor=actor)
    report.status = HealthReport.RECORDED
    report.record = record
    report.decided_by = actor
    report.decided_at = timezone.now()
    report.save(update_fields=['status', 'record', 'decided_by', 'decided_at'])
    notifications.notify(profile=report.profile, kind=Notification.HEALTH_ANSWERED,
                         title='Your health report was recorded',
                         body=f'{record.condition}. The college has it on file.', link='profile')
    return report


@transaction.atomic
def reject_report(report, *, actor, reason):
    """Not a valid hospital document, or not readable: the student is told why
    and may report it again with the right one."""
    if not can_keep_health_records(actor):
        raise HealthRecordError('Health records are kept by the records office.')
    reason = (reason or '').strip()
    if not reason:
        raise HealthRecordError('Say why it is not accepted, so the student can put it right.')
    report = _waiting(report)
    report.status = HealthReport.REJECTED
    report.decision_note = reason[:300]
    report.decided_by = actor
    report.decided_at = timezone.now()
    report.save(update_fields=['status', 'decision_note', 'decided_by', 'decided_at'])
    notifications.notify(profile=report.profile, kind=Notification.HEALTH_ANSWERED,
                         title='Your health report was not accepted', body=report.decision_note, link='profile')
    return report


def report_row(report):
    profile = report.profile
    return {
        'id': report.id, 'profile_id': profile.id, 'reg_no': profile.nactvet_reg_no,
        'name': profile.name, 'condition': report.condition, 'facility': report.facility,
        'issued_on': report.issued_on, 'status': report.status, 'status_display': report.get_status_display(),
        'decision_note': report.decision_note, 'created_at': report.created_at,
        'document_name': report.document.display_name,
        'document_url': f'/api/student-documents/{report.document_id}/file/',
    }


@transaction.atomic
def close_record(record, *, note, actor=None):
    """The condition no longer applies. Kept, with the reason."""
    record = HealthRecord.objects.select_for_update().get(pk=record.pk)
    if not can_keep_health_records(actor):
        raise HealthRecordError('Health records are kept by the records office.')
    if not record.is_current:
        raise HealthRecordError('This record is already closed.')
    note = (note or '').strip()
    if not note:
        raise HealthRecordError('Say why it no longer applies.')
    record.is_current = False
    record.closed_by = actor
    record.closed_at = timezone.now()
    record.closed_note = note[:300]
    record.save(update_fields=['is_current', 'closed_by', 'closed_at', 'closed_note'])
    return record


def record_row(record):
    profile = record.profile
    certificate = record.certificate
    return {
        'id': record.id,
        'profile_id': profile.id,
        'reg_no': profile.nactvet_reg_no,
        'college_id': profile.college_id or '',
        'name': profile.name,
        'gender': profile.get_gender_display() if profile.gender else '',
        'condition': record.condition,
        'notes': record.notes,
        'facility': record.facility,
        'issued_on': record.issued_on,
        'certificate_id': certificate.id,
        'certificate_name': certificate.display_name,
        'certificate_url': f'/api/student-documents/{certificate.id}/file/',
        'verified_by': (certificate.verified_by.get_full_name() or certificate.verified_by.username)
                       if certificate.verified_by_id else '',
        'verified_at': certificate.verified_at,
        'recorded_by': (record.recorded_by.get_full_name() or record.recorded_by.username)
                       if record.recorded_by_id else '',
        'recorded_at': record.recorded_at,
        'is_current': record.is_current,
        'closed_at': record.closed_at,
        'closed_note': record.closed_note,
    }
