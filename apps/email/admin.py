from django.contrib import admin

from apps.email.models import (
    AuditLog,
    EmailApiKey,
    EmailDomain,
    EmailMessage,
    EmailTemplate,
    EmailTemplateAsset,
    EmailTemplateVersion,
    Mailbox,
    ProvisioningJob,
    SystemEmailTemplate,
)


@admin.register(EmailDomain)
class EmailDomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "account", "status", "dkim_ok", "spf_ok", "created_at")
    list_filter = ("status", "dkim_ok", "spf_ok")
    search_fields = ("domain", "account__company_name")
    raw_id_fields = ("account",)
    readonly_fields = ("created_at", "verified_at", "last_checked_at")


@admin.register(EmailApiKey)
class EmailApiKeyAdmin(admin.ModelAdmin):
    list_display = ("account", "name", "is_active", "created_at", "last_used_at")
    list_filter = ("is_active",)
    search_fields = ("account__company_name", "key")
    raw_id_fields = ("account",)
    readonly_fields = ("key", "created_at", "last_used_at")


@admin.register(Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    list_display = ("email", "account", "status", "quota_mb", "created_at")
    list_filter = ("status",)
    search_fields = ("email", "account__company_name")
    raw_id_fields = ("account", "domain")
    readonly_fields = ("created_at", "error")


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("to_email", "from_email", "account", "status", "created_at", "sent_at")
    list_filter = ("status",)
    search_fields = ("to_email", "from_email", "subject", "account__company_name")
    raw_id_fields = ("account", "domain")
    readonly_fields = ("created_at", "sent_at", "provider_message_id", "error")


class EmailTemplateVersionInline(admin.TabularInline):
    model = EmailTemplateVersion
    extra = 0
    fields = ("created_at", "created_by", "subject")
    readonly_fields = ("created_at", "created_by", "subject")
    can_delete = False
    max_num = 0

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "account", "builder_mode", "is_active", "updated_at")
    list_filter = ("is_active", "builder_mode")
    search_fields = ("name", "slug", "account__company_name")
    raw_id_fields = ("account",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [EmailTemplateVersionInline]


@admin.register(SystemEmailTemplate)
class SystemEmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("key", "name", "subject")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EmailTemplateAsset)
class EmailTemplateAssetAdmin(admin.ModelAdmin):
    list_display = ("file", "account", "uploaded_at")
    search_fields = ("account__company_name",)
    raw_id_fields = ("account",)
    readonly_fields = ("uploaded_at",)


@admin.register(ProvisioningJob)
class ProvisioningJobAdmin(admin.ModelAdmin):
    list_display = (
        "job_type", "resource_id", "account", "status",
        "attempts", "created_at", "completed_at",
    )
    list_filter = ("status", "job_type", "resource_type")
    search_fields = ("resource_id", "account__company_name", "celery_task_id")
    raw_id_fields = ("account",)
    readonly_fields = (
        "created_at", "started_at", "completed_at", "celery_task_id", "attempts"
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "action", "resource_id", "account", "actor", "success", "timestamp"
    )
    list_filter = ("success", "action", "resource_type")
    search_fields = ("resource_id", "account__company_name", "action")
    raw_id_fields = ("account", "actor")
    readonly_fields = (
        "timestamp", "account", "actor", "action", "resource_type",
        "resource_id", "success", "error", "metadata",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
