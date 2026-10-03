"""A student's health problems, each backed by a verified medical certificate.

The way it runs:

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

from django.db import transaction
from django.utils import timezone

from . import notifications
from .models import DeanOfStudentsProfile, HealthRecord, Notification, StudentDocument
from .roles import can_keep_health_records


class HealthRecordError(ValueError):
    """Something about this record the rules do not allow; the message says what."""


def deans():
    """Every account holding the Dean of Students' office."""
    return [profile.user for profile in
            DeanOfStudentsProfile.objects.filter(is_active=True).select_related('user')]


@transaction.atomic
def add_record(profile, *, condition, certificate, notes='', actor=None):
    """Records writes a health problem down against a verified certificate."""
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
    record = HealthRecord.objects.create(
        profile=profile, condition=condition[:200], notes=(notes or '').strip()[:500],
        certificate=certificate, recorded_by=actor)
    notifications.notify(
        deans(), kind=Notification.HEALTH_RECORDED,
        title=f'Health record: {profile.name}',
        body=f'{profile.nactvet_reg_no} · {record.condition}. For reference, on a verified certificate.',
        link='health')
    return record


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
