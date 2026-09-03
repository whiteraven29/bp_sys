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
