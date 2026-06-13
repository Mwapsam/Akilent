from django.contrib import admin

from apps.whatsapp.models import (
    AutomationRule,
    BitrixAccount,
    CrmBinding,
    Conversation,
    MessageLog,
    MessageTemplate,
    OutboundMessage,
    WebhookEventLog,
    WhatsAppContact,
)
from apps.whatsapp.models.tenant import WhatsAppBusinessNumber


@admin.register(BitrixAccount)
class BitrixAccountAdmin(admin.ModelAdmin):
    list_display = ("company_name", "domain", "is_active", "expires_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("company_name", "domain")
    readonly_fields = ("created_at",)


@admin.register(WhatsAppBusinessNumber)
class WhatsAppBusinessNumberAdmin(admin.ModelAdmin):
    list_display = ("bitrix_account", "phone_number_id", "display_number", "waba_id", "is_active")
    list_filter = ("is_active", "bitrix_account")
    search_fields = ("phone_number_id", "display_number", "waba_id")


@admin.register(WhatsAppContact)
class WhatsAppContactAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "display_name", "bitrix_account", "last_message_at", "created_at")
    list_filter = ("bitrix_account",)
    search_fields = ("phone_number", "display_name")
    readonly_fields = ("created_at",)


@admin.register(CrmBinding)
class CrmBindingAdmin(admin.ModelAdmin):
    list_display = ("contact", "entity_type", "entity_id", "is_primary", "bitrix_account")
    list_filter = ("entity_type", "is_primary", "bitrix_account")
    search_fields = ("entity_id", "contact__phone_number")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("contact", "bitrix_account", "is_open", "window_expires_at", "last_message_at", "created_at")
    list_filter = ("is_open", "bitrix_account")
    search_fields = ("contact__phone_number",)
    readonly_fields = ("created_at", "closed_at")


@admin.register(MessageLog)
class MessageLogAdmin(admin.ModelAdmin):
    list_display = ("contact", "direction", "message_type", "status", "timestamp", "bitrix_account")
    list_filter = ("direction", "message_type", "status", "bitrix_account")
    search_fields = ("contact__phone_number", "message_id", "content")
    readonly_fields = ("created_at", "raw_payload")


@admin.register(OutboundMessage)
class OutboundMessageAdmin(admin.ModelAdmin):
    list_display = ("contact", "status", "attempts", "scheduled_at", "next_attempt_at", "sent_at")
    list_filter = ("status", "bitrix_account")
    search_fields = ("contact__phone_number", "idempotency_key")
    readonly_fields = ("created_at", "sent_at", "last_error")


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "whatsapp_template_name", "category", "approval_status", "language_code", "bitrix_account")
    list_filter = ("approval_status", "category", "bitrix_account")
    search_fields = ("name", "whatsapp_template_name")
    readonly_fields = ("created_at",)


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "trigger_event", "is_active", "bitrix_account", "created_at")
    list_filter = ("trigger_event", "is_active", "bitrix_account")
    search_fields = ("name",)
    readonly_fields = ("created_at",)


@admin.register(WebhookEventLog)
class WebhookEventLogAdmin(admin.ModelAdmin):
    list_display = ("source", "event_type", "processed", "attempts", "created_at", "processed_at")
    list_filter = ("source", "event_type", "processed")
    search_fields = ("event_type",)
    readonly_fields = ("created_at", "processed_at", "payload", "error_message")
