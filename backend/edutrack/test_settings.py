import tempfile

from .settings import *  # noqa: F403

# Keep test-uploaded files out of the real media/ directory.
MEDIA_ROOT = tempfile.mkdtemp(prefix='edutrack-test-media-')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
DEBUG = True
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# Tests sign in and post hundreds of times from one test client; the throttles
# are exercised by their own tests (test_security.py), which set the rates.
REST_FRAMEWORK = {**REST_FRAMEWORK, 'DEFAULT_THROTTLE_RATES': {  # noqa: F405
    'staff': '100000/min', 'portal': '100000/min', 'anonymous': '100000/min'}}
LOGGING = {**LOGGING, 'loggers': {  # noqa: F405
    'edutrack.security': {'handlers': [], 'level': 'CRITICAL', 'propagate': False},
    'django.security': {'handlers': [], 'level': 'CRITICAL', 'propagate': False}}}
