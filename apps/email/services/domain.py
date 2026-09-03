"""Domain provisioning business logic.

DomainService is the authoritative place for:
  - Creating, updating, and deleting sending domains
  - Keeping EmailDomain (DB) in sync with the mail provider
  - Writing AuditLog entries for every mutation
  - Firing async Celery jobs for heavy provisioning work

Views and tasks import this service — never the provider directly.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.utils import timezone

from apps.email.audit import record as audit
from apps.email.exceptions import EmailProviderError
from apps.email.models import EmailDomain
from apps.email.providers import get_mail_provider
from apps.email.types import DkimRecord, DomainInfo, OperationResult

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)


class DomainService:
    """Orchestrates domain provisioning between Django and the mail provider."""

    def __init__(self, account, *, actor: "AbstractBaseUser | None" = None) -> None:
        self.account = account
        self.actor = actor
        self._provider = get_mail_provider()

    # ── Public API ────────────────────────────────────────────────────────

    def provision(
        self,
        domain_record: EmailDomain,
        *,
        selector: str | None = None,
    ) -> DomainInfo:
        """Create the domain on the mail server and populate DKIM fields.

        Updates domain_record in place (dkim_public_key, status) and saves it.
        Raises EmailProviderError on failure — caller decides how to surface it.
        """
        selector = selector or domain_record.dkim_selector or "dkim"
        try:
            info = self._provider.create_domain(
                domain_record.domain,
                description=f"Automator account {self.account.pk}",
            )
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="domain.provision",
                resource_type="domain",
                resource_id=domain_record.domain,
                success=False,
                error="Provider error during create_domain",
            )
            raise

        dkim = info.dkim
        if dkim:
            domain_record.dkim_public_key = dkim.public_key_txt
            domain_record.dkim_selector = dkim.selector
            domain_record.save(update_fields=["dkim_public_key", "dkim_selector"])
        elif hasattr(self._provider, "get_dkim_records"):
            # Multi-record DKIM (AWS SES Easy DKIM = 3 CNAMEs): the tokens aren't
            # available at create time, so fetch them now and materialise the
            # full DNS spec as EmailDnsRecord rows for the customer to publish.
            self._sync_dns_records(domain_record)

        audit(
            account=self.account,
            actor=self.actor,
            action="domain.provision",
            resource_type="domain",
            resource_id=domain_record.domain,
            metadata={"selector": selector},
        )
        logger.info(
            "DomainService.provision: %s provisioned (account=%s)",
            domain_record.domain,
            self.account.pk,
        )
        return info

    def enable(self, domain_record: EmailDomain) -> OperationResult:
        """Enable a previously disabled domain."""
        return self._toggle(domain_record, active=True)

    def disable(self, domain_record: EmailDomain) -> OperationResult:
        """Disable a domain without deleting it."""
        return self._toggle(domain_record, active=False)

    def deprovision(self, domain_record: EmailDomain) -> OperationResult:
        """Remove the domain from the mail server.

        Does not delete the Django EmailDomain row — caller decides that.
        """
        try:
            result = self._provider.delete_domain(domain_record.domain)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="domain.deprovision",
                resource_type="domain",
                resource_id=domain_record.domain,
                success=False,
                error="Provider error during delete_domain",
            )
            raise

        audit(
            account=self.account,
            actor=self.actor,
            action="domain.deprovision",
            resource_type="domain",
            resource_id=domain_record.domain,
        )
        return result

    def rotate_dkim(
        self,
        domain_record: EmailDomain,
        *,
        new_selector: str,
    ) -> DkimRecord:
        """Generate a new DKIM keypair under a new selector.

        Updates dkim_public_key and dkim_selector in the DB after success.
        The old selector continues to work until DNS TTL expires.
        """
        try:
            record = self._provider.rotate_dkim(
                domain_record.domain,
                new_selector=new_selector,
            )
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="domain.rotate_dkim",
                resource_type="domain",
                resource_id=domain_record.domain,
                success=False,
                error=f"Provider error rotating DKIM to selector {new_selector!r}",
            )
            raise

        domain_record.dkim_public_key = record.public_key_txt
        domain_record.dkim_selector = record.selector
        domain_record.save(update_fields=["dkim_public_key", "dkim_selector"])

        audit(
            account=self.account,
            actor=self.actor,
            action="domain.rotate_dkim",
            resource_type="domain",
            resource_id=domain_record.domain,
            metadata={"old_selector": domain_record.dkim_selector, "new_selector": new_selector},
        )
        return record

    # ── Private helpers ───────────────────────────────────────────────────

    def _sync_dns_records(self, domain_record: EmailDomain) -> None:
        """Materialise the full DNS spec (verify TXT, 3 DKIM CNAMEs, SPF, DMARC)
        as EmailDnsRecord rows for a multi-record-DKIM provider (SES)."""
        from apps.email.models import EmailDnsRecord

        dkim_records = self._provider.get_dkim_records(domain_record.domain)

        if domain_record.ensure_verification_token():
            domain_record.save(
                update_fields=["verify_record_name", "verify_record_value"]
            )

        wanted: list[tuple[str, str, str, str]] = [
            (
                EmailDnsRecord.Key.VERIFY,
                EmailDnsRecord.RecordType.TXT,
                domain_record.verify_record_name or domain_record.domain,
                domain_record.verify_record_value,
            ),
        ]
        for rec in dkim_records:
            wanted.append(
                (
                    EmailDnsRecord.Key.DKIM,
                    EmailDnsRecord.RecordType.CNAME,
                    rec.record_name,
                    rec.public_key_txt,
                )
            )
        wanted.append(
            (
                EmailDnsRecord.Key.SPF,
                EmailDnsRecord.RecordType.TXT,
                domain_record.domain,
                domain_record.spf_value,
            )
        )
        wanted.append(
            (
                EmailDnsRecord.Key.DMARC,
                EmailDnsRecord.RecordType.TXT,
                domain_record.dmarc_record_name,
                domain_record.dmarc_value,
            )
        )

        for key, rtype, name, value in wanted:
            EmailDnsRecord.objects.update_or_create(
                domain=domain_record,
                key=key,
                name=name,
                defaults={"record_type": rtype, "value": value},
            )

    def _toggle(
        self, domain_record: EmailDomain, *, active: bool
    ) -> OperationResult:
        action = "domain.enable" if active else "domain.disable"
        try:
            result = self._provider.set_domain_active(domain_record.domain, active=active)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action=action,
                resource_type="domain",
                resource_id=domain_record.domain,
                success=False,
                error="Provider error during set_domain_active",
            )
            raise

        domain_record.is_active = active
        domain_record.save(update_fields=["is_active"])

        audit(
            account=self.account,
            actor=self.actor,
            action=action,
            resource_type="domain",
            resource_id=domain_record.domain,
        )
        return result
