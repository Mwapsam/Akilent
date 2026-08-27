import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Account(models.Model):
    company_name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Per-account token for the Progstack domain-verification API.
    progstack_token = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["company_name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug(self.company_name or "account")
        super().save(*args, **kwargs)

    @staticmethod
    def _unique_slug(base: str) -> str:
        root = slugify(base) or "account"
        slug = root
        i = 2
        while Account.objects.filter(slug=slug).exists():
            slug = f"{root}-{i}"
            i += 1
        return slug

    @property
    def owner(self):
        membership = self.memberships.filter(role=Membership.Role.OWNER).first()
        return membership.user if membership else None

    def __str__(self):
        return self.company_name


class Membership(models.Model):
    """Links a Django ``User`` to an ``Account`` with a role.

    Allows a tenant to have multiple users and lets us resolve the "current
    account" for a logged-in user.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="memberships"
    )
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.OWNER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "account")
        indexes = [
            models.Index(fields=["user", "account"]),
        ]

    def __str__(self):
        return f"{self.user} -> {self.account} ({self.role})"


class Invitation(models.Model):
    """A pending invitation for someone to join an ``Account`` with a role.

    Sent by email with a tokened accept link. Accepting either signs the
    recipient into an existing account (matched by email) or lets them create
    one, then creates the corresponding ``Membership``. Owners can never be
    invited — there is exactly one owner (the account creator).
    """

    EXPIRY_DAYS = 7

    # Owner is intentionally excluded — you can only invite admins/members.
    INVITE_ROLES = [
        (Membership.Role.MEMBER, Membership.Role.MEMBER.label),
        (Membership.Role.ADMIN, Membership.Role.ADMIN.label),
    ]

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="invitations"
    )
    email = models.EmailField()
    role = models.CharField(
        max_length=20, choices=INVITE_ROLES, default=Membership.Role.MEMBER
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    invited_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sent_invitations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # At most one *pending* invite per email per account; an accepted
            # one may coexist (e.g. re-inviting someone after removal).
            models.UniqueConstraint(
                fields=["account", "email"],
                condition=models.Q(accepted_at__isnull=True),
                name="uniq_pending_invite_per_account_email",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None

    @property
    def expires_at(self):
        return self.created_at + timedelta(days=self.EXPIRY_DAYS) if self.created_at else None

    @property
    def is_expired(self) -> bool:
        return bool(self.created_at) and timezone.now() > self.expires_at

    def __str__(self):
        return f"invite {self.email} -> {self.account} ({self.role})"
