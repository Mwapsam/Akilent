"""No-op mail provider for local development and testing.

Set MAIL_PROVIDER_BACKEND=null in .env to run the full application without a
live mail server. All provisioning calls succeed silently; DKIM is a placeholder
value so domain creation completes and DNS records can be displayed.

Never use this in production — email will not actually be delivered.
"""

import logging

from .base import DkimResult, MailProvider, ProvisionResult

logger = logging.getLogger(__name__)

_PLACEHOLDER_DKIM = (
    "v=DKIM1; k=rsa; "
    "p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC_NULL_PROVIDER_PLACEHOLDER_QIDAQAB"
)


class NullProvider(MailProvider):
    """Implements MailProvider with no-op stubs. Safe to use in CI and local dev."""

    def provision_domain(self, domain: str, selector: str = "dkim") -> ProvisionResult:
        logger.debug("NullProvider.provision_domain(%s)", domain)
        return ProvisionResult(dkim=DkimResult(selector=selector, dkim_txt=_PLACEHOLDER_DKIM))

    def delete_domain(self, domain: str) -> None:
        logger.debug("NullProvider.delete_domain(%s)", domain)

    def set_domain_active(self, domain: str, active: bool) -> None:
        logger.debug("NullProvider.set_domain_active(%s, %s)", domain, active)

    def create_mailbox(
        self,
        email: str,
        password: str,
        name: str = "",
        quota_mb: int | None = None,
    ) -> None:
        logger.debug("NullProvider.create_mailbox(%s)", email)

    def delete_mailbox(self, email: str) -> None:
        logger.debug("NullProvider.delete_mailbox(%s)", email)

    def change_password(self, email: str, password: str) -> None:
        logger.debug("NullProvider.change_password(%s)", email)

    def set_quota(self, email: str, quota_mb: int) -> None:
        logger.debug("NullProvider.set_quota(%s, %s MB)", email, quota_mb)

    def create_alias(self, address: str, goto: str) -> None:
        logger.debug("NullProvider.create_alias(%s -> %s)", address, goto)
