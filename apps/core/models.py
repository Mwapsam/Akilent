from django.db import models
from django.core.validators import URLValidator


class SiteSettings(models.Model):
    """Platform-wide configuration, edited by admins in the Settings page.

    A singleton (always pk=1) loaded via ``SiteSettings.load()``.
    """

    # Branding
    app_name = models.CharField(max_length=100, default="Automator")
    logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    support_email = models.EmailField(blank=True, default="")

    # Feature flags (UI layer). WhatsApp/Bitrix also require the matching env
    # flag at boot to wire URLs/Celery — these can only further *disable* them.
    whatsapp_enabled = models.BooleanField(default=True)
    bitrix_enabled = models.BooleanField(default=True)
    signups_enabled = models.BooleanField(default=True)
    payments_enabled = models.BooleanField(default=True)

    # Phase 1: temporary gate for automation event publishing until Phase 4's ModuleSubscription exists
    automation_events_enabled = models.BooleanField(
        default=False,
        help_text="Enable domain event publishing for automation rules and AI (Phase 1 beta)",
    )

    # New-signup defaults
    default_plan = models.ForeignKey(
        "billing.Plan", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    default_trial_days = models.PositiveIntegerField(default=14)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return self.app_name

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class MailProviderSettings(models.Model):
    """Platform-wide mail infrastructure configuration, edited by superadmins.

    A singleton (always pk=1) loaded via MailProviderSettings.load().
    Allows runtime switching between different mail server backends (Stalwart, SES, etc.)
    and message delivery providers (SMTP, SES API, etc.) without redeploying.
    """

    BACKEND_CHOICES = [
        ("stalwart", "Stalwart Mail Server"),
        ("ses", "AWS SES"),
        ("null", "Null (dev/test)"),
    ]

    SEND_BACKEND_CHOICES = [
        ("smtp", "SMTP Relay"),
        ("ses", "AWS SES API"),
        ("null", "Null (dev/test)"),
    ]

    # Infrastructure provider (domain provisioning, DKIM, mailbox management if applicable)
    infra_backend = models.CharField(
        max_length=32,
        choices=BACKEND_CHOICES,
        default="stalwart",
        help_text="Mail server for domain/DKIM infrastructure"
    )

    # Message delivery provider
    send_backend = models.CharField(
        max_length=32,
        choices=SEND_BACKEND_CHOICES,
        default="smtp",
        help_text="Provider for outbound message delivery"
    )

    # SES-specific configuration (read from env vars at runtime, just store names here)
    aws_region = models.CharField(
        max_length=32,
        default="us-east-1",
        help_text="AWS region for SES (e.g., us-east-1, eu-west-1). Credentials from AWS_ACCESS_KEY_ID env vars."
    )
    ses_configuration_set = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="SES configuration set name for tracking bounces/complaints (optional but recommended)"
    )
    ses_sns_topic_arn = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="SNS topic ARN for receiving bounce/complaint notifications (required if configuration_set is used)"
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mail provider settings"
        verbose_name_plural = "Mail provider settings"

    def __str__(self):
        return f"Mail: {self.get_infra_backend_display()} (send: {self.get_send_backend_display()})"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Get or create the singleton settings row."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
