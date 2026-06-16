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

    slug = models.CharField(max_length=20, unique=True, choices=SLUG_CHOICES)
    name = models.CharField(max_length=100)
    price_monthly = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))

    max_conversations_per_month = models.IntegerField(default=100)
    max_emails_per_month = models.IntegerField(default=1000)
    max_mailboxes = models.IntegerField(default=1)
    max_automation_rules = models.IntegerField(default=2)
    max_whatsapp_numbers = models.IntegerField(default=1)

    trial_days = models.IntegerField(default=0)
    has_priority_support = models.BooleanField(default=False)

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
