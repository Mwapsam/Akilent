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

# --- Applications ---

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.whatsapp",
    "apps.bitrix",
    "apps.automation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ---

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

# --- Auth ---

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

# --- Encryption ---

FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY")
if not FIELD_ENCRYPTION_KEY:
    raise ValueError(
        "FIELD_ENCRYPTION_KEY must be set. "
        "Generate one using Fernet.generate_key()."
    )
FIELD_ENCRYPTION_KEYS = [FIELD_ENCRYPTION_KEY]

# --- WhatsApp ---

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")

if not DEBUG:
    if not WHATSAPP_VERIFY_TOKEN:
        raise ValueError("WHATSAPP_VERIFY_TOKEN is missing")
    if not WHATSAPP_APP_SECRET:
        raise ValueError("WHATSAPP_APP_SECRET is missing")

# --- Bitrix ---
BITRIX_CLIENT_ID = os.getenv("BITRIX_CLIENT_ID")
BITRIX_CLIENT_SECRET = os.getenv("BITRIX_CLIENT_SECRET")

BITRIX24_WEBHOOK_TIMEOUT = 10
BITRIX24_OAUTH_REDIRECT_URL = os.getenv(
    "BITRIX24_OAUTH_REDIRECT_URL",
    "http://localhost:8000/auth/bitrix/callback/",
)

if not DEBUG:
    if not BITRIX_CLIENT_ID:
        raise ValueError(
            "BITRIX_CLIENT_ID missing"
        )

    if not BITRIX_CLIENT_SECRET:
        raise ValueError(
            "BITRIX_CLIENT_SECRET missing"
        )

# --- Celery ---

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

CELERY_TASK_ROUTES = {
    "apps.whatsapp.tasks.process_whatsapp_event": {"queue": "whatsapp"},
    "apps.bitrix.tasks.process_bitrix_webhook": {"queue": "bitrix"},
    "apps.whatsapp.tasks.drain_outbound_queue": {"queue": "outbound"},
}

CELERY_BEAT_SCHEDULE = {
    "refresh-bitrix-tokens": {
        "task": "apps.bitrix.tasks.refresh_tokens",
        "schedule": 300.0,
    },
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
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
