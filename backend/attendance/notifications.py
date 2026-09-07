"""Telling people that something needs them.

A service request used to sit in a queue nobody had a reason to open. The
secretary found out a student had asked for a letter by going and looking; the
Principal found out one was waiting for a decision the same way; the student
found out it had been answered by checking. Work that depends on somebody
noticing it is work that waits, and the college's own answer to that was a
person walking down a corridor.

Everything here is best-effort and never blocks the thing it is reporting on: a
request must not fail to be submitted because nobody could be told about it.
"""

import logging

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import (
    HeadOfDepartmentProfile, Notification, PrincipalProfile, SecretaryProfile,
)

logger = logging.getLogger(__name__)
User = get_user_model()


# ── who hears about what ──────────────────────────────────────────────────────

def _users_holding(*profile_models):
    ids = set()
    for model in profile_models:
        ids.update(model.objects.filter(is_active=True).values_list('user_id', flat=True))
    return list(User.objects.filter(id__in=ids, is_active=True))


def secretaries():
    """Who receives what students ask for, and who acts on the answer."""
    return _users_holding(SecretaryProfile)


def deciders():
    """Who says yes or no to a request — the Principal and the Head of
    Department. Not the secretary: they pass it on."""
    return _users_holding(PrincipalProfile, HeadOfDepartmentProfile)


# ── raising one ───────────────────────────────────────────────────────────────

def notify(users=None, *, profile=None, kind, title, body='', link=''):
    """Tell people. One row each, because a notification is read by a person.

    Never raises: nothing here is important enough to fail the action it is
    reporting on.
    """
    rows = []
    try:
        for user in users or []:
            rows.append(Notification(user=user, kind=kind, title=title, body=body, link=link))
        if profile is not None:
            rows.append(Notification(profile=profile, kind=kind, title=title,
                                     body=body, link=link))
        if rows:
            Notification.objects.bulk_create(rows)
    except Exception:                                    # pragma: no cover - belt and braces
        logger.exception('Could not raise a %s notification', kind)
    return rows


def unread_for(user=None, profile=None):
    return _for(user, profile).filter(read_at__isnull=True)


def _for(user=None, profile=None):
    if user is not None and getattr(user, 'is_authenticated', False):
        return Notification.objects.filter(user=user)
    if profile is not None:
        return Notification.objects.filter(profile=profile)
    return Notification.objects.none()


def recent_for(user=None, profile=None, limit=20):
    return list(_for(user, profile)[:limit])


def mark_read(user=None, profile=None, ids=None):
    """Mark some or all of somebody's notifications read."""
    qs = _for(user, profile).filter(read_at__isnull=True)
    if ids:
        qs = qs.filter(id__in=ids)
    return qs.update(read_at=timezone.now())


# ── the events worth telling somebody about ───────────────────────────────────

def request_submitted(service_request):
    """A student has asked for something. It lands on the secretary's desk."""
    student = service_request.profile.name if service_request.profile else 'A student'
    notify(secretaries(), kind=Notification.REQUEST_SUBMITTED,
           title=f'{service_request.form.title}',
           body=f'{student} has asked for this. Check it and pass it on for a decision.',
           link='forms:requests')


def request_forwarded(service_request):
    """The secretary has put it in front of the Principal and the HoD."""
    student = service_request.profile.name if service_request.profile else 'A student'
    body = f'{student} — waiting on your decision.'
    if service_request.forward_note:
        body += f' Secretary: “{service_request.forward_note}”'
    notify(deciders(), kind=Notification.REQUEST_FORWARDED,
           title=f'Decision needed: {service_request.form.title}',
           body=body, link='forms:requests')


def request_decided(service_request):
    """A decision came back. The secretary acts on it; the student is told."""
    verdict = 'approved' if service_request.status == service_request.APPROVED else 'declined'
    student = service_request.profile.name if service_request.profile else 'A student'
    officer = service_request.decided_by
    by = (officer.get_full_name() or officer.username) if officer else 'the College'

    notify(secretaries(), kind=Notification.REQUEST_DECIDED,
           title=f'{service_request.form.title} — {verdict}',
           body=(f'{student}. {by} {verdict} it. '
                 + ('Prepare the document and send it to them.' if verdict == 'approved'
                    else 'Nothing further to prepare.')),
           link='forms:requests')

    notify(profile=service_request.profile, kind=Notification.REQUEST_DECIDED,
           title=f'{service_request.form.title} — {verdict}',
           body=service_request.decision_note or f'Your request has been {verdict}.',
           link='my-requests')


def document_sent(service_request, attachment):
    notify(profile=service_request.profile, kind=Notification.REQUEST_DOCUMENT,
           title=f'{service_request.form.title} — document ready',
           body=f'{attachment.display_name} is ready to download.',
           link='my-requests')
