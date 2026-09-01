"""AWS SES event webhooks via SNS.

Handles bounce/complaint/delivery notifications from SES via SNS. Verifies SNS
message signatures and updates the suppression list / email message status.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

def _get_allowed_sns_topic_arns() -> set[str]:
    """Load allowed SNS topic ARNs from MailProviderSettings or env var.

    MailProviderSettings takes precedence; env var is the fallback.
    Returns a set of ARNs, or empty set if not configured.
    """
    import os
    from django.db import DEFAULT_DB_ALIAS, connections

    # Try DB first (MailProviderSettings.ses_sns_topic_arn)
    try:
        # Check if DB is ready (avoid errors during migrations)
        if connections[DEFAULT_DB_ALIAS].ensure_connection() is not None:
            from apps.core.models import MailProviderSettings
            settings_obj = MailProviderSettings.load()
            if settings_obj.ses_sns_topic_arn:
                return {settings_obj.ses_sns_topic_arn}
    except Exception as e:
        logger.debug("Failed to load MailProviderSettings (DB may not be ready): %s", e)

    # Fallback to environment variable
    env_arn = os.getenv("SES_SNS_TOPIC_ARN", "").strip()
    return {env_arn} if env_arn else set()


def _get_sns_topic_arn_if_allowed(topic_arn: str | None) -> bool:
    """Check if a topic ARN is allowed."""
    if not topic_arn:
        return False
    allowed = _get_allowed_sns_topic_arns()
    return topic_arn in allowed

# AWS SNS signing certs and SubscribeURLs are always hosted on a
# sns.<region>.amazonaws.com (or .amazonaws.com.cn) host.
_SNS_HOST_RE = re.compile(
    r"^sns\.[a-z0-9-]{3,}\.amazonaws\.com(\.cn)?$",
    re.IGNORECASE,
)

# SNS signing certificates use a fixed path pattern.
_SNS_CERT_PATH_RE = re.compile(
    r"^/SimpleNotificationService-[a-f0-9]+\.pem$",
    re.IGNORECASE,
)

# How long to remember processed SNS MessageIds (idempotency). SNS can retry
# for hours; 7 days is a safe window for most setups.
_IDEMPOTENCY_TTL_SECONDS = 7 * 24 * 3600


def _is_valid_sns_url(url: str, *, require_cert_path: bool = False) -> bool:
    """Validate that a URL is an HTTPS SNS endpoint we are willing to fetch."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme != "https":
        return False

    hostname = (parsed.hostname or "").lower()
    if not _SNS_HOST_RE.match(hostname):
        return False

    if require_cert_path:
        path = parsed.path or ""
        if not _SNS_CERT_PATH_RE.match(path):
            return False

    return True


def _verify_sns_signature(message: dict[str, Any]) -> bool:
    """Verify SNS message signature (SignatureVersion 1 = SHA1, 2 = SHA256)."""
    try:
        signature = message.get("Signature", "")
        cert_url = message.get("SigningCertURL") or message.get("SigningCertUrl") or ""

        if not signature or not cert_url:
            logger.warning("SNS message missing Signature or SigningCertURL")
            return False

        if not _is_valid_sns_url(cert_url, require_cert_path=True):
            logger.warning("Rejecting SigningCertURL with untrusted host/path: %s", cert_url)
            return False

        signature_version = str(message.get("SignatureVersion", "1"))
        if signature_version not in ("1", "2"):
            logger.warning("Unsupported SNS SignatureVersion: %s", signature_version)
            return False

        hash_algo = hashes.SHA1() if signature_version == "1" else hashes.SHA256()

        cache_key = f"sns_cert_{hashlib.sha256(cert_url.encode()).hexdigest()}"
        cert_content = cache.get(cache_key)

        if not cert_content:
            try:
                cert_response = requests.get(
                    cert_url,
                    timeout=5,
                    allow_redirects=False,
                )
                cert_response.raise_for_status()
                cert_content = cert_response.text
                cache.set(cache_key, cert_content, 86400)
            except Exception as e:
                logger.error("Failed to fetch SNS signing certificate: %s", e)
                return False

        msg_type = message.get("Type")
        if msg_type == "Notification":
            fields_to_sign = [
                "Message",
                "MessageId",
                "Subject",
                "Timestamp",
                "TopicArn",
                "Type",
            ]
        else:
            fields_to_sign = [
                "Message",
                "MessageId",
                "SubscribeURL",
                "Timestamp",
                "Token",
                "TopicArn",
                "Type",
            ]

        string_to_sign = "".join(
            f"{field}\n{message[field]}\n"
            for field in fields_to_sign
            if field in message
        )

        try:
            cert_obj = x509.load_pem_x509_certificate(
                cert_content.encode("utf-8"),
                default_backend(),
            )
            public_key = cert_obj.public_key()
            signature_bytes = base64.b64decode(signature)

            public_key.verify(
                signature_bytes,
                string_to_sign.encode("utf-8"),
                asym_padding.PKCS1v15(),
                hash_algo,
            )
            return True
        except InvalidSignature:
            logger.warning("SNS signature verification failed: signature mismatch")
            return False
        except Exception as e:
            logger.warning("SNS signature verification failed: %s", e)
            return False
    except Exception as e:
        logger.error("Unexpected error during SNS signature verification: %s", e)
        return False


