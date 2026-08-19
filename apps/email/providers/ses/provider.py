"""AWS SES mail infrastructure provider — manages domain identities and DKIM.

This provider uses AWS SES EmailIdentity API to manage domain verification and
DKIM setup. Unlike Stalwart which uses TXT records, SES uses CNAME records for
Easy DKIM.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

from apps.email.exceptions import EmailProviderError
from apps.email.types import (
    DkimRecord,
    DomainInfo,
    OperationResult,
)

from ..base import EmailProvider

if TYPE_CHECKING:
    from mypy_boto3_sesv2 import SESv2Client

logger = logging.getLogger(__name__)


class SesProvider(EmailProvider):
    """Mail provider using AWS SES email identity and Easy DKIM."""

    def __init__(self):
        """Initialize SES client with credentials from environment/IAM role."""
        region = os.getenv("AWS_REGION", "us-east-1")
        self.client: SESv2Client = boto3.client("sesv2", region_name=region)

    # ── Domain management ──────────────────────────────────────────────────────

    def create_domain(self, domain: str) -> OperationResult:
        """Create a new email identity for a domain in SES.

        SES doesn't create mailboxes, so this is really just registering the
        domain as a sending identity. DKIM is enabled by default with Easy DKIM.
        """
        try:
            # Create the email identity (domain-level)
            self.client.create_email_identity(
                EmailAddress=f"noreply@{domain}",  # Placeholder; SES treats it as domain identity
                Tags=[
                    {"Name": "Source", "Value": "Automator"},
                ],
            )
            logger.info("SES domain identity created: %s", domain)
            return OperationResult(success=True)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "AlreadyExistsException":
                logger.info("SES domain identity already exists: %s", domain)
                return OperationResult(success=True)
            logger.error("Failed to create SES domain identity: %s", exc)
            raise EmailProviderError(f"Failed to create domain identity: {exc}") from exc
        except Exception as exc:
            logger.error("Unexpected error creating SES domain identity: %s", exc)
            raise EmailProviderError(str(exc)) from exc

    def verify_domain(self, domain: str) -> OperationResult:
        """SES doesn't require explicit verification; identity is auto-verified upon creation.

        However, DKIM must be verified via DNS. This returns success to match the interface.
        """
        return OperationResult(success=True)

    def get_dkim(self, domain: str, *, selector: str = "dkim") -> DkimRecord | None:
        """Fetch DKIM record details from SES for Easy DKIM.

        SES provides CNAME records (not TXT like Stalwart). Returns None if not found.
        """
        try:
            response = self.client.get_email_identity(EmailAddress=domain)
            identity_attrs = response.get("Attributes", {})

            # Easy DKIM tokens from SES
            dkim_tokens = identity_attrs.get("DkimTokens", [])
            if not dkim_tokens:
                logger.warning("No DKIM tokens found for SES domain: %s", domain)
                return None

            # SES uses CNAME format: token.dkim.amazonses.com -> token.dkim.amazonses.com (CNAME)
            # Construct the record name and value for the first token
            token = dkim_tokens[0]
            cname_name = f"{token}._domainkey.{domain}"
            cname_value = f"{token}.dkim.amazonses.com"

            logger.info("SES DKIM record for %s: %s -> %s", domain, cname_name, cname_value)

            return DkimRecord(
                selector="amazonses",  # SES-specific selector
                public_key=cname_value,  # Store CNAME target as "public_key" field
                is_cname=True,  # Flag to indicate this is a CNAME, not TXT
            )

        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NotFoundException":
                logger.warning("SES domain not found: %s", domain)
                return None
            logger.error("Failed to fetch SES DKIM: %s", exc)
            raise EmailProviderError(f"Failed to fetch DKIM: {exc}") from exc
        except Exception as exc:
            logger.error("Unexpected error fetching SES DKIM: %s", exc)
            raise EmailProviderError(str(exc)) from exc

    def delete_domain(self, domain: str) -> OperationResult:
        """Delete an email identity from SES."""
        try:
            self.client.delete_email_identity(EmailAddress=domain)
            logger.info("SES domain identity deleted: %s", domain)
            return OperationResult(success=True)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NotFoundException":
                logger.info("SES domain not found (already deleted): %s", domain)
                return OperationResult(success=True)
            logger.error("Failed to delete SES domain: %s", exc)
            raise EmailProviderError(f"Failed to delete domain: {exc}") from exc
        except Exception as exc:
            logger.error("Unexpected error deleting SES domain: %s", exc)
            raise EmailProviderError(str(exc)) from exc

    # ── Unsupported operations (mailbox/alias/relay) ────────────────────────────
    # These are no longer part of the EmailProvider interface, so they're removed.
    # See apps.email.models.EmailDomain.dns_records() for how to handle CNAME vs TXT display.
