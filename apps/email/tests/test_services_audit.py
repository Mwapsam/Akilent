from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.accounts.models import Account, Membership
from apps.billing.models import Plan, Subscription
from apps.email.models import AuditLog, EmailAlias, EmailDomain, Mailbox
from apps.email.types import AliasInfo, OperationResult


@pytest.fixture
def account(db):
    user = User.objects.create_user("owner", "owner@example.com", "pw")
    acc = Account.objects.create(company_name="Acme")
    Membership.objects.create(user=user, account=acc, role=Membership.Role.OWNER)
    plan = Plan.objects.create(
        slug="p", name="P", price_monthly=Decimal("10"), max_aliases=10,
    )
    Subscription.objects.create(
        account=acc, plan=plan, status=Subscription.ACTIVE,
        current_period_start=timezone.now(),
    )
    return acc


@pytest.fixture
def domain(account):
    return EmailDomain.objects.create(
        account=account, domain="mail.acme.com", status=EmailDomain.Status.VERIFIED,
    )


@pytest.fixture
def mailbox(account, domain):
    return Mailbox.objects.create(
        account=account, domain=domain, email="user@mail.acme.com",
        status=Mailbox.Status.ACTIVE,
    )


class _FakeProvider:
    """Duck-typed stand-in for EmailProvider — no HTTP calls."""

    def set_domain_active(self, domain, *, active):
        return OperationResult(success=True)

    def delete_domain(self, domain):
        return OperationResult(success=True)

    def delete_mailbox(self, email):
        return OperationResult(success=True)

    def change_password(self, email, new_password):
        return OperationResult(success=True)

    def set_quota(self, email, quota_mb):
        return OperationResult(success=True)

    def create_alias(self, address, targets, *, description=""):
        return AliasInfo(address=address, targets=targets)


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch):
    fake = _FakeProvider()
    monkeypatch.setattr("apps.email.services.domain.get_mail_provider", lambda: fake)
    monkeypatch.setattr("apps.email.services.mailbox.get_mail_provider", lambda: fake)
    monkeypatch.setattr("apps.email.services.alias.get_mail_provider", lambda: fake)
    return fake


def _last_audit(account, action):
    return AuditLog.objects.filter(account=account, action=action).order_by("-timestamp").first()


@pytest.mark.django_db
def test_domain_toggle_writes_audit_log(client, account, domain):
    client.force_login(account.owner)
    resp = client.post(f"/email/domains/{domain.pk}/toggle/")
    assert resp.status_code == 302
    log = _last_audit(account, "domain.disable")
    assert log is not None
    assert log.resource_id == domain.domain
    assert log.actor_id == account.owner.pk


@pytest.mark.django_db
def test_domain_delete_writes_audit_log(client, account, domain):
    client.force_login(account.owner)
    resp = client.post(f"/email/domains/{domain.pk}/delete/")
    assert resp.status_code == 302
    log = _last_audit(account, "domain.deprovision")
    assert log is not None
    assert log.resource_id == domain.domain


@pytest.mark.django_db
def test_mailbox_delete_writes_audit_log(client, account, mailbox):
    client.force_login(account.owner)
    resp = client.post(f"/email/mailboxes/{mailbox.pk}/delete/")
    assert resp.status_code == 302
    log = _last_audit(account, "mailbox.deprovision")
    assert log is not None
    assert log.resource_id == mailbox.email


@pytest.mark.django_db
def test_mailbox_password_writes_audit_log(client, account, mailbox):
    client.force_login(account.owner)
    resp = client.post(f"/email/mailboxes/{mailbox.pk}/password/", {"password": "n3wPassw0rd!"})
    assert resp.status_code == 302
    log = _last_audit(account, "mailbox.change_password")
    assert log is not None


@pytest.mark.django_db
def test_mailbox_quota_writes_audit_log_and_updates_model(client, account, mailbox):
    client.force_login(account.owner)
    resp = client.post(f"/email/mailboxes/{mailbox.pk}/quota/", {"quota_mb": "2048"})
    assert resp.status_code == 302
    mailbox.refresh_from_db()
    assert mailbox.quota_mb == 2048
    log = _last_audit(account, "mailbox.set_quota")
    assert log is not None
    assert log.metadata.get("quota_mb") == 2048


@pytest.mark.django_db
def test_alias_create_writes_audit_log(client, account, domain):
    client.force_login(account.owner)
    resp = client.post(
        "/email/aliases/create/",
        {"address": "sales@mail.acme.com", "goto": "team@example.com"},
    )
    assert resp.status_code == 302
    assert EmailAlias.objects.filter(address="sales@mail.acme.com").exists()
    log = _last_audit(account, "alias.provision")
    assert log is not None
    assert log.resource_id == "sales@mail.acme.com"
