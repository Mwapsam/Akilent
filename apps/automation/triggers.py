import logging

from apps.whatsapp.models import AutomationRule, Conversation, MessageLog

logger = logging.getLogger(__name__)


def on_message_received(message: MessageLog) -> None:
    """Fire automation rules when an inbound WhatsApp message arrives."""
    from apps.automation.workflows import execute_rule

    context = {
        "phone_number": message.contact.phone_number,
        "message_type": message.message_type,
        "message_contains": message.content,
    }
    _dispatch(message.bitrix_account_id, AutomationRule.TriggerEvent.MESSAGE_RECEIVED, context)


def on_message_sent(message: MessageLog) -> None:
    """Fire automation rules after an outbound message is sent."""
    from apps.automation.workflows import execute_rule

    context = {
        "phone_number": message.contact.phone_number,
        "message_type": message.message_type,
    }
    _dispatch(message.bitrix_account_id, AutomationRule.TriggerEvent.MESSAGE_SENT, context)


def on_lead_created(account_id: int, lead_id: str, fields: dict) -> None:
    """Fire automation rules when a Bitrix24 lead is created."""
    context = {"lead_id": lead_id, **fields}
    _dispatch(account_id, AutomationRule.TriggerEvent.LEAD_CREATED, context)


def on_deal_stage_changed(account_id: int, deal_id: str, stage_id: str) -> None:
    """Fire automation rules when a Bitrix24 deal changes stage."""
    context = {"deal_id": deal_id, "stage_id": stage_id}
    _dispatch(account_id, AutomationRule.TriggerEvent.DEAL_STAGE_CHANGED, context)


def _dispatch(account_id: int, event: str, context: dict) -> None:
    from apps.automation.rules import evaluate_conditions, get_matching_rules
    from apps.automation.workflows import execute_rule

    for rule in get_matching_rules(account_id, event):
        if evaluate_conditions(rule, context):
            try:
                execute_rule(rule, context)
            except Exception:
                logger.exception("_dispatch: error executing rule pk=%s", rule.pk)
