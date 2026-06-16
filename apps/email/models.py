import secrets

from django.db import models
from django.utils import timezone


class EmailDomain(models.Model):
    """A per-tenant sending domain provisioned on Mailcow.

    Transactional email is sent *from* a verified domain; we provision the
    domain on Mailcow, expose the generated DKIM record for the tenant to add to
    DNS, and only allow sending once verified.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending DNS / verification"
        VERIFIED = "verified", "Verified"
        FAILED = "failed", "Failed"

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="email_domains"
    )

    domain = models.CharField(max_length=255, unique=True)

    dkim_selector = models.CharField(max_length=63, default="dkim")
    dkim_public_key = models.TextField(blank=True, default="")  # DKIM TXT record value

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    is_active = models.BooleanField(default=True)  # enabled/disabled on the mail server
    spf_ok = models.BooleanField(default=False)
    dkim_ok = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["domain"]

    @property
    def is_verified(self) -> bool:
        return self.status == self.Status.VERIFIED

    @property
    def spf_record(self) -> str:
        return "v=spf1 include:%(host)s ~all"

    @property
    def dkim_record_name(self) -> str:
        return f"{self.dkim_selector}._domainkey.{self.domain}"

    @property
    def dmarc_record_name(self) -> str:
        return f"_dmarc.{self.domain}"

    def __str__(self):
        return f"{self.domain} ({self.status})"


class EmailApiKey(models.Model):
    """A per-account key authenticating calls to the transactional send API."""

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="email_api_keys"
    )
    name = models.CharField(max_length=100, default="default")
    key = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(blank=True, null=True)

    @staticmethod
    def generate_key() -> str:
        return "ek_" + secrets.token_urlsafe(36)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        super().save(*args, **kwargs)

    def touch(self):
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    def __str__(self):
        return f"{self.account} :: {self.name}"


class Mailbox(models.Model):
    """A real mailbox provisioned on the iRedMail server for a tenant.

    Mailboxes are a billable unit sold per subscription package. The account's
    password is never stored here — it is passed straight to iredmail-api at
    provisioning time.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        FAILED = "failed", "Failed"

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="mailboxes"
    )
    domain = models.ForeignKey(
        EmailDomain, on_delete=models.CASCADE, related_name="mailboxes"
    )

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    quota_mb = models.PositiveIntegerField(default=1024)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    error = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return f"{self.email} ({self.status})"


class EmailAlias(models.Model):
    """An alias address forwarding to another address (iRedMail alias)."""

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="email_aliases"
    )
    domain = models.ForeignKey(
        EmailDomain, on_delete=models.CASCADE, related_name="aliases"
    )

    address = models.EmailField()
    goto = models.EmailField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["address"]
        unique_together = ("address", "goto")

    def __str__(self):
        return f"{self.address} -> {self.goto}"


class EmailMessage(models.Model):
    """Log of a transactional email send."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="email_messages"
    )
    domain = models.ForeignKey(
        EmailDomain, on_delete=models.SET_NULL, blank=True, null=True
    )

    from_email = models.EmailField()
    to_email = models.EmailField()
    subject = models.CharField(max_length=998, blank=True, default="")

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED
    )
    provider_message_id = models.CharField(max_length=255, blank=True, null=True)
    error = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["account", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def mark_sent(self, provider_message_id: str = ""):
        self.status = self.Status.SENT
        self.provider_message_id = provider_message_id or None
        self.sent_at = timezone.now()
        self.save(update_fields=["status", "provider_message_id", "sent_at"])

    def mark_failed(self, error: str):
        self.status = self.Status.FAILED
        self.error = (error or "")[:5000]
        self.save(update_fields=["status", "error"])

    def __str__(self):
        return f"{self.to_email} [{self.status}]"
