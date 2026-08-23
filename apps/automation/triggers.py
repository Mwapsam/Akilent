import logging

from apps.core.events import MessageReceived, MessageStatusChanged
from apps.whatsapp.models import AutomationRule, WhatsAppContact

logger = logging.getLogger(__name__)


def on_message_received(event: MessageReceived, **kwargs) -> None:
    """Fire automation rules when a MessageReceived domain event is published.

    This is triggered by apps/whatsapp when an inbound message is processed,
    published as a domain event via the event dispatcher. Rule evaluation
    is dispatched asynchronously to the 'automation' Celery queue to keep
    the event publisher (webhook handling) decoupled from rule latency.

    Note: **kwargs is required by Django's signal dispatcher, though unused.
    """
    from apps.automation.tasks import evaluate_rules_for_message

    # Get the contact details for context
    try:
        contact = WhatsAppContact.objects.get(id=event.contact_id)
    except WhatsAppContact.DoesNotExist:
        logger.warning(
            "on_message_received: contact %s not found for account %s",
            event.contact_id,
            event.account_id,
        )
        return

    context = {
        "phone_number": contact.phone_number,
        "message_type": event.message_type,
        "message_contains": event.body,
    }

    # Dispatch rule evaluation to Celery asynchronously (automation queue)
    evaluate_rules_for_message.delay(
        event.account_id,
        AutomationRule.TriggerEvent.MESSAGE_RECEIVED,
        context,
    )


def on_message_sent(message_log) -> None:
    """Fire automation rules after an outbound message is sent.

    Note: This is kept for backwards compatibility but is not yet
    driven by domain events. It's called from views/tasks directly.
    TODO(Phase 2): refactor to use domain events via dispatcher.
    """
    from apps.automation.workflows import execute_rule

    context = {
        "phone_number": message_log.contact.phone_number,
        "message_type": message_log.message_type,
    }
    _dispatch(message_log.account_id, AutomationRule.TriggerEvent.MESSAGE_SENT, context)


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
