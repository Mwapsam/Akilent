from decimal import Decimal

from django.db import models, transaction
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
    bulk_email = models.BooleanField(default=False)          # mass/campaign sending
    email_templates = models.BooleanField(default=True)      # DB-backed reusable templates
    # -1 = unlimited, same convention as the other max_* fields.
    max_bulk_recipients_per_campaign = models.IntegerField(default=500)
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

    # Which payment method activated the current period — audit/display only,
    # the gateway-specific fields above (fw_*) remain the source of truth for
    # recurring-billing operations on Flutterwave-activated subscriptions.
    payment_method = models.CharField(max_length=20, blank=True, default="")

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


class PaymentMethod(models.Model):
    """Admin-configurable payment option (gateway).

    ``code`` must match a key registered in ``apps.billing.gateways.registry``.
    Enabling/disabling here is what controls what tenants see at checkout —
    the gateway implementation itself stays code-only.
    """

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    is_enabled = models.BooleanField(default=False)
    # Free-form instructions shown to the customer at checkout — e.g. bank
    # account / mobile money details for the manual gateway. Unused by
    # hosted gateways like Flutterwave.
    instructions = models.TextField(blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ManualPaymentRequest(models.Model):
    """A tenant-submitted offline payment awaiting admin approval."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="manual_payment_requests"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="manual_payment_requests")
    reference = models.CharField(max_length=255, blank=True, default="")
    proof = models.FileField(upload_to="manual_payments/", blank=True, null=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    note = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account} — {self.plan.name} ({self.status})"


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
    def reserve_email_bulk(cls, account, cap: int, count: int) -> int:
        """Atomically claim up to `count` emails against the monthly cap.

        cap=-1 means unlimited. Returns the number actually reserved, which
        may be less than `count` (including 0) if the cap is close to being
        reached — callers use this to implement partial-send semantics for
        bulk campaigns rather than an all-or-nothing accept/reject.
        """
        if count <= 0:
            return 0
        period_start = timezone.now().date().replace(day=1)
        with transaction.atomic():
            obj, _ = cls.objects.select_for_update().get_or_create(
                account=account, period_start=period_start
            )
            if cap == -1:
                granted = count
            else:
                granted = max(0, min(count, cap - obj.emails_used))
            if granted:
                cls.objects.filter(pk=obj.pk).update(
                    emails_used=F("emails_used") + granted
                )
            return granted

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
