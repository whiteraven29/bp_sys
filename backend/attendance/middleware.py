"""Which hat a member of staff is wearing.

A tutor promoted to Principal does not stop teaching. But every admin role sees
the whole college by default, so their own two modules — the ones they take
attendance for every week — arrive buried in a list of every module the college
runs. Being promoted should not make your own classes harder to find.

The switch is a *view* scope and never a permission: a Principal working in
"my modules" still holds every principal right, they are simply looking at a
narrower set. Nothing here can widen what an account may do.
"""


class TeachingScopeMiddleware:
    """Carry the session's teaching-scope switch onto the request's user.

    `user_modules()` is called from twenty-odd places with `request.user` and
    nothing else, so the switch rides along on the user rather than being
    threaded through every one of them.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            user.teaching_scope_only = bool(request.session.get('teaching_scope_only'))
        return self.get_response(request)


class PasswordExpiryMiddleware:
    """Stop a member of staff at the door when their password is past its life.

    The college's rule is six months, then change it, with a fortnight's grace.
    A rule nobody enforces is a note on a wall, so it is enforced here rather
    than in each of the sixty-odd views: an expired account is sent to the
    change-password page and can reach nothing else, and one that let the grace
    run out is signed out and told to see the administrator.

    Deliberately narrow: it never touches the student portal (which checks at
    its own door, on the session), never the login, logout or change-password
    pages themselves, and never static files — locking somebody out of the page
    that would let them back in is the one failure mode this must not have.
    """

    EXEMPT_PREFIXES = ('/login', '/logout', '/static/', '/media/', '/password/')
    EXEMPT_EXACT = ('/api/change-password/', '/api/dashboard/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.contrib.auth import logout
        from django.shortcuts import redirect
        from django.urls import reverse

        user = getattr(request, 'user', None)
        path = request.path
        if (user is None or not getattr(user, 'is_authenticated', False)
                or path.startswith(self.EXEMPT_PREFIXES) or path in self.EXEMPT_EXACT):
            return self.get_response(request)

        from . import passwords

        reading = passwords.reading_for(user)
        if reading is None:
            return self.get_response(request)
        if reading['state'] == passwords.LOCKED:
            # Told on the login page itself rather than through the messages
            # framework, which is not set up this early in the chain — and a
            # sign-out that explains nothing is how people conclude the system
            # is broken.
            logout(request)
            return redirect(f"{reverse('login')}?expired=1")
        if reading['must_change']:
            return redirect('password-change')
        return self.get_response(request)


class IdleTimeoutMiddleware:
    """A session nobody has used for five minutes ends (said 19 October 2026).

    Staff and students alike: a shared office PC or a phone left on a desk does
    not stay signed in. "Used" means a request the person made — the
    notification poll every minute is the page talking to itself and does not
    count. The page sends a small keep-alive while somebody is actually typing
    or clicking, so a long form is not lost to it.
    """

    #: Polls the pages make by themselves: they neither count as use nor, after
    #: the limit, keep anybody signed in.
    BACKGROUND = ('/api/notifications/', '/api/my-notifications/')
    STAMP = 'last_activity'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import time

        from django.conf import settings
        from django.contrib.auth import logout
        from django.http import JsonResponse
        from django.shortcuts import redirect

        session = getattr(request, 'session', None)
        user = getattr(request, 'user', None)
        signed_in = bool(user is not None and getattr(user, 'is_authenticated', False)) or bool(
            session is not None and (session.get('student_id') or session.get('course_profile_id')))
        if not signed_in or request.path.startswith(('/static/', '/login', '/logout', '/student-logout')):
            return self.get_response(request)

        limit = getattr(settings, 'IDLE_TIMEOUT_SECONDS', 300)
        now = time.time()
        last = session.get(self.STAMP)
        if last is not None and now - last > limit:
            logout(request)                     # flushes the session, staff or student
            if request.path.startswith('/api/') or request.path == '/keep-alive/':
                return JsonResponse({'detail': 'You were signed out after 5 minutes without use. Sign in again.',
                                     'idle': True}, status=401)
            return redirect('/login/?idle=1')
        if request.path not in self.BACKGROUND and (last is None or now - last > 15):
            session[self.STAMP] = now           # written at most every 15 seconds
        return self.get_response(request)
