"""Tests for apps.email.verification.refresh_domain and the reverify task."""
import pytest
from django.contrib.auth.models import User

from apps.accounts.models import Account, Membership
from apps.email import dnscheck
from apps.email.models import EmailDomain
from apps.email.tasks import reverify_pending_domains
from apps.email.verification import refresh_domain

VERIFY_VALUE = "automator-domain-verification=abcd1234"


@pytest.fixture
def account(db):
    user = User.objects.create_user("owner", "owner@example.com", "pw")
    acc = Account.objects.create(company_name="Acme")
    Membership.objects.create(user=user, account=acc, role=Membership.Role.OWNER)
    return acc


@pytest.fixture
def domain(account):
    return EmailDomain.objects.create(
        account=account, domain="mail.acme.com",
        dkim_public_key="v=DKIM1; k=rsa; p=ABCDEF",
        verify_record_name="mail.acme.com", verify_record_value=VERIFY_VALUE,
    )


def _rows(result_by_key):
    def _inner(record):
        return [
            {**row, "ok": result_by_key.get(row["key"], False)}
            for row in record.dns_records()
        ]
    return _inner


@pytest.mark.django_db
def test_refresh_domain_transitions_pending_to_verified(domain, monkeypatch):
    monkeypatch.setattr(dnscheck, "check_records",
                        _rows({"verify": True, "dkim": True, "spf": True, "dmarc": True}))
    out = refresh_domain(domain)
    assert out == {"verify": True, "dkim": True, "spf": True, "dmarc": True}
    domain.refresh_from_db()
    assert domain.is_verified
    assert domain.verified_at is not None


@pytest.mark.django_db
def test_refresh_domain_stays_pending_without_ownership(domain, monkeypatch):
    monkeypatch.setattr(dnscheck, "check_records",
                        _rows({"verify": False, "dkim": True, "spf": False, "dmarc": False}))
    refresh_domain(domain)
    domain.refresh_from_db()
    assert not domain.is_verified
    assert domain.dkim_ok is True


@pytest.mark.django_db
def test_refresh_domain_ses_needs_provider_success(domain, monkeypatch):
    monkeypatch.setattr(EmailDomain, "is_ses_backed", lambda self: True)
    monkeypatch.setattr(dnscheck, "check_records",
                        _rows({"verify": True, "dkim": True, "spf": True, "dmarc": True}))

    # DNS is all green but SES itself hasn't flipped to SUCCESS yet.
    monkeypatch.setattr("apps.email.verification._ses_identity_verified",
                        lambda rec, prov: False)
    refresh_domain(domain)
    domain.refresh_from_db()
    assert not domain.is_verified

    # SES now reports SUCCESS -> verified.
    monkeypatch.setattr("apps.email.verification._ses_identity_verified",
                        lambda rec, prov: True)
    refresh_domain(domain)
    domain.refresh_from_db()
    assert domain.is_verified


@pytest.mark.django_db
def test_reverify_pending_domains_only_touches_pending(account, monkeypatch):
    pending = EmailDomain.objects.create(
        account=account, domain="a.acme.com",
        verify_record_name="a.acme.com", verify_record_value=VERIFY_VALUE,
    )
    done = EmailDomain.objects.create(
        account=account, domain="b.acme.com", status=EmailDomain.Status.VERIFIED,
        verify_record_name="b.acme.com", verify_record_value=VERIFY_VALUE,
    )

    seen = []
    def _fake_refresh(record, **kw):
        seen.append(record.domain)
        record.status = EmailDomain.Status.VERIFIED
        record.save(update_fields=["status"])
        return {}
    monkeypatch.setattr("apps.email.verification.refresh_domain", _fake_refresh)

    verified = reverify_pending_domains()
    assert seen == ["a.acme.com"]
    assert verified == 1
