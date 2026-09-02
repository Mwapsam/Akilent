"""AWS SES mail infrastructure provider — manages domain identities and DKIM.

This provider uses AWS SES EmailIdentity API to manage domain verification and
DKIM setup. Unlike providers that use TXT records, SES Easy DKIM uses CNAME
records of the form:

    {token}._domainkey.{domain}  CNAME  {token}.dkim.amazonses.com
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
    OperationResult,
)

from ..base import EmailProvider

if TYPE_CHECKING:
    from mypy_boto3_sesv2 import SESv2Client

logger = logging.getLogger(__name__)


class SesProvider(EmailProvider):
    """Mail provider using AWS SES email identity and Easy DKIM."""

    def __init__(self) -> None:
        """Initialize SES client with credentials from environment/IAM role."""
        from apps.core.models import MailProviderSettings

        region = "us-east-1"
        try:
            settings = MailProviderSettings.load()
            region = settings.aws_region or os.getenv("AWS_REGION", "us-east-1")
        except Exception:
            logger.debug("Failed to load MailProviderSettings; falling back to env/defaults")
            region = os.getenv("AWS_REGION", "us-east-1")

        self.client: SESv2Client = boto3.client("sesv2", region_name=region)

    # ── Domain management ──────────────────────────────────────────────────────

    def create_domain(self, domain: str) -> OperationResult:
        """Register a domain as a sending identity in SES (Easy DKIM enabled)."""
        try:
            # EmailIdentity must be the bare domain, not an address like
            # "noreply@domain". Address identities use email confirmation,
            # not DNS/DKIM.
            self.client.create_email_identity(
                EmailIdentity=domain,
                Tags=[{"Key": "Source", "Value": "Automator"}],
            )
            logger.info("SES domain identity created: %s", domain)
            return OperationResult(success=True)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "AlreadyExistsException":
                logger.info("SES domain identity already exists: %s", domain)
                return OperationResult(success=True)
            logger.exception("Failed to create SES domain identity: %s", domain)
            raise EmailProviderError(
                f"Failed to create domain identity: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error creating SES domain identity: %s", domain)
            raise EmailProviderError(str(exc)) from exc

    def verify_domain(self, domain: str) -> OperationResult:
        """Return success when SES reports VerificationStatus == SUCCESS."""
        try:
            response = self.client.get_email_identity(EmailIdentity=domain)
            # SESv2: VerificationStatus is top-level (not under Attributes).
            status = response.get("VerificationStatus")

            if status == "SUCCESS":
                return OperationResult(success=True)

            logger.info("SES domain verification status for %s: %s", domain, status)
            return OperationResult(success=False)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NotFoundException":
                logger.info("SES domain not found for verification: %s", domain)
                return OperationResult(success=False)
            logger.exception("Failed to verify SES domain: %s", domain)
            raise EmailProviderError(f"Failed to verify domain: {exc}") from exc
        except Exception as exc:
            logger.exception("Unexpected error verifying SES domain: %s", domain)
            raise EmailProviderError(str(exc)) from exc

    def get_dkim_records(self, domain: str) -> list[DkimRecord]:
        try:
            response = self.client.get_email_identity(EmailIdentity=domain)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NotFoundException":
                logger.info("SES identity not found for domain=%s", domain)
                return []
            logger.exception("SES get_email_identity failed for domain=%s", domain)
            raise EmailProviderError(
                f"Failed to get DKIM records for {domain}: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error getting DKIM records for %s", domain)
            raise EmailProviderError(str(exc)) from exc

        tokens = (response.get("DkimAttributes") or {}).get("Tokens") or []
        records: list[DkimRecord] = []
        for token in tokens:
            cname_name = f"{token}._domainkey.{domain}"
            cname_value = f"{token}.dkim.amazonses.com"
            # selector = token so callers that build
            # f"{selector}._domainkey.{domain}" get the correct name.
            # Include name= only if DkimRecord defines that field.
            records.append(
                DkimRecord(
                    selector=token,
                    public_key_txt=cname_value,
                    is_cname=True,
                    # Uncomment if your DkimRecord supports it:
                    # name=cname_name,
                )
            )
            logger.debug(
                "SES DKIM CNAME for %s: %s -> %s", domain, cname_name, cname_value
            )
        return records

    def get_dkim(self, domain: str, selector: str | None = None) -> DkimRecord | None:
        """Compatibility helper — prefer get_dkim_records() and publish all three.

        ``selector`` is ignored for Easy DKIM (SES assigns the tokens).
        """
        if selector is not None:
            logger.warning(
                "SesProvider.get_dkim(selector=...) is ignored; "
                "Easy DKIM selectors are SES-assigned tokens. "
                "Use get_dkim_records() and publish all three CNAMEs."
            )
        records = self.get_dkim_records(domain)
        if not records:
            return None
        if len(records) > 1:
            logger.warning(
                "SesProvider.get_dkim() returning only the first of %d DKIM "
                "records for domain=%s; callers must use get_dkim_records()",
                len(records),
                domain,
            )
        return records[0]

    def delete_domain(self, domain: str) -> OperationResult:
        """Delete an email identity from SES (idempotent if already gone)."""
        try:
            self.client.delete_email_identity(EmailIdentity=domain)
            logger.info("SES domain identity deleted: %s", domain)
            return OperationResult(success=True)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NotFoundException":
                logger.info("SES domain not found (already deleted): %s", domain)
                return OperationResult(success=True)
            logger.exception("Failed to delete SES domain: %s", domain)
            raise EmailProviderError(f"Failed to delete domain: {exc}") from exc
        except Exception as exc:
            logger.exception("Unexpected error deleting SES domain: %s", domain)
            raise EmailProviderError(str(exc)) from exc