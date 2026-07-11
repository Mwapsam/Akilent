"""Email services package.

Business logic that views and Celery tasks call — never the provider directly.

Provisioning services (DomainService, MailboxService, AliasService) orchestrate:
  - Plan limit enforcement
  - Provider calls
  - Django ORM sync
  - Audit log writes

Sending helpers (smtp_send, apply_tracking) are exported here for backwards
compatibility with existing tasks.py imports.
"""
from .alias import AliasService
from .domain import DomainService
from .mailbox import MailboxService
from .render import render_string, render_template
from .send import apply_tracking, smtp_send
from .smtp_credential import SmtpCredentialService

__all__ = [
    "DomainService",
    "MailboxService",
    "AliasService",
    "SmtpCredentialService",
    "smtp_send",
    "apply_tracking",
    "render_template",
    "render_string",
]
