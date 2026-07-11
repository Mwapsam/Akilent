"""Provider-agnostic EmailProvider interface.

Any mail server backend (Stalwart, Mailcow, Google Workspace, Exchange, ...)
is supported by implementing this ABC. Business logic imports only from here
and from apps.email.types — never from a concrete provider module.

Design principle: every method returns a typed dataclass from apps.email.types,
never a raw dict. Adapters belong in the provider, not scattered across the
service layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from apps.email.exceptions import EmailProviderError
from apps.email.types import (
    AliasInfo,
    DkimRecord,
    DomainInfo,
    MailboxInfo,
    OperationResult,
    QuotaInfo,
)


# ── Backwards-compatible shims ────────────────────────────────────────────────
# Existing code imported MailProviderError, DkimResult, ProvisionResult from
# here. Keeping them so no view or task import breaks during the migration.

MailProviderError = EmailProviderError


@dataclass
class DkimResult:
    """Legacy wrapper — new code should use DkimRecord from apps.email.types."""

    selector: str
    dkim_txt: str   # full TXT value ready for DNS: "v=DKIM1; k=rsa; p=..."


@dataclass
class ProvisionResult:
    """Legacy wrapper — new code should use DomainInfo from apps.email.types."""

    dkim: DkimResult


# ── Abstract interface ────────────────────────────────────────────────────────


class EmailProvider(ABC):
    """Abstract interface for a mail server infrastructure backend.

    Implement all abstract methods to add a new provider. The factory in
    apps.email.providers.__init__ resolves the concrete class at runtime via
    the MAIL_PROVIDER_BACKEND Django setting.

    Threading: instances are not thread-safe. Instantiate one per request,
    per Celery task, or per service call.
    """

    # ── Domain lifecycle ──────────────────────────────────────────────────

    @abstractmethod
    def create_domain(
        self,
        domain: str,
        *,
        max_accounts: int | None = None,
        disk_quota_mb: int | None = None,
        description: str = "",
    ) -> DomainInfo:
        """Provision a new domain and generate its DKIM keypair.

        Returns DomainInfo with .dkim populated so the caller can display
        the DNS TXT value to the tenant immediately after provisioning.
        The DKIM private key stays on the mail server.
        """

    @abstractmethod
    def get_domain(self, domain: str) -> DomainInfo:
        """Fetch current metadata for an existing domain."""

    @abstractmethod
    def update_domain(
        self,
        domain: str,
        *,
        max_accounts: int | None = None,
        disk_quota_mb: int | None = None,
        description: str | None = None,
    ) -> DomainInfo:
        """Update mutable domain settings."""

    @abstractmethod
    def delete_domain(self, domain: str) -> OperationResult:
        """Permanently remove a domain and all its accounts/aliases/DKIM keys."""

    @abstractmethod
    def list_domains(self) -> list[DomainInfo]:
        """Return all domains configured on the mail server."""

    @abstractmethod
    def set_domain_active(self, domain: str, *, active: bool) -> OperationResult:
        """Enable or disable a domain without deleting it."""

    # ── DKIM management ───────────────────────────────────────────────────

    @abstractmethod
    def provision_dkim(
        self,
        domain: str,
        *,
        selector: str = "dkim",
        algorithm: str = "rsa-sha256",
    ) -> DkimRecord:
        """Generate a DKIM keypair for the domain on the mail server.

        The private key stays on the server. Returns the public-key TXT record
        for storage in Django and display to the tenant.
        """

    @abstractmethod
    def get_dkim(self, domain: str, *, selector: str = "dkim") -> DkimRecord:
        """Retrieve the current DKIM public-key TXT record."""

    @abstractmethod
    def rotate_dkim(
        self,
        domain: str,
        *,
        new_selector: str,
        algorithm: str = "rsa-sha256",
    ) -> DkimRecord:
        """Generate a new DKIM keypair under a different selector.

        The old selector remains valid during DNS propagation. Callers should
        schedule old-key deletion after confirming the new record is live in DNS.
        """

    # ── Mailbox lifecycle ─────────────────────────────────────────────────

    @abstractmethod
    def create_mailbox(
        self,
        email: str,
        password: str,
        *,
        name: str = "",
        quota_mb: int | None = None,
        description: str = "",
    ) -> MailboxInfo:
        """Provision a mailbox. The password is hashed by the server; Django never stores it."""

    @abstractmethod
    def get_mailbox(self, email: str) -> MailboxInfo:
        """Fetch current metadata for an existing mailbox."""

    @abstractmethod
    def update_mailbox(
        self,
        email: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> MailboxInfo:
        """Update mutable mailbox fields (excludes password and quota)."""

    @abstractmethod
    def delete_mailbox(self, email: str) -> OperationResult:
        """Permanently remove a mailbox and all its stored mail."""

    @abstractmethod
    def list_mailboxes(self, domain: str) -> list[MailboxInfo]:
        """Return all mailboxes for the given domain."""

    @abstractmethod
    def set_mailbox_active(self, email: str, *, active: bool) -> OperationResult:
        """Suspend or reactivate a mailbox without deleting it."""

    # ── Password management ───────────────────────────────────────────────

    @abstractmethod
    def change_password(self, email: str, new_password: str) -> OperationResult:
        """Change a mailbox password. Plaintext is sent once; not retained by Django."""

    # ── Quota management ──────────────────────────────────────────────────

    @abstractmethod
    def get_quota(self, email: str) -> QuotaInfo:
        """Return current storage usage and limit for a mailbox."""

    @abstractmethod
    def set_quota(self, email: str, quota_mb: int) -> OperationResult:
        """Update the storage quota for a mailbox (0 = unlimited)."""

    # ── Alias lifecycle ───────────────────────────────────────────────────

    @abstractmethod
    def create_alias(
        self,
        address: str,
        targets: list[str],
        *,
        description: str = "",
    ) -> AliasInfo:
        """Create a forwarding alias from ``address`` to one or more ``targets``."""

    @abstractmethod
    def get_alias(self, address: str) -> AliasInfo:
        """Fetch the current target list for an alias."""

    @abstractmethod
    def update_alias(
        self,
        address: str,
        targets: list[str],
        *,
        description: str | None = None,
    ) -> AliasInfo:
        """Replace the target list for an existing alias."""

    @abstractmethod
    def delete_alias(self, address: str) -> OperationResult:
        """Remove a forwarding alias."""

    @abstractmethod
    def list_aliases(self, domain: str) -> list[AliasInfo]:
        """Return all aliases in the given domain."""

    # ── SMTP relay identity ───────────────────────────────────────────────
    # A per-tenant SMTP AUTH identity is just a dedicated mailbox-like
    # account under the tenant's domain — mail servers don't expose a
    # distinct "API relay" primitive, so this is built entirely on the
    # mailbox lifecycle above. Concrete (not abstract): any provider that
    # implements create_mailbox/change_password/delete_mailbox gets SMTP
    # relay identities for free, with no per-provider override needed.

    def create_relay_identity(
        self, email: str, password: str, *, description: str = ""
    ) -> MailboxInfo:
        """Provision a dedicated SMTP AUTH identity (not a real inbox) for API relay."""
        return self.create_mailbox(
            email,
            password,
            name="API relay",
            description=description or "Akilent SMTP relay identity",
        )

    def rotate_relay_secret(self, email: str, new_password: str) -> OperationResult:
        """Rotate the SMTP AUTH password for a relay identity."""
        return self.change_password(email, new_password)

    def delete_relay_identity(self, email: str) -> OperationResult:
        """Permanently remove a relay identity."""
        return self.delete_mailbox(email)

    # ── Legacy compatibility helpers ──────────────────────────────────────
    # Concrete implementations of the old 8-method interface so existing
    # views and tasks continue to work unchanged while the migration proceeds.

    def provision_domain(
        self, domain: str, selector: str = "dkim"
    ) -> ProvisionResult:
        """Legacy shim: wraps create_domain() in the old ProvisionResult shape."""
        info = self.create_domain(domain)
        dkim = info.dkim
        if dkim is None:
            dkim = self.provision_dkim(domain, selector=selector)
        return ProvisionResult(
            dkim=DkimResult(selector=dkim.selector, dkim_txt=dkim.public_key_txt)
        )


# Alias so old imports of MailProvider still resolve.
MailProvider = EmailProvider
