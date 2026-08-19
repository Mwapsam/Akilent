"""AWS SES send provider — delivers messages via boto3 SES client.

This implementation sends through AWS Simple Email Service (SES) API,
configured to use a configuration set for tracking bounces/complaints.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

from apps.email.exceptions import EmailProviderError
from apps.email.types import OutboundEmail, SendResult

from ..send_base import EmailSendProvider

if TYPE_CHECKING:
    from mypy_boto3_sesv2 import SESv2Client

logger = logging.getLogger(__name__)


class SesSendProvider(EmailSendProvider):
    """Send provider using AWS SES v2 API."""

    def __init__(self):
        """Initialize SES client with credentials from environment/IAM role."""
        region = os.getenv("AWS_REGION", "us-east-1")
        self.client: SESv2Client = boto3.client("sesv2", region_name=region)

        # Configuration set for tracking (optional but recommended)
        from apps.core.models import MailProviderSettings
        try:
            settings = MailProviderSettings.load()
            self.configuration_set = settings.ses_configuration_set or None
        except Exception:
            self.configuration_set = None

    def send(self, message: OutboundEmail) -> SendResult:
        """Deliver `message` via AWS SES. Raises EmailProviderError on failure.

        Args:
            message: OutboundEmail with from_email, to_email, subject, text_body, html_body

        Returns:
            SendResult with success=True and provider_message_id from SES

        Raises:
            EmailProviderError: On unrecoverable boto3 errors
        """
        try:
            params = {
                "FromEmailAddress": message.from_email,
                "Destination": {
                    "ToAddresses": [message.to_email],
                },
                "Content": {
                    "Simple": {
                        "Subject": {
                            "Data": message.subject,
                            "Charset": "UTF-8",
                        },
                    }
                },
            }

            # Add body parts (prefer HTML, fallback to text-only)
            if message.html_body:
                params["Content"]["Simple"]["Body"] = {
                    "Html": {
                        "Data": message.html_body,
                        "Charset": "UTF-8",
                    },
                }
                if message.text_body:
                    params["Content"]["Simple"]["Body"]["Text"] = {
                        "Data": message.text_body,
                        "Charset": "UTF-8",
                    }
            elif message.text_body:
                params["Content"]["Simple"]["Body"] = {
                    "Text": {
                        "Data": message.text_body,
                        "Charset": "UTF-8",
                    }
                }

            # Add configuration set if configured
            if self.configuration_set:
                params["ConfigurationSetName"] = self.configuration_set

            response = self.client.send_email(**params)
            message_id = response.get("MessageId")

            if not message_id:
                raise EmailProviderError("SES returned no MessageId")

            logger.info(
                "SES send successful: to=%s, message_id=%s",
                message.to_email,
                message_id,
            )
            return SendResult(success=True, provider_message_id=message_id)

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            error_msg = exc.response.get("Error", {}).get("Message", str(exc))

            # Determine if this is retryable
            retryable_codes = {"Throttling", "ServiceUnavailable"}
            if error_code in retryable_codes:
                logger.warning(
                    "SES send retryable error: %s - %s (to=%s)",
                    error_code,
                    error_msg,
                    message.to_email,
                )
            else:
                logger.error(
                    "SES send failed: %s - %s (to=%s)",
                    error_code,
                    error_msg,
                    message.to_email,
                )

            raise EmailProviderError(f"{error_code}: {error_msg}") from exc

        except Exception as exc:
            logger.error(
                "SES send unexpected error: %s (to=%s)",
                str(exc),
                message.to_email,
                exc_info=True,
            )
            raise EmailProviderError(str(exc)) from exc
