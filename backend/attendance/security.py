"""The API's defaults, set in settings.REST_FRAMEWORK, and response headers.

Two kinds of people use the API:

  * staff, signed in through Django (`request.user`);
  * students, whose portal runs on a plain session (`student_id`, or
    `course_profile_id` for a VETA course) — to DRF they are anonymous.

DRF only checks CSRF for signed-in users, and with no default permission a view
that forgot its permission class would be open to anybody. These close both:
a student's request that changes something must carry the CSRF token, and the
default is "signed-in staff or a signed-in student" rather than "anyone".
Throttles count each staff account and each student session on its own, and
fall back to the address only for somebody who is neither — a whole class
signs in through the college Wi-Fi, so counting students by address would
stop them all at once.
"""

from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, CSRFCheck
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.throttling import SimpleRateThrottle

STUDENT_SESSION_KEYS = ('student_id', 'course_profile_id')


def student_session(request):
    """The student (or VETA course student) this session belongs to, as a
    string key, or None. Works on DRF and plain Django requests alike."""
    session = getattr(getattr(request, '_request', request), 'session', None)
    if session is None:
        return None
    for key in STUDENT_SESSION_KEYS:
        if session.get(key):
            return f'{key}:{session[key]}'
    return None


class StudentSessionCSRF(BaseAuthentication):
    """Authenticates nobody — a student stays anonymous to DRF and each view
    checks the session itself — but a student's request that changes
    something must carry the CSRF token, as a staff member's already must."""

    def authenticate(self, request):
        if request.method not in SAFE_METHODS and student_session(request):
            def dummy_get_response(_):    # pragma: no cover - never called
                return None
            check = CSRFCheck(dummy_get_response)
            check.process_request(request._request)
            reason = check.process_view(request._request, None, (), {})
            if reason:
                raise exceptions.PermissionDenied(f'CSRF Failed: {reason}')
        return None


class SignedInOrStudentSession(BasePermission):
    """The default for every API view: signed-in staff, or a signed-in
    student. Each view still checks the office or the person itself; this
    only makes sure a view that forgot to is not open to everybody."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated) or bool(student_session(request))


class StaffRateThrottle(SimpleRateThrottle):
    """Each signed-in member of staff."""
    scope = 'staff'

    def get_cache_key(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return None
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}


class PortalRateThrottle(SimpleRateThrottle):
    """Each signed-in student, by their session — not by address."""
    scope = 'portal'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return None
        ident = student_session(request)
        if ident is None:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class AnonymousRateThrottle(SimpleRateThrottle):
    """Somebody who is neither staff nor a student: by address."""
    scope = 'anonymous'

    def get_cache_key(self, request, view):
        if (request.user and request.user.is_authenticated) or student_session(request):
            return None
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class SecurityHeadersMiddleware:
    """Headers Django does not set by itself.

    The Content-Security-Policy is the part that costs nothing today: no
    plugins, no <base> tag that could repoint relative links, forms post only
    here, and only this site may frame a page. A policy that also stops inline
    script needs the pages' inline handlers moved out first (security review
    S14)."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.policy = getattr(settings, 'CONTENT_SECURITY_POLICY', '')

    def __call__(self, request):
        response = self.get_response(request)
        if self.policy and 'Content-Security-Policy' not in response:
            response['Content-Security-Policy'] = self.policy
        response.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        return response


def neutralise_formulas(workbook):
    """Every cell that openpyxl would store as a formula, stored as the text it
    is. openpyxl turns any text starting with "=" into a formula, so a name or
    a form answer like =HYPERLINK(...) would come alive when staff open the
    export. None of the exports writes a formula on purpose. Returns the
    workbook, for chaining."""
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == 'f':
                    cell.data_type = 's'
    return workbook
