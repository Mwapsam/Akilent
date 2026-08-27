import logging
from datetime import datetime, timezone as dt_timezone

from celery import shared_task
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from apps.core.events import dispatcher, MessageReceived, MessageStatusChanged
from apps.whatsapp.models import (
    Conversation,
    MessageLog,
    OutboundMessage,
    WebhookEventLog,
    WhatsAppContact,
)
from apps.whatsapp.models.tenant import TenantResolutionError, get_account_for_webhook

logger = logging.getLogger(__name__)

_OUTBOUND_BATCH = 50
_MEDIA_BATCH = 20
_MAX_EVENT_ATTEMPTS = 3
_AUTOMATION_EVENTS_CACHE_KEY = "whatsapp_automation_events_enabled"
_AUTOMATION_EVENTS_CACHE_TTL = 60  # 60 second cache for SiteSettings flag


def _automation_events_enabled() -> bool:
    """Check if automation events are enabled, with short-lived caching.

    This caches the SiteSettings.automation_events_enabled flag for 60 seconds
    to avoid a database query on every webhook. The flag can be changed at runtime;
    the cache will refresh within the TTL.

    TODO(Phase 4): Replace with ModuleSubscription model check.
    """
    cached = cache.get(_AUTOMATION_EVENTS_CACHE_KEY)
    if cached is not None:
        return cached

    from apps.core.models import SiteSettings
    result = SiteSettings.objects.filter(automation_events_enabled=True).exists()
    cache.set(_AUTOMATION_EVENTS_CACHE_KEY, result, _AUTOMATION_EVENTS_CACHE_TTL)
    return result


@shared_task(bind=True, max_retries=_MAX_EVENT_ATTEMPTS, default_retry_delay=60)
def process_whatsapp_event(self, event_id: int):
    try:
        event = WebhookEventLog.objects.get(pk=event_id)
    except WebhookEventLog.DoesNotExist:
        logger.error("process_whatsapp_event: event %s not found", event_id)
        return

    if event.processed:
        return

    try:
        if event.event_type == "message":
            _handle_inbound_message(event)
        elif event.event_type == "status":
            _handle_status_update(event)
        else:
            logger.debug(
                "process_whatsapp_event: no handler for event_type=%s", event.event_type
            )
        event.mark_processed()
    except TenantResolutionError as exc:
        logger.warning(
            "process_whatsapp_event: tenant not found for event %s: %s", event_id, exc
        )
        event.mark_failed(str(exc))
    except Exception as exc:
        event.mark_failed(str(exc))
        logger.exception("process_whatsapp_event: unhandled error for event %s", event_id)
        raise self.retry(exc=exc)


def _handle_inbound_message(event: WebhookEventLog) -> None:
    value = event.payload["entry"][0]["changes"][0]["value"]
    message = value["messages"][0]
    phone_number_id = value["metadata"]["phone_number_id"]

    account = get_account_for_webhook(phone_number_id)

    try:
        from apps.billing.limits import LimitChecker, PlanLimitExceeded
        LimitChecker(account).check_conversation()
    except Exception as exc:
        from apps.billing.limits import PlanLimitExceeded
        if isinstance(exc, PlanLimitExceeded):
            logger.warning(
                "_handle_inbound_message: conversation limit exceeded for account %s: %s",
                account.pk, exc,
            )
            return
        logger.debug("_handle_inbound_message: limit check skipped: %s", exc)

    wa_id = message["from"]
    profile_name = (value.get("contacts") or [{}])[0].get("profile", {}).get("name")

    contact, _ = WhatsAppContact.objects.get_or_create(
        account=account,
        phone_number=wa_id,
        defaults={"display_name": profile_name},
    )
    if profile_name and contact.display_name != profile_name:
        contact.display_name = profile_name
        contact.save(update_fields=["display_name"])

    msg_ts = datetime.fromtimestamp(int(message["timestamp"]), tz=dt_timezone.utc)
    conversation = Conversation.get_or_open(contact)
    conversation.register_inbound(msg_ts)

    try:
        from apps.billing.models import UsageSummary
        UsageSummary.increment_conversations(account)
    except Exception as exc:
        logger.debug("_handle_inbound_message: usage increment skipped: %s", exc)

    msg_type = message.get("type", "unknown")
    content = ""
    media_id = media_mime_type = None

    if msg_type == "text":
        content = message.get("text", {}).get("body", "")
    elif msg_type in ("image", "audio", "video", "document", "sticker"):
        block = message.get(msg_type, {})
        media_id = block.get("id")
        media_mime_type = block.get("mime_type")
        content = block.get("caption", "")
    elif msg_type == "location":
        loc = message.get("location", {})
        content = f"{loc.get('latitude')},{loc.get('longitude')}"

    valid_types = {c[0] for c in MessageLog.MessageType.choices}
    message_log, created = MessageLog.objects.get_or_create(
        account=account,
        message_id=message.get("id"),
        defaults={
            "conversation": conversation,
            "contact": contact,
            "direction": MessageLog.Direction.INBOUND,
            "message_type": msg_type if msg_type in valid_types else MessageLog.MessageType.UNKNOWN,
            "content": content,
            "media_id": media_id,
            "media_mime_type": media_mime_type,
            "status": MessageLog.Status.DELIVERED,
            "timestamp": msg_ts,
            "raw_payload": event.payload,
        },
    )

    contact.last_message_at = msg_ts
    contact.save(update_fields=["last_message_at"])

    # Publish domain event for subscribers (automation, AI, analytics)
    # IMPORTANT: Only publish for newly created messages to prevent duplicate automation
    # evaluations when webhooks are replayed or messages are reprocessed.
    # Gate behind a temporary SiteSettings flag until Phase 4's ModuleSubscription exists
    if created:
        try:
            if _automation_events_enabled():
                dispatcher.publish(
                    MessageReceived(
                        account_id=account.id,
                        contact_id=contact.id,
                        message_id=message.get("id"),
                        channel="whatsapp",
                        body=content,
                        message_type=msg_type,
                        occurred_at=msg_ts,
                    )
                )
        except Exception as exc:
            logger.debug("_handle_inbound_message: failed to publish event: %s", exc)


