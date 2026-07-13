from django.contrib import admin

from .models import ManualPaymentRequest, PaymentMethod, Plan, Subscription, UsageSummary


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = [
        "name", "slug", "price_monthly",
        "max_conversations_per_month", "max_emails_per_month", "max_automation_rules",
        "max_whatsapp_numbers", "trial_days", "has_priority_support", "bulk_email", "is_active",
    ]
    list_filter = ["is_active", "has_priority_support"]
    search_fields = ["name", "slug"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "account", "plan", "status",
        "trial_ends_at", "current_period_start", "current_period_end",
        "fw_subscription_id", "created_at",
    ]
    list_filter = ["status", "plan"]
    search_fields = ["account__company_name", "account__slug", "fw_customer_email"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["account"]


@admin.register(UsageSummary)
class UsageSummaryAdmin(admin.ModelAdmin):
    list_display = ["account", "period_start", "conversations_used", "emails_used"]
    list_filter = ["period_start"]
    search_fields = ["account__company_name"]
    raw_id_fields = ["account"]


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_enabled", "sort_order"]
    list_filter = ["is_enabled"]
    search_fields = ["name", "code"]


@admin.register(ManualPaymentRequest)
class ManualPaymentRequestAdmin(admin.ModelAdmin):
    list_display = ["account", "plan", "status", "reference", "reviewed_by", "created_at"]
    list_filter = ["status", "plan"]
    search_fields = ["account__company_name", "reference"]
    raw_id_fields = ["account"]
    readonly_fields = ["created_at"]
