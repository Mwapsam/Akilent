import logging

from apps.whatsapp.models import AutomationRule, OutboundMessage

logger = logging.getLogger(__name__)


def execute_rule(rule: AutomationRule, context: dict) -> None:
    """Dispatch the rule's action to the appropriate handler."""
    action = rule.action
    action_type = action.get("type")

    handler = _ACTION_HANDLERS.get(action_type)
    if handler is None:
        logger.warning(
            "execute_rule: unknown action type '%s' for rule pk=%s", action_type, rule.pk
        )
        return

    logger.info("execute_rule: running '%s' for rule pk=%s", action_type, rule.pk)
    handler(rule, action, context)


def _send_whatsapp_message(rule: AutomationRule, action: dict, context: dict) -> None:
    """
    Action payload example:
        {"type": "send_whatsapp_message", "template_id": 42, "params": {...}}
    """
    from apps.whatsapp.models import MessageTemplate, WhatsAppContact

    phone = context.get("phone_number")
    template_id = action.get("template_id")
    if not phone or not template_id:
        logger.warning("_send_whatsapp_message: missing phone or template_id in rule pk=%s", rule.pk)
        return

    try:
        contact = WhatsAppContact.objects.get(
            bitrix_account=rule.bitrix_account, phone_number=phone
        )
        template = MessageTemplate.objects.get(pk=template_id, bitrix_account=rule.bitrix_account)
    except (WhatsAppContact.DoesNotExist, MessageTemplate.DoesNotExist) as exc:
        logger.error("_send_whatsapp_message: %s", exc)
        return

    OutboundMessage.objects.create(
        bitrix_account=rule.bitrix_account,
        contact=contact,
        template=template,
        payload={
            "type": "template",
            "template_name": template.whatsapp_template_name,
            "language": template.language_code,
            "params": action.get("params", {}),
        },
    )


def _update_crm_field(rule: AutomationRule, action: dict, context: dict) -> None:
    """
    Action payload example:
        {"type": "update_crm_field", "entity_type": "deal", "entity_id_key": "deal_id", "fields": {"STAGE_ID": "WON"}}
    """
    from apps.bitrix.client import BitrixClient

    entity_type = action.get("entity_type", "contact")
    entity_id = context.get(action.get("entity_id_key", ""))
    fields = action.get("fields", {})

    if not entity_id:
        logger.warning("_update_crm_field: no entity_id in context for rule pk=%s", rule.pk)
        return

    account = rule.bitrix_account
    client = BitrixClient(account.domain, account.access_token)
    method = f"crm.{entity_type}.update"
    client.call(method, {"id": entity_id, "fields": fields})


_ACTION_HANDLERS = {
    "send_whatsapp_message": _send_whatsapp_message,
    "update_crm_field": _update_crm_field,
}
