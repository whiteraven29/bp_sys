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

from django.db import models

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


def require_change(user):
    """Ask for a new password at once, without moving the six-month clock —
    a password that falls short of the rule (said 19 October 2026)."""
    status = status_for(user)
    if not status.must_change:
        status.must_change = True
        status.save(update_fields=['must_change'])
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
    if not stamps and getattr(student, 'portal_pin_set_at', None):
        stamps = [student.portal_pin_set_at]          # no module yet: the record's own
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


# ── the password desk: resets, and who may sign in with what ─────────────────
#
# The ICT officer is where anybody goes when they have forgotten a password —
# staff, the Principal, students. A reset hands over a temporary password that
# must be changed at the next sign-in, is recorded (PasswordReset), and is
# shown to the holder, so a reset they did not ask for does not go unnoticed.

class PasswordResetError(ValueError):
    """A reset the rules do not allow; the message says why."""


def _record_reset(*, kind, actor, user=None, profile=None, ip=None, first=False):
    from . import notifications
    from .models import Notification, PasswordReset
    row = PasswordReset.objects.create(kind=kind, user=user, profile=profile, done_by=actor, ip=ip)
    by = (actor.get_full_name() or actor.username) if actor is not None else 'the office'
    when = f'{timezone.localtime(row.done_at):%d %b %Y at %H:%M}'
    notifications.notify(
        [user] if user is not None else None, profile=profile, kind=Notification.PASSWORD_RESET,
        title='Your portal password was issued' if first else 'Your password was reset',
        body=(f'Issued by {by} on {when}. Choose your own the first time you sign in.' if first else
              f'Reset by {by} on {when}. If you did not ask for this, tell the ICT officer.'))
    return row


