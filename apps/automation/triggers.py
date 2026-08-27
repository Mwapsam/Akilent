import logging

from apps.core.events import MessageReceived, MessageStatusChanged
from apps.whatsapp import api as whatsapp_api

logger = logging.getLogger(__name__)


def on_message_received(event: MessageReceived, **kwargs) -> None:
    from apps.automation.tasks import evaluate_rules_for_message
    from apps.automation.models import AutomationRule
    from apps.accounts import api as accounts_api

    # Get the contact details for context
    try:
        account = accounts_api.get_account(event.account_id)
        contact = whatsapp_api.get_contact(account, event.contact_id)
    except Exception:
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
    from apps.automation.workflows import execute_rule
    from apps.whatsapp.models import AutomationRule

    context = {
        "phone_number": message_log.contact.phone_number,
        "message_type": message_log.message_type,
    }
    _dispatch(message_log.account_id, AutomationRule.TriggerEvent.MESSAGE_SENT, context)


def on_lead_created(account_id: int, lead_id: str, fields: dict) -> None:
    pass


def on_deal_stage_changed(account_id: int, deal_id: str, stage_id: str) -> None:
    pass


def _dispatch(account_id: int, event: str, context: dict) -> None:
    from apps.automation.rules import evaluate_conditions, get_matching_rules
    from apps.automation.workflows import execute_rule

    for rule in get_matching_rules(account_id, event):
        if evaluate_conditions(rule, context):
            try:
                execute_rule(rule, context)
            except Exception:
                logger.exception("_dispatch: error executing rule pk=%s", rule.pk)
