import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Core ---

DEBUG = os.getenv("DEBUG", "False").lower() == "true"

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY and not DEBUG:
    raise ValueError("DJANGO_SECRET_KEY is required in production")

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

_extra_origins = os.getenv("CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _extra_origins.split(",") if o.strip()]

# --- Applications ---

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core",
    "apps.accounts",
    "apps.whatsapp",
    "apps.email",
    "apps.bitrix",
    "apps.automation",
    "apps.billing",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serve built static assets (CSS/JS/fonts) compressed + cache-busted.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "automator.urls"
WSGI_APPLICATION = "automator.wsgi.application"

# --- Templates ---

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site_context",
                "apps.accounts.context_processors.onboarding_status",
            ],
        },
    },
]

# --- Database ---

if os.getenv("USE_SQLITE", "0") == "1":
    # Local dev convenience: run without Postgres/docker.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "automator"),
            "USER": os.getenv("POSTGRES_USER", "automator"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD"),
            "HOST": os.getenv("POSTGRES_HOST", "db"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }

    if not DEBUG and not DATABASES["default"]["PASSWORD"]:
        raise ValueError("POSTGRES_PASSWORD is required")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/auth/login/"

# --- Auth ---

# Email is the login credential; ModelBackend stays as a fallback (Django
# admin and any username-based auth keep working unchanged).
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalization ---

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files ---

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Compressed + hashed (cache-busted) static files via WhiteNoise in production.
# In DEBUG keep the plain backend so `runserver` needs no manifest/collectstatic.
_staticfiles_backend = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": _staticfiles_backend},
}

# User-uploaded files (e.g. the site logo). In DEBUG these are served by Django
# (see automator/urls.py); in production point a web server / volume at MEDIA_ROOT.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

BASE_DOMAIN = os.getenv("BASE_DOMAIN", "localhost:8000")


# --- Encryption ---

FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY")
if not FIELD_ENCRYPTION_KEY:
    raise ValueError(
        "FIELD_ENCRYPTION_KEY must be set. "
        "Generate one using Fernet.generate_key()."
    )
FIELD_ENCRYPTION_KEYS = [FIELD_ENCRYPTION_KEY]

# --- Feature flags ---
# Soft-disable the non-email verticals. Apps stay in INSTALLED_APPS (so models,
# migrations and signals remain intact); these flags gate their URLs, nav,
# Celery schedule and startup secret validation. Flip to True to re-enable.

WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "False").lower() == "true"
BITRIX_ENABLED = os.getenv("BITRIX_ENABLED", "False").lower() == "true"

# --- WhatsApp ---

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")

# Embedded Signup (Tech Provider onboarding). app_id + config_id come from the
# onboarding link Meta gives you in the App dashboard.
WHATSAPP_APP_ID = os.getenv("WHATSAPP_APP_ID", "")
WHATSAPP_CONFIG_ID = os.getenv("WHATSAPP_CONFIG_ID", "")
WHATSAPP_GRAPH_VERSION = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")

if not DEBUG and WHATSAPP_ENABLED:
    if not WHATSAPP_VERIFY_TOKEN:
        raise ValueError("WHATSAPP_VERIFY_TOKEN is missing")
    if not WHATSAPP_APP_SECRET:
        raise ValueError("WHATSAPP_APP_SECRET is missing")

# --- Bitrix ---
BITRIX_CLIENT_ID = os.getenv("BITRIX_CLIENT_ID")
BITRIX_CLIENT_SECRET = os.getenv("BITRIX_CLIENT_SECRET")

# The portal you authorize against, e.g. "mycompany.bitrix24.com" (no scheme).
# OAuth authorization happens on the portal itself; only the token exchange
# uses oauth.bitrix.info.
BITRIX_PORTAL_DOMAIN = os.getenv("BITRIX_PORTAL_DOMAIN", "")

BITRIX24_WEBHOOK_TIMEOUT = 10
BITRIX24_OAUTH_REDIRECT_URL = os.getenv(
    "BITRIX24_OAUTH_REDIRECT_URL",
    f"https://{BASE_DOMAIN}/auth/bitrix/callback/",
)

if not DEBUG and BITRIX_ENABLED:
    if not BITRIX_CLIENT_ID:
        raise ValueError(
            "BITRIX_CLIENT_ID missing"
        )

    if not BITRIX_CLIENT_SECRET:
        raise ValueError(
            "BITRIX_CLIENT_SECRET missing"
        )

# --- Slack ---

# Incoming webhook URL for admin alerts (e.g. manual payment review requests).
# Create one at https://api.slack.com/messaging/webhooks. Blank disables
# posting — this isn't required in production the way email/Stalwart are.
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# --- Email (Stalwart Mail Server) ---

# Stalwart HTTP Management API — provisions per-tenant domains, DKIM,
# mailboxes and aliases. Port 8080 is internal-only (not published to host).
# Auth is a static, pre-generated Bearer API key (format "API_..."),
# created in the Stalwart admin UI/CLI — not a username/password login.
STALWART_API_BASE = os.getenv("STALWART_API_BASE", "")
STALWART_API_KEY = os.getenv("STALWART_API_KEY", "")

# Provider selector — swap the implementation without touching business logic.
MAIL_PROVIDER_BACKEND = os.getenv("MAIL_PROVIDER_BACKEND", "stalwart")

# Public domain used to build absolute tracking URLs in outgoing emails.
BASE_DOMAIN = os.getenv("BASE_DOMAIN", (ALLOWED_HOSTS[0] if ALLOWED_HOSTS else "localhost"))

# SMTP submission credentials (Stalwart port 587) used to actually send mail.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "True").lower() == "true"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@localhost")

