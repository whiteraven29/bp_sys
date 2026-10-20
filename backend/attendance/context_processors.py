from django.conf import settings


def session_policy(request):
    """How long a page may sit unused before it signs itself out."""
    return {'IDLE_SECONDS': getattr(settings, 'IDLE_TIMEOUT_SECONDS', 300)}
