"""How long a password lasts, and what happens when it does not.

The college's rule: a password is good for six months. In the fortnight before
that, every dashboard says so. Once it expires the holder is made to change it
at the next sign-in, and they have a further fortnight of grace to do it in.
Miss that and the account stops opening — the office resets it, which is the
point: an account nobody has signed into in half a year is one worth checking
on before it is handed back.

The same clock runs for staff accounts and for students' portal passwords.
Staff carry a `PasswordStatus` row; a student's password lives on their
enrollments, which stamp the day it was set.

Nothing here reads or stores a password. It reads dates.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import PasswordStatus, Student, StudentProfile

#: What the college is shown, in the order things get worse.
OK = 'ok'
EXPIRING = 'expiring'
EXPIRED = 'expired'
LOCKED = 'locked'


def max_age():
    return timedelta(days=getattr(settings, 'PASSWORD_MAX_AGE_DAYS', 182))


def grace():
    return timedelta(days=getattr(settings, 'PASSWORD_GRACE_DAYS', 14))


def warn_days():
    return getattr(settings, 'PASSWORD_WARN_DAYS', 14)


def _reading(changed_at, *, must_change=False, now=None):
    """One password's age, told as the dashboard tells it."""
    now = now or timezone.now()
    if changed_at is None:
        # A password the college cannot date is treated as set today rather
        # than as ancient: locking everybody out on the day this ships would
        # be a policy nobody agreed to.
        changed_at = now
    expires_at = changed_at + max_age()
    locks_at = expires_at + grace()
    days_left = (expires_at - now).days

    if now >= locks_at:
        state = LOCKED
    elif now >= expires_at or must_change:
        state = EXPIRED
    elif days_left <= warn_days():
        state = EXPIRING
    else:
        state = OK

    return {
        'state': state,
        'changed_on': changed_at.date(),
        'expires_on': expires_at.date(),
        'locks_on': locks_at.date(),
        'days_left': days_left,
        'days_to_reset': (locks_at - now).days,
        'must_change': bool(must_change) or state in (EXPIRED, LOCKED),
        'message': _message(state, days_left, (locks_at - now).days),
    }


def _message(state, days_left, days_to_reset):
    if state == LOCKED:
        return ('Your password has expired and the time to change it has passed. '
                'See the administrator for a reset.')
    if state == EXPIRED:
        return (f'Your password has expired. Change it now — after {max(days_to_reset, 0)} '
                f'more day(s) only the administrator can reset it.')
    if state == EXPIRING:
        return f'Your password expires in {max(days_left, 0)} day(s). Change it before then.'
    return f'Your password is good for another {max(days_left, 0)} day(s).'


# ── staff ─────────────────────────────────────────────────────────────────────

def status_for(user):
    """This account's password row, started today if it has none.

    Accounts that existed before the policy did have no date on their
    password. Their clock starts when the college first asks, which gives
    everybody six months from the day this arrives rather than locking out the
    whole staff room at once.
    """
    status, _ = PasswordStatus.objects.get_or_create(user=user)
    return status


def reading_for(user, *, now=None):
    if not (user and user.is_authenticated):
        return None
    status = status_for(user)
    return _reading(status.changed_at, must_change=status.must_change, now=now)


def mark_changed(user, *, must_change=False):
    """The password has just been set. The clock starts again."""
    status = status_for(user)
    status.changed_at = timezone.now()
    status.must_change = must_change
    status.save(update_fields=['changed_at', 'must_change'])
    return status


def mark_reset(user, *, by=None):
    """The office has given this account a temporary password: the holder
    picks their own at the next sign-in."""
    status = mark_changed(user, must_change=True)
    status.reset_by = by
    status.reset_at = timezone.now()
    status.save(update_fields=['reset_by', 'reset_at'])
    return status


def is_locked(user, *, now=None):
    reading = reading_for(user, now=now)
    return bool(reading and reading['state'] == LOCKED)


# ── students ──────────────────────────────────────────────────────────────────

def student_reading(student, *, now=None):
    """The age of this student's portal password.

    Their enrollments all carry the same password, set together, so the latest
    stamp on any of them is the day it was set.
    """
    if student is None:
        return None
    rows = Student.objects.filter(nactvet_reg_no__iexact=student.nactvet_reg_no)
    stamps = [row.portal_pin_set_at for row in rows if row.portal_pin_set_at]
    return _reading(max(stamps) if stamps else None,
                    must_change=student.must_change_portal_password, now=now)


def student_is_locked(student, *, now=None):
    reading = student_reading(student, now=now)
    return bool(reading and reading['state'] == LOCKED)


def require_student_change(student):
    """Mark every one of this student's enrollments as needing a new password,
    which is what the portal checks at the door."""
    return Student.objects.filter(
        nactvet_reg_no__iexact=student.nactvet_reg_no).update(must_change_portal_password=True)


def profile_of(student):
    if student.profile_id:
        return student.profile
    return StudentProfile.objects.filter(
        nactvet_reg_no__iexact=student.nactvet_reg_no).first()