# Email is the always-on v1 product (unlike the flagged-off WhatsApp/Bitrix
# verticals above), so these secrets are required in every production
# deployment rather than gated behind a feature flag.
if not DEBUG:
    if not STALWART_API_BASE:
        raise ValueError("STALWART_API_BASE is required")
    if not STALWART_API_KEY:
        raise ValueError("STALWART_API_KEY is required")
    if not EMAIL_HOST:
        raise ValueError("EMAIL_HOST is required")
    if not EMAIL_HOST_USER:
        raise ValueError("EMAIL_HOST_USER is required")
    if not EMAIL_HOST_PASSWORD:
        raise ValueError("EMAIL_HOST_PASSWORD is required")

# The external Stalwart submission endpoint, shown to customers setting up
# per-tenant SMTP relay (apps.api / apps.email SmtpCredential docs).
SMTP_RELAY_HOST = os.getenv("SMTP_RELAY_HOST", EMAIL_HOST)
SMTP_RELAY_PORT = int(os.getenv("SMTP_RELAY_PORT", str(EMAIL_PORT)))

# --- REST API (Developer Platform) ---
# Public, versioned API surface (apps.api). Auth/permission classes are set
# per-view rather than as DRF defaults, so this never touches the
# session-authenticated dashboard views in apps.email/apps.accounts/etc.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_THROTTLE_CLASSES": [],
    "EXCEPTION_HANDLER": "apps.api.errors.custom_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# --- Flutterwave ---

FLUTTERWAVE_SECRET_KEY = os.getenv("FLUTTERWAVE_SECRET_KEY")
FLUTTERWAVE_WEBHOOK_HASH = os.getenv("FLUTTERWAVE_WEBHOOK_HASH")
FLUTTERWAVE_CURRENCY = os.getenv("FLUTTERWAVE_CURRENCY", "USD")

# --- Cache ---
# Backs DRF request-rate throttling and the API-key bad-attempt lockout
# counter (apps.api.authentication) — must be shared across gunicorn workers,
# so LocMemCache (Django's default) won't do outside of tests. Redis is
# already a dependency for Celery; a separate DB index keeps the keyspaces apart.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("REDIS_CACHE_URL", "redis://redis:6379/1"),
    }
}

# --- Celery ---

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Email + billing always run; the WhatsApp/Bitrix routes and schedules are only
# registered when their feature flag is on (see "Feature flags" above).
CELERY_TASK_ROUTES = {
    # Accounts
    "apps.accounts.tasks.send_verification_email": {"queue": "celery"},
    # Email provisioning
    "apps.email.tasks.send_email": {"queue": "outbound"},
    "apps.email.tasks.send_bulk_recipient_email": {"queue": "outbound"},
    "apps.email.tasks.dispatch_campaign": {"queue": "campaigns"},
    "apps.email.tasks.provision_mailbox": {"queue": "email"},
    "apps.email.tasks.provision_mailbox_async": {"queue": "email"},
    "apps.email.tasks.deprovision_mailbox_async": {"queue": "email"},
    "apps.email.tasks.change_password_async": {"queue": "email"},
    "apps.email.tasks.set_quota_async": {"queue": "email"},
    "apps.email.tasks.rotate_dkim_async": {"queue": "email"},
    "apps.email.tasks.provision_domain_async": {"queue": "email"},
    "apps.email.tasks.deliver_webhook": {"queue": "webhooks"},
    # Maintenance
    "apps.email.tasks.prune_email_logs": {"queue": "celery"},
    "apps.email.tasks.prune_tracking_tokens": {"queue": "celery"},
    "apps.email.tasks.prune_provisioning_jobs": {"queue": "celery"},
}

CELERY_BEAT_SCHEDULE = {
    "expire-trials": {
        "task": "apps.billing.tasks.expire_trials",
        "schedule": 3600.0,
    },
    "prune-email-logs": {
        "task": "apps.email.tasks.prune_email_logs",
        "schedule": 86400.0,
    },
    "prune-tracking-tokens": {
        "task": "apps.email.tasks.prune_tracking_tokens",
        "schedule": 86400.0,
    },
    "prune-provisioning-jobs": {
        "task": "apps.email.tasks.prune_provisioning_jobs",
        "schedule": 86400.0,
    },
}

if WHATSAPP_ENABLED:
    CELERY_TASK_ROUTES.update({
        "apps.whatsapp.tasks.process_whatsapp_event": {"queue": "whatsapp"},
        "apps.whatsapp.tasks.drain_outbound_queue": {"queue": "outbound"},
    })
    CELERY_BEAT_SCHEDULE.update({
        "close-expired-conversations": {
            "task": "apps.whatsapp.tasks.close_expired_conversations",
            "schedule": 3600.0,
        },
        "drain-outbound-queue": {
            "task": "apps.whatsapp.tasks.drain_outbound_queue",
            "schedule": 10.0,
        },
        "download-media": {
            "task": "apps.whatsapp.tasks.download_media",
            "schedule": 60.0,
        },
    })

if BITRIX_ENABLED:
    CELERY_TASK_ROUTES["apps.bitrix.tasks.process_bitrix_webhook"] = {"queue": "bitrix"}
    CELERY_BEAT_SCHEDULE["refresh-bitrix-tokens"] = {
        "task": "apps.bitrix.tasks.refresh_tokens",
        "schedule": 300.0,
    }

# --- Logging ---

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "[{levelname}] {asctime} {name}:{lineno} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "automator.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "DEBUG" if DEBUG else "INFO",
    },
}

# --- Production security ---

if not DEBUG:
    # SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    CSRF_TRUSTED_ORIGINS = [
        "https://akilent.com",
        "https://www.akilent.com",
    ]

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        send_default_pii=False,
    )
