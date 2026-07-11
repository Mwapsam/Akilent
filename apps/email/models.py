import hashlib
import re
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class ProvisioningJob(models.Model):
    """Tracks the lifecycle of an async Celery provisioning operation.

    Created before the Celery task is dispatched; updated by the task as it
    runs. Provides operators and tenants with visibility into in-progress or
    failed provisioning work.
    """

    class Status(models.TextChoices):
        PENDING  = "pending",  "Pending"
        RUNNING  = "running",  "Running"
        SUCCESS  = "success",  "Success"
        FAILED   = "failed",   "Failed"
        RETRYING = "retrying", "Retrying"

    class JobType(models.TextChoices):
        PROVISION_DOMAIN  = "provision_domain",  "Provision Domain"
        DEPROVISION_DOMAIN = "deprovision_domain", "Deprovision Domain"
        PROVISION_MAILBOX = "provision_mailbox", "Provision Mailbox"
        DEPROVISION_MAILBOX = "deprovision_mailbox", "Deprovision Mailbox"
        CHANGE_PASSWORD   = "change_password",   "Change Password"
        SET_QUOTA         = "set_quota",         "Set Quota"
        ROTATE_DKIM       = "rotate_dkim",       "Rotate DKIM"
        SUSPEND_MAILBOX   = "suspend_mailbox",   "Suspend Mailbox"
        PROVISION_ALIAS   = "provision_alias",   "Provision Alias"
        DEPROVISION_ALIAS = "deprovision_alias", "Deprovision Alias"

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="provisioning_jobs",
    )
    job_type      = models.CharField(max_length=50, choices=JobType.choices)
    resource_type = models.CharField(max_length=50)    # "domain" | "mailbox" | "alias"
    resource_id   = models.CharField(max_length=255)   # domain name / email / alias address
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error         = models.TextField(blank=True, default="")
    attempts      = models.PositiveSmallIntegerField(default=0)
    metadata      = models.JSONField(default=dict)
    created_at    = models.DateTimeField(auto_now_add=True)
    started_at    = models.DateTimeField(blank=True, null=True)
    completed_at  = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["account", "status"]),
            models.Index(fields=["resource_type", "resource_id"]),
            models.Index(fields=["created_at"]),
        ]

    def mark_running(self) -> None:
        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.attempts += 1
        self.save(update_fields=["status", "started_at", "attempts"])

    def mark_success(self) -> None:
        self.status = self.Status.SUCCESS
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])

    def mark_failed(self, error: str, *, retrying: bool = False) -> None:
        self.status = self.Status.RETRYING if retrying else self.Status.FAILED
        self.error = (error or "")[:5000]
        self.completed_at = timezone.now() if not retrying else None
        update_fields = ["status", "error"]
        if self.completed_at is not None:
            update_fields.append("completed_at")
        self.save(update_fields=update_fields)

    def __str__(self) -> str:
        return f"{self.job_type} {self.resource_id} [{self.status}]"


class AuditLog(models.Model):
    """Immutable record of every mail provider operation.

    Written by apps.email.audit.record() — never written directly.
    Rows are never updated or deleted (use a retention policy at the DB level).
    """

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="email_audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    action        = models.CharField(max_length=100)   # e.g. "domain.create"
    resource_type = models.CharField(max_length=50)    # "domain" | "mailbox" | "alias"
    resource_id   = models.CharField(max_length=255)
    success       = models.BooleanField(default=True)
    error         = models.TextField(blank=True, default="")
    metadata      = models.JSONField(default=dict)
    timestamp     = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["account", "timestamp"]),
            models.Index(fields=["resource_type", "resource_id"]),
        ]

    def __str__(self) -> str:
        status = "OK" if self.success else "FAIL"
        return f"[{status}] {self.action} {self.resource_id} @ {self.timestamp}"


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
    """A per-account key authenticating calls to the transactional send API.

    New keys are ``ak_live_...``, hashed (sha256) before storage — the
    plaintext is only ever available once, at creation, via
    ``create_for_account``. ``key`` is a legacy column: pre-migration keys
    (``ek_...``) were stored in plaintext there and keep authenticating via
    ``authenticate()``'s fallback lookup until customers rotate onto a
    hashed key, at which point ``key`` can be dropped in a follow-up migration.
    """

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="email_api_keys"
    )
    name = models.CharField(max_length=100, default="default")

    # Legacy plaintext column — new keys leave this blank.
    key = models.CharField(max_length=64, unique=True, db_index=True, blank=True, null=True)

    key_hash = models.CharField(max_length=64, unique=True, db_index=True, blank=True, null=True)
    key_prefix = models.CharField(max_length=12, default="ak_live_")
    last4 = models.CharField(max_length=4, blank=True, default="")
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField(blank=True, null=True)
    rate_per_min = models.PositiveIntegerField(blank=True, null=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(blank=True, null=True)

    @staticmethod
    def generate_key() -> str:
        return "ak_live_" + secrets.token_urlsafe(36)

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def create_for_account(cls, account, *, name: str = "default", scopes: list[str] | None = None):
        """Create a new key. Returns (instance, plaintext) — plaintext is shown once."""
        raw = cls.generate_key()
        obj = cls.objects.create(
            account=account,
            name=name,
            key_hash=cls.hash_key(raw),
            key_prefix="ak_live_",
            last4=raw[-4:],
            scopes=scopes or ["messages:send"],
        )
        return obj, raw

    @classmethod
    def authenticate(cls, raw_key: str):
        """Look up an active key by hash, falling back to the legacy plaintext column."""
        if not raw_key:
            return None
        by_hash = cls.objects.filter(
            key_hash=cls.hash_key(raw_key), is_active=True
        ).select_related("account").first()
        if by_hash is not None:
            return by_hash
        return cls.objects.filter(key=raw_key, is_active=True).select_related("account").first()

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