def _already_processed(sns_message_id: str) -> bool:
    """Return True if this SNS MessageId was already handled (cache-backed)."""
    if not sns_message_id:
        return False
    key = f"sns_processed_{sns_message_id}"
    return cache.get(key) is not None


def _mark_processed(sns_message_id: str) -> None:
    if not sns_message_id:
        return
    key = f"sns_processed_{sns_message_id}"
    cache.set(key, "1", _IDEMPOTENCY_TTL_SECONDS)


@csrf_exempt
@require_POST
def ses_sns_webhook(request):
    """Handle SNS messages from SES (bounces, complaints, deliveries)."""
    try:
        message_data = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in SNS webhook")
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not _verify_sns_signature(message_data):
        logger.warning("SNS signature verification failed")
        return JsonResponse({"error": "Signature verification failed"}, status=403)

    topic_arn = message_data.get("TopicArn")
    if not _get_sns_topic_arn_if_allowed(topic_arn):
        if not topic_arn:
            logger.warning("SNS message missing TopicArn")
        else:
            allowed = _get_allowed_sns_topic_arns()
            if not allowed:
                logger.error("No SNS topic ARNs configured in MailProviderSettings or SES_SNS_TOPIC_ARN env var")
            else:
                logger.warning("Rejecting unexpected SNS TopicArn: %s (allowed: %s)", topic_arn, allowed)
        return JsonResponse({"error": "Unexpected or unconfigured TopicArn"}, status=403)

    sns_message_id = message_data.get("MessageId") or ""

    # Subscription confirmation (no SES payload; still verify + confirm once)
    if message_data.get("Type") == "SubscriptionConfirmation":
        subscribe_url = message_data.get("SubscribeURL")
        if not subscribe_url or not _is_valid_sns_url(subscribe_url):
            logger.warning("Rejecting SubscribeURL with untrusted host: %s", subscribe_url)
            return JsonResponse({"error": "Invalid SubscribeURL"}, status=400)

        if _already_processed(sns_message_id):
            logger.info("Duplicate SubscriptionConfirmation MessageId=%s; ignoring", sns_message_id)
            return HttpResponse("OK")

        logger.info("SNS SubscriptionConfirmation: %s", subscribe_url)
        try:
            resp = requests.get(subscribe_url, timeout=5, allow_redirects=False)
            resp.raise_for_status()
            _mark_processed(sns_message_id)
            logger.info("Successfully confirmed SNS subscription")
        except Exception as e:
            logger.error("Failed to confirm SNS subscription: %s", e)
            # Do not mark processed so SNS can retry confirmation.
        return HttpResponse("OK")

    if message_data.get("Type") != "Notification":
        logger.warning("Unknown SNS message type: %s", message_data.get("Type"))
        return HttpResponse("OK")

    # Durable-enough idempotency for notifications (cache; see note below for DB)
    if _already_processed(sns_message_id):
        logger.info("Duplicate SNS Notification MessageId=%s; acknowledging", sns_message_id)
        return HttpResponse("OK")

    try:
        ses_message = json.loads(message_data.get("Message", "{}"))
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in SNS Message field")
        # Acknowledge so SNS does not retry forever on malformed payload.
        _mark_processed(sns_message_id)
        return HttpResponse("OK")

    event_type = ses_message.get("eventType")

    try:
        if event_type == "Bounce":
            _handle_bounce(ses_message)
        elif event_type == "Complaint":
            _handle_complaint(ses_message)
        elif event_type == "Delivery":
            _handle_delivery(ses_message)
        else:
            logger.debug("Ignoring SES event type: %s", event_type)
    except Exception:
        # Let SNS retry on unexpected handler failures; do not mark processed.
        logger.exception("Error handling SES event type=%s", event_type)
        return JsonResponse({"error": "Processing failed"}, status=500)

    _mark_processed(sns_message_id)
    return HttpResponse("OK")


