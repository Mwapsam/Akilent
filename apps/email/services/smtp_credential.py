"""SMTP relay credential provisioning business logic.

SmtpCredentialService owns:
  - Creating a dedicated SMTP AUTH identity for a verified domain
  - Rotating / revoking that identity on the mail provider
  - Audit log writes

Views import this service — never the provider directly. Mirrors the
DomainService/MailboxService/AliasService pattern in this package.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import TYPE_CHECKING

from apps.email.audit import record as audit
from apps.email.exceptions import EmailProviderError
from apps.email.models import EmailDomain, SmtpCredential
from apps.email.providers import get_mail_provider

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)


def _generate_secret() -> str:
    return secrets.token_urlsafe(24)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class SmtpCredentialService:
    """Orchestrates SMTP relay identity provisioning between Django and the mail provider."""

    def __init__(self, account, *, actor: "AbstractBaseUser | None" = None) -> None:
        self.account = account
        self.actor = actor
        self._provider = get_mail_provider()

    def provision(self, domain: EmailDomain) -> tuple[SmtpCredential, str]:
        """Create a dedicated SMTP relay identity for a verified domain.

        Returns (credential, plaintext_secret) — the plaintext is shown once
        and never persisted; the mail server holds the authoritative secret.
        """
        username = f"relay@{domain.domain}"
        secret = _generate_secret()
        try:
            self._provider.create_relay_identity(username, secret)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="smtp_credential.provision",
                resource_type="smtp_credential",
                resource_id=username,
                success=False,
                error="Provider error during create_relay_identity",
            )
            raise

        credential = SmtpCredential.objects.create(
            account=self.account,
            domain=domain,
            username=username,
            secret_hash=_hash(secret),
            last4=secret[-4:],
        )
        audit(
            account=self.account,
            actor=self.actor,
            action="smtp_credential.provision",
            resource_type="smtp_credential",
            resource_id=username,
        )
        logger.info(
            "SmtpCredentialService.provision: %s created (account=%s)",
            username,
            self.account.pk,
        )
        return credential, secret

    def rotate(self, credential: SmtpCredential) -> str:
        """Generate a new secret for an existing relay identity. Returns the plaintext once."""
        secret = _generate_secret()
        try:
            self._provider.rotate_relay_secret(credential.username, secret)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="smtp_credential.rotate",
                resource_type="smtp_credential",
                resource_id=credential.username,
                success=False,
                error="Provider error during rotate_relay_secret",
            )
            raise

        credential.secret_hash = _hash(secret)
        credential.last4 = secret[-4:]
        credential.save(update_fields=["secret_hash", "last4"])
        audit(
            account=self.account,
            actor=self.actor,
            action="smtp_credential.rotate",
            resource_type="smtp_credential",
            resource_id=credential.username,
        )
        return secret

    def revoke(self, credential: SmtpCredential) -> None:
        """Permanently remove a relay identity from the mail server."""
        try:
            self._provider.delete_relay_identity(credential.username)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="smtp_credential.revoke",
                resource_type="smtp_credential",
                resource_id=credential.username,
                success=False,
                error="Provider error during delete_relay_identity",
            )
            raise

        credential.is_active = False
        credential.save(update_fields=["is_active"])
        audit(
            account=self.account,
            actor=self.actor,
            action="smtp_credential.revoke",
            resource_type="smtp_credential",
            resource_id=credential.username,
        )
