"""Mail provider factory.

Resolves the configured backend at runtime via the MAIL_PROVIDER_BACKEND
Django setting. Supports built-in short aliases and full dotted import paths
so any EmailProvider implementation can be plugged in without changing this file.

Built-in aliases:
    stalwart  — Stalwart Mail Server HTTP Management API (production default)
    null      — no-op stub that succeeds silently (local dev / CI)

Custom providers — set MAIL_PROVIDER_BACKEND to a full dotted import path:
    MAIL_PROVIDER_BACKEND=myapp.mail.providers.MailcowProvider
"""
import importlib

from django.conf import settings

from .base import (
    DkimResult,
    EmailProvider,
    MailProvider,
    MailProviderError,
    ProvisionResult,
)
from .send_base import EmailSendProvider
from apps.email.exceptions import EmailProviderError
from apps.email.types import (
    AliasInfo,
    DkimRecord,
    DomainInfo,
    MailboxInfo,
    OperationResult,
    OutboundEmail,
    QuotaInfo,
    SendResult,
)

_ALIASES: dict[str, str] = {
    "stalwart": "apps.email.providers.stalwart.StalwartProvider",
    "null":     "apps.email.providers.null.NullProvider",
}

_SEND_ALIASES: dict[str, str] = {
    "smtp": "apps.email.providers.smtp.SmtpSendProvider",
}


def get_mail_provider() -> EmailProvider:
    """Return an instance of the configured mail infrastructure provider.

    MAIL_PROVIDER_BACKEND accepts either a short alias ("stalwart", "null") or a
    full dotted import path ("myapp.providers.postfix.PostfixProvider"), so any
    class that implements EmailProvider can be plugged in without touching this
    file or any call site.
    """
    backend: str = getattr(settings, "MAIL_PROVIDER_BACKEND", "stalwart")
    dotted = _ALIASES.get(backend, backend)
    if "." not in dotted:
        raise ValueError(
            f"MAIL_PROVIDER_BACKEND {backend!r} is not a known alias and is not "
            "a dotted import path (e.g. 'myapp.mail.MyProvider')."
        )
    module_path, class_name = dotted.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
        cls: type[EmailProvider] = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(
            f"Cannot load mail provider {dotted!r}: {exc}"
        ) from exc
    return cls()


def get_send_provider() -> EmailSendProvider:
    """Return an instance of the configured outbound-message send provider.

    EMAIL_SEND_PROVIDER_BACKEND accepts either a short alias ("smtp") or a
    full dotted import path, mirroring get_mail_provider()'s resolution.
    Defaults to "smtp" — today's only implementation, wrapping the existing
    self-hosted SMTP relay send path unchanged.
    """
    backend: str = getattr(settings, "EMAIL_SEND_PROVIDER_BACKEND", "smtp")
    dotted = _SEND_ALIASES.get(backend, backend)
    if "." not in dotted:
        raise ValueError(
            f"EMAIL_SEND_PROVIDER_BACKEND {backend!r} is not a known alias and is "
            "not a dotted import path (e.g. 'myapp.mail.MySendProvider')."
        )
    module_path, class_name = dotted.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
        cls: type[EmailSendProvider] = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(
            f"Cannot load send provider {dotted!r}: {exc}"
        ) from exc
    return cls()


__all__ = [
    # Factory
    "get_mail_provider",
    "get_send_provider",
    # Abstract interface
    "EmailProvider",
    "MailProvider",          # backwards-compat alias
    "EmailSendProvider",
    # Exceptions
    "EmailProviderError",
    "MailProviderError",     # backwards-compat alias
    # Legacy result shims
    "ProvisionResult",
    "DkimResult",
    # Normalized types
    "DomainInfo",
    "MailboxInfo",
    "AliasInfo",
    "DkimRecord",
    "QuotaInfo",
    "OperationResult",
    "OutboundEmail",
    "SendResult",
]
