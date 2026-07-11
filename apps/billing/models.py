from decimal import Decimal

from django.db import models
from django.db.models import F
from django.utils import timezone


class Plan(models.Model):
    TRIAL = "trial"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    BUSINESS = "business"

    SLUG_CHOICES = [
        (TRIAL, "Trial"),
        (STARTER, "Starter"),
        (PROFESSIONAL, "Professional"),
        (BUSINESS, "Business"),
    ]

    # Free-form so admins can create custom packages; the constants above are
    # just the seeded defaults referenced in code (signals, limits).
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    price_monthly = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))

    max_conversations_per_month = models.IntegerField(default=100)
    max_emails_per_month = models.IntegerField(default=1000)
    max_mailboxes = models.IntegerField(default=1)
    mailbox_storage_gb = models.PositiveIntegerField(default=10)  # storage per mailbox
    max_forwarding_rules = models.IntegerField(default=10)        # -1 = unlimited
    max_aliases = models.IntegerField(default=10)                 # -1 = unlimited
    max_automation_rules = models.IntegerField(default=2)
    max_whatsapp_numbers = models.IntegerField(default=1)

    trial_days = models.IntegerField(default=0)
    has_priority_support = models.BooleanField(default=False)

    # Email-platform capabilities — toggled/edited per package by admins.
    email_apis = models.BooleanField(default=True)          # RESTful API + SMTP relay
    inbound_email = models.BooleanField(default=False)      # inbound email processing
    tracking_webhooks = models.BooleanField(default=False)  # tracking, analytics & webhooks
    detailed_analytics = models.BooleanField(default=False) # detailed analytics & insights
    outbound_webhooks = models.BooleanField(default=False)  # delivery/open/click callbacks
    log_retention_days = models.PositiveIntegerField(default=7)  # log retention window
    # Requests/minute against apps.api (distinct from max_emails_per_month,
    # which is a monthly volume cap, not a request-rate limit).
    api_rate_per_min = models.PositiveIntegerField(default=60)

    # Set this once you create matching plans in the Flutterwave dashboard
    flutterwave_plan_id = models.CharField(max_length=100, blank=True, null=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["price_monthly"]

    def __str__(self):
        return self.name


class Subscription(models.Model):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    STATUS_CHOICES = [
        (TRIALING, "Trialing"),
        (ACTIVE, "Active"),
        (PAST_DUE, "Past Due"),
        (CANCELLED, "Cancelled"),
        (EXPIRED, "Expired"),
    ]

    account = models.OneToOneField(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=TRIALING)

    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField(null=True, blank=True)

    fw_customer_email = models.CharField(max_length=255, blank=True, null=True)
    fw_subscription_id = models.CharField(max_length=100, blank=True, null=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account} — {self.plan.name} ({self.status})"

    @property
    def is_active(self):
        return self.status in (self.TRIALING, self.ACTIVE)

    @property
    def is_trialing(self):
        return self.status == self.TRIALING


class ProcessedWebhookEvent(models.Model):
    """De-dupe ledger for inbound payment webhooks.

    A replayed Flutterwave event (same event type + provider object id) must not
    re-activate or re-extend a subscription. The unique ``event_key`` makes
    processing idempotent: if the row already exists, the event was handled.
    """

    event_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.event_key


class UsageSummary(models.Model):
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="usage_summaries",
    )
    period_start = models.DateField()
    conversations_used = models.PositiveIntegerField(default=0)
    emails_used = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("account", "period_start")

    def __str__(self):
        return f"{self.account} {self.period_start}: {self.conversations_used} conv / {self.emails_used} email"

    @classmethod
    def _increment(cls, account, field: str):
        period_start = timezone.now().date().replace(day=1)
        cls.objects.get_or_create(account=account, period_start=period_start)
        cls.objects.filter(
            account=account,
            period_start=period_start,
        ).update(**{field: F(field) + 1})

    @classmethod
    def increment_conversations(cls, account):
        cls._increment(account, "conversations_used")

    @classmethod
    def increment_emails(cls, account):
        cls._increment(account, "emails_used")

    @classmethod
    def reserve_email(cls, account, cap: int) -> bool:
        """Atomically claim one email against the monthly cap.

        cap=-1 means unlimited. Returns False (without incrementing) if the
        cap has already been reached — the single conditional UPDATE closes
        the check-then-increment race a plain get()-then-F()-update would
        leave open under concurrent sends.
        """
        period_start = timezone.now().date().replace(day=1)
        obj, _ = cls.objects.get_or_create(account=account, period_start=period_start)
        if cap == -1:
            cls.objects.filter(pk=obj.pk).update(emails_used=F("emails_used") + 1)
            return True
        updated = cls.objects.filter(pk=obj.pk, emails_used__lt=cap).update(
            emails_used=F("emails_used") + 1
        )
        return updated > 0

    @classmethod
    def release_email(cls, account) -> None:
        """Refund a reservation made by reserve_email (e.g. on terminal send failure)."""
        period_start = timezone.now().date().replace(day=1)
        cls.objects.filter(
            account=account, period_start=period_start, emails_used__gt=0
        ).update(emails_used=F("emails_used") - 1)

    @classmethod
    def get_current_usage(cls, account):
        period_start = timezone.now().date().replace(day=1)
        try:
            return cls.objects.get(account=account, period_start=period_start).conversations_used
        except cls.DoesNotExist:
            return 0

    @classmethod
    def get_current_email_usage(cls, account):
        period_start = timezone.now().date().replace(day=1)
        try:
            return cls.objects.get(account=account, period_start=period_start).emails_used
        except cls.DoesNotExist:
            return 0
