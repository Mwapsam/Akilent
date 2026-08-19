"""AWS SES event webhooks via SNS.

Handles bounce/complaint/delivery notifications from SES via SNS. Verifies SNS
message signatures and updates the suppression list / email message status.
"""
import json
import logging
from typing import Any

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


def _verify_sns_signature(message: dict[str, Any]) -> bool:
    """Verify SNS message signature to prevent spoofing.

    Implements AWS SNS signature verification as per:
    https://docs.aws.amazon.com/sns/latest/dg/sns-verify-signature-of-message.html
    """
    try:
        import boto3
        from botocore.exceptions import ClientError

        # Extract signature fields from message
        signature = message.get("Signature", "")
        cert_url = message.get("SigningCertUrl", "")

        if not signature or not cert_url:
            logger.warning("SNS message missing Signature or SigningCertUrl")
            return False

        # Download and cache the certificate (in production, this should be cached)
        import requests
        try:
            cert_response = requests.get(cert_url, timeout=5)
            cert_response.raise_for_status()
            cert_content = cert_response.text
        except Exception as e:
            logger.error(f"Failed to fetch SNS signing certificate: {e}")
            return False

        # Build the string to sign (order matters)
        if message.get("Type") == "Notification":
            fields_to_sign = ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"]
        else:
            fields_to_sign = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]

        string_to_sign = "".join(
            f"{field}\n{message.get(field, '')}\n"
            for field in fields_to_sign
            if field in message
        )

        # Verify signature with certificate
        import ssl
        import hashlib
        import base64
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes, serialization

        try:
            # Load certificate
            cert_obj = x509.load_pem_x509_certificate(
                cert_content.encode(), default_backend()
            )
            public_key = cert_obj.public_key()

            # Decode signature
            signature_bytes = base64.b64decode(signature)

            # Verify
            public_key.verify(
                signature_bytes,
                string_to_sign.encode("utf-8"),
                padding=None,  # type: ignore
                algorithm=hashes.SHA256(),
            )
            return True
        except Exception as e:
            logger.warning(f"SNS signature verification failed: {e}")
            return False
    except ImportError:
        logger.error("cryptography library not installed")
        return False


@csrf_exempt
@require_POST
def ses_sns_webhook(request):
    """Handle SNS messages from SES (bounces, complaints, deliveries).

    AWS SNS sends a POST request with the JSON payload. This view verifies
    the signature, extracts event details, and updates the suppression list.
    """
    try:
        message_data = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in SNS webhook")
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # Verify SNS signature
    if not _verify_sns_signature(message_data):
        logger.warning("SNS signature verification failed")
        return JsonResponse({"error": "Signature verification failed"}, status=403)

    # Handle subscription confirmation
    if message_data.get("Type") == "SubscriptionConfirmation":
        logger.info(f"SNS SubscriptionConfirmation: {message_data.get('SubscribeURL')}")
        # In production, you would call SubscribeURL to confirm the subscription
        return HttpResponse("OK")

    # Handle notifications
    if message_data.get("Type") != "Notification":
        logger.warning(f"Unknown SNS message type: {message_data.get('Type')}")
        return HttpResponse("OK")

    # Parse the SES event from the SNS message
    try:
        ses_message = json.loads(message_data.get("Message", "{}"))
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in SNS Message field")
        return HttpResponse("OK")

    event_type = ses_message.get("eventType")

    if event_type == "Bounce":
        _handle_bounce(ses_message)
    elif event_type == "Complaint":
        _handle_complaint(ses_message)
    elif event_type == "Delivery":
        _handle_delivery(ses_message)
    else:
        logger.debug(f"Ignoring SES event type: {event_type}")

    return HttpResponse("OK")


def _handle_bounce(ses_message: dict[str, Any]) -> None:
    """Process SES bounce notification."""
    from apps.email.models import SuppressionListEntry

    bounce = ses_message.get("bounce", {})
    bounce_type = bounce.get("bounceType", "Permanent")  # Permanent, Transient, Undetermined
    recipients = bounce.get("bouncedRecipients", [])

    # Only suppress on Permanent bounces
    if bounce_type != "Permanent":
        logger.debug(f"Ignoring {bounce_type} bounce")
        return

    for recipient in recipients:
        email = recipient.get("emailAddress")
        if not email:
            continue

        # Find the account that sent this email (via SES message ID if available)
        message_id = ses_message.get("mail", {}).get("messageId")
        account = _find_account_for_message(message_id)

        if not account:
            logger.warning(f"Could not find account for bounced email {email}")
            continue

        # Create or update suppression entry
        SuppressionListEntry.objects.get_or_create(
            account=account,
            email=email,
            defaults={
                "reason": SuppressionListEntry.Reason.BOUNCE,
                "bounce_type": bounce_type,
            }
        )
        logger.info(f"Added {email} to suppression list (bounce)")


def _handle_complaint(ses_message: dict[str, Any]) -> None:
    """Process SES complaint notification."""
    from apps.email.models import SuppressionListEntry

    complaint = ses_message.get("complaint", {})
    recipients = complaint.get("complainedRecipients", [])

    for recipient in recipients:
        email = recipient.get("emailAddress")
        if not email:
            continue

        message_id = ses_message.get("mail", {}).get("messageId")
        account = _find_account_for_message(message_id)

        if not account:
            logger.warning(f"Could not find account for complained email {email}")
            continue

        # Create or update suppression entry
        SuppressionListEntry.objects.get_or_create(
            account=account,
            email=email,
            defaults={
                "reason": SuppressionListEntry.Reason.COMPLAINT,
            }
        )
        logger.info(f"Added {email} to suppression list (complaint)")


def _handle_delivery(ses_message: dict[str, Any]) -> None:
    """Process SES delivery notification (currently just logs)."""
    mail = ses_message.get("mail", {})
    message_id = mail.get("messageId")
    destination = mail.get("destination", [])
    logger.debug(f"SES delivery notification for {message_id}: {destination}")


def _find_account_for_message(message_id: str | None):
    """Look up the account that sent a message by its SES message ID."""
    if not message_id:
        return None

    from apps.email.models import EmailMessage

    try:
        msg = EmailMessage.objects.get(provider_message_id=message_id)
        return msg.account
    except EmailMessage.DoesNotExist:
        return None
