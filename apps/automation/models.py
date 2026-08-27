"""Automation rules and configuration.

AutomationRule defines WHEN/IF/THEN automation workflows: when a trigger event
occurs with matching conditions, execute the specified action (send message,
create contact, etc.).
"""
from django.db import models


class AutomationRule(models.Model):
    """A rule that fires actions when trigger events match conditions.

    The rule engine is deterministic-first: all conditions are evaluated
    synchronously without AI. AI-augmented conditions (Phase 5) are optional
    and gate separately.
    """

    class TriggerEvent(models.TextChoices):
        MESSAGE_RECEIVED = "message_received", "Message received"
        MESSAGE_SENT = "message_sent", "Message sent"
        LEAD_CREATED = "lead_created", "Lead created"
        DEAL_STAGE_CHANGED = "deal_stage_changed", "Deal stage changed"

    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE)

    name = models.CharField(max_length=255)
    trigger_event = models.CharField(max_length=50, choices=TriggerEvent.choices)

    conditions = models.JSONField(default=dict)
    action = models.JSONField(default=dict)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "whatsapp_automationrule"  # Pinned to original table to avoid data migration
        indexes = [
            models.Index(fields=["account", "trigger_event", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.trigger_event})"
