"""
Django settings for UMIS-D.

Environment-driven. PostgreSQL is the only supported database.
No import-time side effects: directories are not created here.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Core security -----------------------------------------------------------

SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError(
        "SECRET_KEY environment variable is required. "
        "Set it in .env or in the process environment."
    )

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,web').split(',')
    if host.strip()
]

# Needed behind a reverse proxy or when the app is served by hostname.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]

USE_X_FORWARDED_HOST = True
# Caddy sends X-Forwarded-Proto; Django only trusts it when it matches.
# Harmless for the plain-HTTP demo, required once TLS is enabled.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Site identity, shown in templates.
SITE_NAME = os.environ.get('SITE_NAME', 'UMIS-D')

# --- Applications --------------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'phonenumber_field',
    'core',
    'inventory',
    'control',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.AuditLogMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'mis_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.site_settings',
                'core.context_processors.session_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'mis_project.wsgi.application'
ASGI_APPLICATION = 'mis_project.asgi.application'

# --- Database ------------------------------------------------------------------

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'umis'),
        'USER': os.environ.get('POSTGRES_USER', 'umis'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': 600,
        'CONN_HEALTH_CHECKS': True,
    }
}

# --- Authentication ------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Region used to parse national-format phone numbers. Set a two-letter country
# code (for example "KG") so staff can type local numbers without a prefix.
PHONENUMBER_DEFAULT_REGION = os.environ.get('PHONENUMBER_DEFAULT_REGION') or None
# Phones are always stored in E.164 so search and matching stay consistent.
PHONENUMBER_DB_FORMAT = 'E164'

# --- Clinic hours ----------------------------------------------------------------
# Shown on the doctor page and used by the agent's availability command.
BOOKING_OPEN_HOUR = int(os.environ.get('BOOKING_OPEN_HOUR', '9'))
BOOKING_CLOSE_HOUR = int(os.environ.get('BOOKING_CLOSE_HOUR', '18'))
BOOKING_SLOT_MINUTES = int(os.environ.get('BOOKING_SLOT_MINUTES', '30'))

# --- Demo data -----------------------------------------------------------------
# Destructive demo reset is opt-in. Never enable this on a real clinic database.
DEMO_ALLOW_RESET = os.environ.get('DEMO_ALLOW_RESET', 'False') == 'True'

# --- Agent control ---------------------------------------------------------------
# Secret lives in deployment configuration only. The base URL and timeout are
# code defaults; the model is deployment-selectable.
VULTR_SERVERLESS_INFERENCE_API_KEY = os.environ.get(
    'VULTR_SERVERLESS_INFERENCE_API_KEY', '',
)
VULTR_INFERENCE_MODEL = os.environ.get(
    'VULTR_INFERENCE_MODEL', 'deepseek-v4.1-flash-1017',
)
VULTR_INFERENCE_BASE_URL = 'https://api.vultrinference.com/v1/chat/completions'
VULTR_INFERENCE_TIMEOUT = int(os.environ.get('VULTR_INFERENCE_TIMEOUT', '90'))

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = 'login'

# Session policy is deployment-configurable. Default is a generic workday session.
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', 43200))
SESSION_SAVE_EVERY_REQUEST = True
SESSION_WARN_SECONDS = int(os.environ.get('SESSION_WARN_SECONDS', 120))

# --- Internationalization ------------------------------------------------------

LANGUAGE_CODE = os.environ.get('LANGUAGE_CODE', 'en')
TIME_ZONE = os.environ.get('TIME_ZONE', 'UTC')
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ('en', 'English'),
]

# --- Static files --------------------------------------------------------------

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').exists() else []

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        # Content-hashed names + compression; tolerates vendored bundles that
        # reference source maps we do not ship (see core/storage.py).
        'BACKEND': 'core.storage.UMISStaticFilesStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Logging -------------------------------------------------------------------

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name}:{lineno} ({funcName}) {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': LOG_LEVEL,
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# --- Security ----------------------------------------------------------------------

# Cookie and redirect flags are deployment-configurable so the same settings
# module serves the plain-HTTP demo and a future HTTPS deployment. Secure
# cookies are on by default in production; the HTTP demo sets both to False.
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', str(not DEBUG)) == 'True'
CSRF_COOKIE_SECURE = os.environ.get('CSRF_COOKIE_SECURE', str(not DEBUG)) == 'True'
SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'False') == 'True'
# Enable with HTTPS and a verified domain:
# SECURE_HSTS_SECONDS = 31536000

# Number of reverse proxies in front of the app. Used to pick the client IP
# from X-Forwarded-For without trusting arbitrary hops.
TRUSTED_PROXY_COUNT = int(os.environ.get('TRUSTED_PROXY_COUNT', 1))
