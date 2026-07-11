"""Test settings: deterministic, no external services.

Env defaults are set *before* importing the base settings so that ``DATABASES``
and the required-secret checks resolve to local test values regardless of when
pytest loads conftest files.
"""
import os

from cryptography.fernet import Fernet

os.environ.setdefault("USE_SQLITE", "1")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("FLUTTERWAVE_SECRET_KEY", "FLWSECK_TEST-testkey")
os.environ.setdefault("FLUTTERWAVE_WEBHOOK_HASH", "test-hash")

from automator.settings import *  # noqa: F401,F403,E402

# Capture mail in django.core.mail.outbox instead of hitting SMTP.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Run Celery tasks inline (no broker needed) so `.delay()` calls in views —
# e.g. verification emails, transactional sends — execute synchronously and
# stay observable via mail.outbox in tests.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