def _handle_status_update(event: WebhookEventLog) -> None:
    value = event.payload["entry"][0]["changes"][0]["value"]
    status_obj = value["statuses"][0]

    message_id = status_obj.get("id")
    new_status = status_obj.get("status")  # "sent" | "delivered" | "read" | "failed"
    if not message_id or not new_status:
        return

    try:
        log = MessageLog.objects.get(
            message_id=message_id,
            direction=MessageLog.Direction.OUTBOUND,
        )
        status_changed = log.apply_status_update(new_status)

        # Publish domain event for subscribers (automation, AI, analytics)
        # IMPORTANT: Only publish if the status actually changed to prevent duplicate
        # automation evaluations when webhooks are replayed.
        # Gate behind a temporary SiteSettings flag until Phase 4's ModuleSubscription exists
        if status_changed:
            try:
                if _automation_events_enabled():
                    dispatcher.publish(
                        MessageStatusChanged(
                            account_id=log.account_id,
                            message_id=message_id,
                            status=new_status,
                            occurred_at=timezone.now(),
                        )
                    )
            except Exception as exc:
                logger.debug("_handle_status_update: failed to publish event: %s", exc)

    except MessageLog.DoesNotExist:
        logger.debug(
            "_handle_status_update: no outbound log for message_id=%s", message_id
        )


def _get_provider_for_account(account):
    """Get a WhatsAppProvider instance for the account.

    Returns None if the account has no usable (active, tokened) number.
    """
    from apps.whatsapp.providers import get_whatsapp_provider, WhatsAppProviderError

    try:
        return get_whatsapp_provider(account)
    except WhatsAppProviderError:
        return None


def _send_outbound(provider, contact, payload: dict) -> dict:
    """Dispatch an OutboundMessage payload via the provider.

    Args:
        provider: WhatsAppProvider instance.
        contact: WhatsAppContact instance.
        payload: Message payload dict with type, content, etc.

    Returns:
        Dict with success status and message_id (or error info).

    Raises:
        WhatsAppProviderError: if the provider call fails.
    """
    from apps.whatsapp.providers import WhatsAppProviderError

    msg_type = payload.get("type", "text")
    to = contact.phone_number

    try:
        if msg_type == "template":
            result = provider.send_template(
                to,
                payload["template_name"],
                payload.get("language", "en"),
                payload.get("components", payload.get("params", [])) or [],
            )
        elif msg_type in ("image", "audio", "video", "document", "sticker"):
            result = provider.send_media(
                to, msg_type, payload["media_id"], payload.get("caption", "")
            )
        else:
            result = provider.send_text(to, payload.get("body", payload.get("text", "")))

        if not result.success:
            raise WhatsAppProviderError(f"Send failed: {result.error}")

        return {"success": True, "message_id": result.message_id}
    except WhatsAppProviderError as e:
        raise


@shared_task
def drain_outbound_queue():
    now = timezone.now()
    due = (
        OutboundMessage.objects.filter(
            status=OutboundMessage.Status.QUEUED,
            scheduled_at__lte=now,
        )
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .select_related("account", "contact")[:_OUTBOUND_BATCH]
    )

    sent = failed = 0
    providers: dict = {}
    for msg in due:
        try:
            provider = providers.get(msg.account_id)
            if provider is None:
                provider = _get_provider_for_account(msg.account)
                providers[msg.account_id] = provider
            if provider is None:
                raise RuntimeError(
                    "No active WhatsApp number with an access token for this account."
                )

            _send_outbound(provider, msg.contact, msg.payload)
            msg.status = OutboundMessage.Status.SENT
            msg.sent_at = timezone.now()
            msg.save(update_fields=["status", "sent_at"])
            sent += 1
        except Exception as exc:
            msg.mark_failed(str(exc))
            failed += 1

    if sent or failed:
        logger.info("drain_outbound_queue: sent=%s failed=%s", sent, failed)


@shared_task
def close_expired_conversations():
    expired = Conversation.objects.filter(
        is_open=True,
        window_expires_at__isnull=False,
        window_expires_at__lte=timezone.now(),
    )
    count = 0
    for convo in expired.iterator():
        convo.close()
        count += 1
    if count:
        logger.info("close_expired_conversations: closed %s conversations", count)


@shared_task
def download_media():
    pending = (
        MessageLog.objects.filter(
            direction=MessageLog.Direction.INBOUND,
            media_id__isnull=False,
            media_url__isnull=True,
        )
        .select_related("account")[:_MEDIA_BATCH]
    )
    count = 0
    for log in pending:
        try:
            count += 1
        except Exception as exc:
            logger.warning(
                "download_media: failed for MessageLog pk=%s: %s", log.pk, exc
            )
    if count:
        logger.info("download_media: fetched %s media URLs", count)
