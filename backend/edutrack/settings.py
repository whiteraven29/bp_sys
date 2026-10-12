from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    value = config(name, default=str(default))
    return str(value).strip().lower() in {
        '1', 'true', 'yes', 'on', 'debug', 'development',
    }


_INSECURE_KEY = 'django-insecure-change-me-in-production'
SECRET_KEY = config('SECRET_KEY', default=_INSECURE_KEY)
DEBUG = env_bool('DEBUG', default=False)
# Anybody who knows the key can forge a session, so a live server must not run
# on the default one: it refuses to start instead.
if not DEBUG and (not SECRET_KEY or SECRET_KEY == _INSECURE_KEY):
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured('SECRET_KEY is not set. Put a long random value in the .env file.')
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'attendance',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # After authentication: it decorates request.user.
    'attendance.middleware.TeachingScopeMiddleware',
    # After it, so request.user is decorated before the password is judged.
    'attendance.middleware.PasswordExpiryMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'attendance.security.SecurityHeadersMiddleware',
]

# What costs nothing today (attendance/security.py). Stopping inline script as
# well needs the pages' inline handlers moved out first.
CONTENT_SECURITY_POLICY = config(
    'CONTENT_SECURITY_POLICY',
    default="object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")

# ── SIGNING IN ────────────────────────────────────────────────────────────────
# Failures for one name, or from one address, within the window pause signing
# in for it (attendance/passwords.py). Generous per address: a whole class signs
# in through the college Wi-Fi.
SIGN_IN_LOCK_MINUTES = config('SIGN_IN_LOCK_MINUTES', default=15, cast=int)
SIGN_IN_FAILURES_PER_NAME = config('SIGN_IN_FAILURES_PER_NAME', default=10, cast=int)
SIGN_IN_FAILURES_PER_IP = config('SIGN_IN_FAILURES_PER_IP', default=100, cast=int)

# ── PASSWORD POLICY ───────────────────────────────────────────────────────────
#
# A password lives six months. After that the holder is made to change it the
# next time they sign in, and they have a fortnight's grace in which to do it.
# Miss that and the account stops opening: the office resets it, which is the
# point — an account nobody has touched in half a year is the one worth
# checking on before it is handed back.
PASSWORD_MAX_AGE_DAYS = config('PASSWORD_MAX_AGE_DAYS', default=182, cast=int)
PASSWORD_GRACE_DAYS = config('PASSWORD_GRACE_DAYS', default=14, cast=int)
#: How many days before expiry the dashboards start saying so.
PASSWORD_WARN_DAYS = config('PASSWORD_WARN_DAYS', default=14, cast=int)

ROOT_URLCONF = 'edutrack.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'edutrack.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='edutrack_db'),
        'USER': config('DB_USER', default='postgres'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
        # Reuse a Postgres connection across requests within a worker instead of
        # opening/closing a new one every request — cuts per-request latency and
        # connection churn under concurrent load. CONN_HEALTH_CHECKS pings a
        # reused connection before use so a connection Postgres dropped while
        # idle doesn't surface as a request error.
        'CONN_MAX_AGE': config('DB_CONN_MAX_AGE', default=60, cast=int),
        'CONN_HEALTH_CHECKS': True,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Nairobi'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Uploaded announcement PDFs. Not served through a public /media/ nginx
# location — every download goes through the gated announcement_download
# view, which checks the requester is a logged-in student or staff member.
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Authentication sessions are browser-session scoped: the browser receives no
# persistent expiry date and removes the cookie when the browser session ends.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
# And at most a working day: a browser left open on a shared office PC does not
# stay signed in overnight.
SESSION_COOKIE_AGE = config('SESSION_COOKIE_AGE', default=8 * 60 * 60, cast=int)

# DRF
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PARSER_CLASSES': ['rest_framework.parsers.JSONParser', 'rest_framework.parsers.MultiPartParser'],
    # The session only. DRF's default also accepts HTTP Basic passwords, which
    # let anybody guess passwords straight at /api/ — no sign-in page, no
    # lockout. A student's session request must carry the CSRF token too.
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'attendance.security.StudentSessionCSRF',
        'rest_framework.authentication.SessionAuthentication',
    ],
    # Signed-in staff or a signed-in student, never "anyone"; every view still
    # checks the office or the person itself (attendance/security.py).
    'DEFAULT_PERMISSION_CLASSES': ['attendance.security.SignedInOrStudentSession'],
    'DEFAULT_THROTTLE_CLASSES': [
        'attendance.security.StaffRateThrottle',
        'attendance.security.PortalRateThrottle',
        'attendance.security.AnonymousRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'staff': config('THROTTLE_STAFF', default='600/min'),
        'portal': config('THROTTLE_PORTAL', default='120/min'),
        'anonymous': config('THROTTLE_ANONYMOUS', default='30/min'),
    },
    # nginx is the one proxy in front: the last X-Forwarded-For entry is the
    # real address (the first is whatever the client sent).
    'NUM_PROXIES': 1,
}

# Failed sign-ins, password resets and refused requests, to the service's log
# (journalctl -u edutrack).
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'plain': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'}},
    'loggers': {
        'edutrack.security': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
    },
}

# CORS — allow frontend served from any origin during dev
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = [
    'http://localhost:8000',
    'http://127.0.0.1:8000',
]

# Production security. Nginx terminates HTTPS and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', default=not DEBUG)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