def _handle_bounce(ses_message: dict[str, Any]) -> None:
    from apps.email.models import SuppressionListEntry

    bounce = ses_message.get("bounce", {})
    bounce_type = bounce.get("bounceType", "Undetermined")
    recipients = bounce.get("bouncedRecipients", [])

    if bounce_type != "Permanent":
        logger.debug("Ignoring %s bounce", bounce_type)
        return

    message_id = ses_message.get("mail", {}).get("messageId")
    account = _find_account_for_message(message_id)
    if not account:
        # Acknowledge at the webhook layer; we cannot suppress without an account.
        logger.warning(
            "No EmailMessage for SES messageId=%s; cannot suppress bounce recipients",
            message_id,
        )
        return

    for recipient in recipients:
        email = recipient.get("emailAddress")
        if not email:
            continue

        SuppressionListEntry.objects.get_or_create(
            account=account,
            email=email,
            defaults={
                "reason": SuppressionListEntry.Reason.BOUNCE,
                "bounce_type": bounce_type,
            },
        )
        logger.info("Added %s to suppression list (bounce)", email)


def _handle_complaint(ses_message: dict[str, Any]) -> None:
    from apps.email.models import SuppressionListEntry

    complaint = ses_message.get("complaint", {})
    recipients = complaint.get("complainedRecipients", [])

    message_id = ses_message.get("mail", {}).get("messageId")
    account = _find_account_for_message(message_id)
    if not account:
        logger.warning(
            "No EmailMessage for SES messageId=%s; cannot suppress complaint recipients",
            message_id,
        )
        return

    for recipient in recipients:
        email = recipient.get("emailAddress")
        if not email:
            continue

        SuppressionListEntry.objects.get_or_create(
            account=account,
            email=email,
            defaults={
                "reason": SuppressionListEntry.Reason.COMPLAINT,
            },
        )
        logger.info("Added %s to suppression list (complaint)", email)


def _handle_delivery(ses_message: dict[str, Any]) -> None:
    """Mark the corresponding EmailMessage as delivered when possible."""
    from apps.email.models import EmailMessage

    mail = ses_message.get("mail", {})
    message_id = mail.get("messageId")
    destination = mail.get("destination", [])

    if not message_id:
        logger.warning("Delivery notification missing mail.messageId")
        return

    updated = EmailMessage.objects.filter(provider_message_id=message_id).update(
        # Adjust field/status names to match your model.
        status=getattr(EmailMessage, "Status", type("S", (), {"DELIVERED": "delivered"})).DELIVERED
        if hasattr(EmailMessage, "Status")
        else "delivered",
    )
    if updated:
        logger.info("Marked EmailMessage provider_message_id=%s as delivered", message_id)
    else:
        logger.warning(
            "No EmailMessage for SES messageId=%s (delivery to %s); acknowledging without update",
            message_id,
            destination,
        )


def _find_account_for_message(message_id: str | None):
    if not message_id:
        return None

    from apps.email.models import EmailMessage

    try:
        msg = EmailMessage.objects.get(provider_message_id=message_id)
        return msg.account
    except EmailMessage.DoesNotExist:
        return None