def reset_staff_password(user, *, actor, given='', ip=None):
    """A new password for a member of staff — the Principal's too: the ICT
    officer is the desk for everybody. Returns the temporary password, which
    must be changed at the next sign-in."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from .admissions import generate_portal_pin
    from .roles import can_reset_passwords
    if not can_reset_passwords(actor):
        raise PasswordResetError('Passwords are reset by the ICT officer (or the Principal).')
    given = (given or '').strip()
    if given:
        try:
            validate_password(given, user)
        except ValidationError as exc:
            raise PasswordResetError(' '.join(exc.messages))
    password = given or generate_portal_pin()
    user.set_password(password)
    user.save(update_fields=['password'])
    mark_reset(user, by=actor)
    _record_reset(kind='staff', actor=actor, user=user, ip=ip)
    return password


def reset_student_password(profile, *, actor, ip=None, first=False):
    """A new portal password for a student — their first, or one they have
    forgotten: every enrollment (PST) and the profile (VETA) get the same one.
    Returns it, once."""
    from .admissions import generate_portal_pin
    from .roles import can_issue_student_passwords
    if not can_issue_student_passwords(actor):
        raise PasswordResetError('Student passwords are reset by the ICT officer.')
    enrollments = list(Student.objects.filter(nactvet_reg_no__iexact=profile.nactvet_reg_no))
    if not enrollments and not is_admitted(profile):
        raise PasswordResetError(f'{profile.name} has not been admitted.')
    pin = generate_portal_pin()
    for enrollment in enrollments:
        enrollment.set_portal_pin(pin, require_change=True)
        enrollment.save(update_fields=['portal_pin_hash', 'must_change_portal_password', 'portal_pin_set_at'])
    # Kept on their record too (said 20 October 2026): admission and finance
    # make somebody our student, not a module — a PST student admitted before
    # their modules are set up signs in with it, and their modules take it on
    # when they come. A VETA student has only this one.
    from . import veta
    veta.set_portal_pin(profile, pin, require_change=True)
    _record_reset(kind='student', actor=actor, profile=profile, ip=ip, first=first)
    return pin


def first_password(profile):
    """A newly admitted student's first password: their last name in capital
    letters, no spaces (said 21 October 2026). They must choose their own the
    first time they sign in."""
    last = profile.last_name or (profile.name.split()[-1] if (profile.name or '').split() else '')
    return ''.join(last.split()).upper()


def give_first_password(profile, *, actor=None):
    """Set the first password at admission — only for somebody who has none:
    a returning student keeps the one they chose."""
    if profile.portal_pin_hash or Student.objects.filter(
            nactvet_reg_no__iexact=profile.nactvet_reg_no).exclude(portal_pin_hash='').exists():
        return False
    pin = first_password(profile)
    if not pin:
        return False
    from . import veta
    veta.set_portal_pin(profile, pin, require_change=True)
    _record_reset(kind='student', actor=actor, profile=profile, first=True)
    return True


def is_admitted(profile):
    """Our student: admitted this year (PST — a registration the admission
    desk made), or on a VETA course. Modules are not the test."""
    from .models import AcademicYear, SemesterRegistration
    if profile.course_enrollments.exists():
        return True
    year = AcademicYear.objects.filter(is_active=True).first()
    return year is not None and SemesterRegistration.objects.filter(
        profile=profile, semester__academic_year=year).exclude(
        status=SemesterRegistration.CANCELLED).exists()


def students_without_password():
    """Current students who have no portal password yet: PST students admitted
    or enrolled this year, and admitted VETA course students. Newly admitted
    students land here, modules or not — the password desk gives them theirs."""
    from .models import AcademicYear, CourseEnrollment, SemesterRegistration
    year = AcademicYear.objects.filter(is_active=True).first()
    with_password = set(Student.objects.exclude(portal_pin_hash='').filter(profile_id__isnull=False)
                        .values_list('profile_id', flat=True))
    pst = StudentProfile.objects.none()
    if year is not None:
        admitted = SemesterRegistration.objects.filter(semester__academic_year=year).exclude(
            status=SemesterRegistration.CANCELLED).values('profile_id')
        enrolled = Student.objects.filter(module__semester__academic_year=year,
                                          withdrawn_at__isnull=True).values('profile_id')
        pst = (StudentProfile.objects.filter(models.Q(id__in=admitted) | models.Q(id__in=enrolled))
               .filter(portal_pin_hash='').exclude(id__in=with_password))
    veta = StudentProfile.objects.filter(
        id__in=CourseEnrollment.objects.values('profile_id'), portal_pin_hash='')
    return (pst | veta).distinct().order_by('name')


def profile_for_sign_in(identifier, secret):
    """An admitted PST student with no module yet, signing in with the
    password kept on their record — by registration number or college ID.
    Only while no module carries a password: from then on the enrollments are
    what sign them in, as before."""
    from django.contrib.auth.hashers import check_password
    reg = str(identifier or '').strip()
    if not reg:
        return None
    profile = (StudentProfile.objects.filter(models.Q(nactvet_reg_no__iexact=reg) | models.Q(college_id__iexact=reg))
               .exclude(portal_pin_hash='').first())
    if profile is None or profile.course_enrollments.exists():
        return None
    if Student.objects.filter(nactvet_reg_no__iexact=profile.nactvet_reg_no).exclude(portal_pin_hash='').exists():
        return None
    if not is_admitted(profile) or not check_password(str(secret), profile.portal_pin_hash):
        return None
    return profile


def admitted_without_modules():
    """PST students admitted this year who are in no module of the semester
    they were admitted to — so nothing to sign in through, and no password to
    give yet. Admission enrolls a student in the modules of their programme,
    level and semester; none there, none made (said 20 October 2026: three
    admitted under per-level programmes whose modules sat under PST)."""
    from django.db.models import Exists, OuterRef
    from .models import AcademicYear, Programme, SemesterRegistration
    year = AcademicYear.objects.filter(is_active=True).first()
    if year is None:
        return SemesterRegistration.objects.none()
    return (SemesterRegistration.objects
            .filter(semester__academic_year=year, programme__structure=Programme.SEMESTER)
            .exclude(status=SemesterRegistration.CANCELLED)
            .exclude(Exists(Student.objects.filter(profile=OuterRef('profile'), module__semester=OuterRef('semester'))))
            .select_related('profile', 'programme', 'class_level', 'semester')
            .order_by('profile__name'))


def issue_missing_student_passwords(*, actor, ip=None):
    """Every student in `students_without_password` gets a password of their
    own — never one shared by the class. Returns the list, the only time the
    passwords can be read."""
    issued = []
    for profile in list(students_without_password()):
        try:
            pin = reset_student_password(profile, actor=actor, ip=ip, first=True)
        except PasswordResetError:
            continue
        issued.append({'name': profile.name, 'reg_no': profile.nactvet_reg_no,
                       'college_id': profile.college_id or '', 'password': pin})
    return issued


def student_for_sign_in(reg_no, secret):
    """The enrollment a student signs in through, or None.

    Only the person's newest password counts — the one set last on any of
    their enrollments. An enrollment carrying some other password (an old one
    never updated, or one somebody else typed in) does not open the portal.
    Enrollments from before passwords were dated count as they always did."""
    from django.contrib.auth.hashers import check_password
    from django.db.models import F
    reg_no = str(reg_no).strip()
    if reg_no and not Student.objects.filter(nactvet_reg_no__iexact=reg_no).exists():
        # The college ID works too, as the password slip says (said 20 October
        # 2026): a first year's registration number is temporary until NACTVET's
        # arrives; the college ID they were admitted with does not change.
        profile = StudentProfile.objects.filter(college_id__iexact=reg_no).exclude(college_id='').first()
        if profile is not None:
            reg_no = profile.nactvet_reg_no
    rows = list(Student.objects.filter(nactvet_reg_no__iexact=reg_no)
                .exclude(portal_pin_hash='')
                .order_by(F('portal_pin_set_at').desc(nulls_last=True), '-id'))
    dated = [row for row in rows if row.portal_pin_set_at is not None]
    candidates = [row for row in dated if row.portal_pin_hash == dated[0].portal_pin_hash] if dated else rows
    for row in candidates:
        if check_password(str(secret), row.portal_pin_hash):
            return row
    return None


def copy_student_password(enrollment):
    """A new enrollment for somebody who already has a portal password carries
    that password — never a new one typed in by whoever enrolled them. Not
    dated, so it never becomes their "newest"."""
    if enrollment.portal_pin_hash:
        return
    existing = (Student.objects.filter(nactvet_reg_no__iexact=enrollment.nactvet_reg_no)
                .exclude(pk=enrollment.pk).exclude(portal_pin_hash='')
                .order_by(models.F('portal_pin_set_at').desc(nulls_last=True), '-id').first())
    if existing is not None:
        Student.objects.filter(pk=enrollment.pk).update(
            portal_pin_hash=existing.portal_pin_hash,
            must_change_portal_password=existing.must_change_portal_password)


# ── slowing down password guessing ────────────────────────────────────────────

def _failure_window():
    return timezone.now() - timedelta(minutes=getattr(settings, 'SIGN_IN_LOCK_MINUTES', 15))


def sign_in_blocked(identifier, ip):
    """Whether signing in is paused for this name or this address: too many
    failures in the last quarter of an hour. Checked before any password is."""
    from .models import SignInFailure
    since = _failure_window()
    recent = SignInFailure.objects.filter(at__gte=since)
    if identifier and recent.filter(identifier=identifier.strip().lower()).count() >= getattr(
            settings, 'SIGN_IN_FAILURES_PER_NAME', 10):
        return True
    # Generous per address: a whole class signs in through the college Wi-Fi.
    return bool(ip) and recent.filter(ip=ip).count() >= getattr(settings, 'SIGN_IN_FAILURES_PER_IP', 100)


def record_sign_in_failure(identifier, ip):
    from .models import SignInFailure
    SignInFailure.objects.create(identifier=(identifier or '').strip().lower()[:150], ip=ip or None)
    # Nothing older than a day is needed; trimmed here so the table stays small.
    SignInFailure.objects.filter(at__lt=timezone.now() - timedelta(days=1)).delete()


def clear_sign_in_failures(identifier):
    from .models import SignInFailure
    SignInFailure.objects.filter(identifier=(identifier or '').strip().lower()).delete()
