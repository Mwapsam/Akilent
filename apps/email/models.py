import re
import secrets

from django.conf import settings
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

    # Self-hosted ownership-verification TXT record (minted by
    # ``ensure_verification_token``). The customer adds this to DNS; the live
    # DNS check (apps.email.dnscheck) confirms it and verifies the domain.
    verify_record_name = models.CharField(max_length=255, blank=True, default="")
    verify_record_value = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    is_active = models.BooleanField(default=True)  # enabled/disabled on the mail server
    # Per-record DNS readiness, refreshed by the live DNS check. Ownership ("verify")
    # readiness is tracked by ``status == VERIFIED``.
    spf_ok = models.BooleanField(default=False)
    dkim_ok = models.BooleanField(default=False)
    dmarc_ok = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(blank=True, null=True)
    last_checked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["domain"]

    @property
    def is_verified(self) -> bool:
        return self.status == self.Status.VERIFIED

    def ensure_verification_token(self) -> bool:
        """Generate a self-hosted ownership TXT record if one isn't set yet.

        Returns True if a new record was created. Replaces the old per-account
        Progstack token flow — ownership is now proven by a TXT record we mint
        and check via DNS ourselves, so customers never handle an API token.
        """
        if self.verify_record_value:
            return False
        token = secrets.token_hex(16)
        self.verify_record_name = self.domain  # root TXT, alongside SPF
        self.verify_record_value = f"automator-domain-verification={token}"
        return True

    @property
    def spf_value(self) -> str:
        host = settings.EMAIL_HOST or "YOUR_MAIL_HOST"
        return f"v=spf1 include:{host} ~all"

    @property
    def dmarc_value(self) -> str:
        return f"v=DMARC1; p=none; rua=mailto:dmarc@{self.domain}"

    def dns_records(self):
        """The DNS records the customer must add, with current readiness baked in.

        Single source of truth for both the UI (the domain card iterates this)
        and the DNS checker, so the names/values they compare always agree.
        """
        return [
            {
                "key": "verify", "label": "Domain verification", "type": "TXT",
                "name": self.verify_record_name or self.domain,
                "value": self.verify_record_value,
                "desc": "Proves you own this domain so we can switch it on.",
                "required": True, "ok": self.is_verified,
            },
            {
                "key": "dkim", "label": "DKIM", "type": "TXT",
                "name": self.dkim_record_name, "value": self.dkim_txt_value,
                "desc": "Signs your mail so providers trust it wasn't tampered with.",
                "required": True, "ok": self.dkim_ok,
            },
            {
                "key": "spf", "label": "SPF", "type": "TXT",
                "name": self.domain, "value": self.spf_value,
                "desc": "Lists the servers allowed to send for your domain.",
                "required": False, "ok": self.spf_ok,
            },
            {
                "key": "dmarc", "label": "DMARC", "type": "TXT",
                "name": self.dmarc_record_name, "value": self.dmarc_value,
                "desc": "Tells receivers what to do with mail that fails the checks.",
                "required": False, "ok": self.dmarc_ok,
            },
        ]

    @property
    def dns_found_count(self) -> int:
        return sum(1 for r in self.dns_records() if r["ok"])

    @property
    def dns_total_count(self) -> int:
        return len(self.dns_records())

    @property
    def dkim_txt_value(self) -> str:
        """The DKIM key as a single DNS-ready TXT value.

        ``amavisd showkeys`` emits BIND format — the record name/TTL/TXT prefix
        plus the key split across quoted chunks inside parentheses. DNS wants
        just the concatenated value (``v=DKIM1; p=...``), so pull out the quoted
        segments and join them; fall back to collapsing whitespace.
        """
        raw = self.dkim_public_key or ""
        chunks = re.findall(r'"([^"]*)"', raw)
        if chunks:
            return "".join(chunks)
        return " ".join(raw.split())

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
    password is never stored here — it is passed to the mail provider at
    provisioning time via the Celery task.
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


class EmailTrackingToken(models.Model):
    """Maps an opaque URL token to a (message, recipient, original_url) triple.

    Django generates the token when rewriting outgoing email links. The token
    is the only secret in the tracking URL — no internal DB IDs are exposed.
    """

    token = models.CharField(max_length=64, unique=True, db_index=True)
    message = models.ForeignKey(
        EmailMessage, on_delete=models.CASCADE, related_name="tracking_tokens"
    )
    recipient = models.EmailField()
    url = models.TextField(blank=True, default="")  # blank = open-pixel token
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self):
        kind = "click" if self.url else "open"
        return f"{kind} token for {self.message_id}"


class EmailTrackingEvent(models.Model):
    """An open or click event recorded when a recipient interacts with an email."""

    class Kind(models.TextChoices):
        OPEN = "open", "Open"
        CLICK = "click", "Click"

    message = models.ForeignKey(
        EmailMessage, on_delete=models.CASCADE, related_name="tracking_events"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    url = models.TextField(blank=True, default="")  # only set for click events
    ip = models.GenericIPAddressField(blank=True, null=True)
    ua = models.CharField(max_length=512, blank=True, default="")
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["message", "kind"]),
            models.Index(fields=["occurred_at"]),
        ]

    def __str__(self):
        return f"{self.kind} on message {self.message_id} at {self.occurred_at}"
