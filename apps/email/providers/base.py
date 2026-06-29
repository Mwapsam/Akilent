"""Provider-agnostic interface for mail-server infrastructure operations.

All SaaS business logic lives in Django. Stalwart (or any future replacement)
is accessed only through this interface — no provider-specific code in views,
tasks, or services.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class MailProviderError(Exception):
    """Raised for any recoverable or user-visible mail provider failure.

    Replaces IRedMailError at all call sites so views and tasks remain
    provider-agnostic.
    """


@dataclass
class DkimResult:
    selector: str
    dkim_txt: str  # full TXT value ready for DNS (e.g. "v=DKIM1; k=rsa; p=...")


@dataclass
class ProvisionResult:
    dkim: DkimResult


class MailProvider(ABC):
    """Abstract mail provider — domain/mailbox/alias lifecycle management."""

    # --- Domain lifecycle ---

    @abstractmethod
    def provision_domain(self, domain: str, selector: str = "dkim") -> ProvisionResult:
        """Create the domain on the mail server and generate a DKIM keypair.

        Returns a ProvisionResult with the DKIM public-key TXT value so Django
        can display it to the tenant for DNS. The private key stays on the server.
        """

    @abstractmethod
    def delete_domain(self, domain: str) -> None:
        """Remove a domain and all associated configuration from the mail server."""

    @abstractmethod
    def set_domain_active(self, domain: str, active: bool) -> None:
        """Enable or disable a domain on the mail server."""

    # --- Mailbox lifecycle ---

    @abstractmethod
    def create_mailbox(
        self,
        email: str,
        password: str,
        name: str = "",
        quota_mb: int | None = None,
    ) -> None:
        """Provision a mailbox. The password is used once and never stored."""

    @abstractmethod
    def delete_mailbox(self, email: str) -> None:
        """Remove a mailbox from the mail server."""

    @abstractmethod
    def change_password(self, email: str, password: str) -> None:
        """Update the password for an existing mailbox."""

    @abstractmethod
    def set_quota(self, email: str, quota_mb: int) -> None:
        """Adjust the storage quota for an existing mailbox."""

    # --- Alias lifecycle ---

    @abstractmethod
    def create_alias(self, address: str, goto: str) -> None:
        """Create a forwarding alias from `address` to `goto`."""
