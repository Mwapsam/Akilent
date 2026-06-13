import logging

from apps.whatsapp.models import WebhookEventLog

logger = logging.getLogger(__name__)


def handle_event(event_type: str, payload: dict) -> None:
    """Route an incoming Bitrix24 webhook event to the appropriate handler."""
    handler = _HANDLERS.get(event_type)
    if handler is None:
        logger.debug("webhooks.handle_event: no handler for event_type=%s", event_type)
        return
    handler(payload)


def _on_lead_add(payload: dict) -> None:
    # TODO: create WhatsApp contact, send greeting template
    logger.info("Bitrix lead added: %s", payload.get("data", {}).get("FIELDS", {}).get("ID"))


def _on_deal_stage_change(payload: dict) -> None:
    # TODO: trigger automation rules based on new stage
    fields = payload.get("data", {}).get("FIELDS", {})
    logger.info(
        "Deal %s moved to stage %s",
        fields.get("ID"),
        fields.get("STAGE_ID"),
    )


def _on_contact_update(payload: dict) -> None:
    # TODO: sync updated contact fields back to WhatsAppContact
    logger.info("Bitrix contact updated: %s", payload.get("data", {}).get("FIELDS", {}).get("ID"))


_HANDLERS = {
    "ONCRMLЕADADD": _on_lead_add,         # noqa: RUF001
    "ONCRMDEАЛSTAGECHANGE": _on_deal_stage_change,
    "ONCRMCONTACTUPDATE": _on_contact_update,
}
