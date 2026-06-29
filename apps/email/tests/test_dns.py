import pytest
from django.contrib.auth.models import User

from apps.accounts.models import Account, Membership
from apps.email import dnscheck
from apps.email.models import EmailDomain

DKIM_PUBLIC = "v=DKIM1; k=rsa; p=ABCDEFGHIJKLMNOP"
VERIFY_VALUE = "automator-domain-verification=deadbeefcafe1234"


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
        dkim_public_key=DKIM_PUBLIC,
        verify_record_name="mail.acme.com", verify_record_value=VERIFY_VALUE,
    )


def _fake_resolver(mapping):
    def _resolve(name):
        return mapping.get(name, [])
    return _resolve


# --- Model -------------------------------------------------------------------

def test_ensure_verification_token_is_idempotent():
    d = EmailDomain(domain="x.com")
    assert d.ensure_verification_token() is True
    assert d.verify_record_value.startswith("automator-domain-verification=")
    first = d.verify_record_value
    # A second call doesn't churn the token.
    assert d.ensure_verification_token() is False
    assert d.verify_record_value == first


@pytest.mark.django_db
def test_dns_records_spec_and_counts(domain):
    keys = [r["key"] for r in domain.dns_records()]
    assert keys == ["verify", "dkim", "spf", "dmarc"]
    assert domain.dns_total_count == 4
    assert domain.dns_found_count == 0  # nothing verified yet


# --- Checker -----------------------------------------------------------------

@pytest.mark.django_db
def test_check_domain_all_present(domain, monkeypatch, settings):
    settings.EMAIL_HOST = "smtp.relay.com"
    monkeypatch.setattr(dnscheck, "_resolve_txt", _fake_resolver({
        "mail.acme.com": [VERIFY_VALUE, "v=spf1 include:smtp.relay.com ~all"],
        "dkim._domainkey.mail.acme.com": [DKIM_PUBLIC],
        "_dmarc.mail.acme.com": ["v=DMARC1; p=none"],
    }))
    res = dnscheck.check_domain(domain)
    assert res == {"verify": True, "dkim": True, "spf": True, "dmarc": True}


@pytest.mark.django_db
def test_check_domain_missing_records(domain, monkeypatch, settings):
    settings.EMAIL_HOST = "smtp.relay.com"
    # Only the verification TXT is published; everything else absent.
    monkeypatch.setattr(dnscheck, "_resolve_txt", _fake_resolver({
        "mail.acme.com": [VERIFY_VALUE],
    }))
    res = dnscheck.check_domain(domain)
    assert res["verify"] is True
    assert res["dkim"] is False
    assert res["spf"] is False
    assert res["dmarc"] is False


@pytest.mark.django_db
def test_spf_requires_matching_include(domain, monkeypatch, settings):
    settings.EMAIL_HOST = "smtp.relay.com"
    # An SPF record for a *different* host shouldn't count.
    monkeypatch.setattr(dnscheck, "_resolve_txt", _fake_resolver({
        "mail.acme.com": ["v=spf1 include:someone-else.com ~all"],
    }))
    assert dnscheck.check_domain(domain)["spf"] is False


def test_value_comparison_is_whitespace_insensitive():
    # Providers may wrap/space long TXT values; matching must tolerate it.
    assert dnscheck._contains.__module__  # sanity: function exists
    norm = dnscheck._norm
    assert norm("v=DKIM1;  p=AB CD") == "v=dkim1;p=abcd"


# --- Verify view -------------------------------------------------------------

@pytest.mark.django_db
def test_verify_view_marks_domain_verified(client, account, domain, monkeypatch):
    client.force_login(account.owner)
    monkeypatch.setattr(
        "apps.email.dnscheck.check_domain",
        lambda rec: {"verify": True, "dkim": True, "spf": True, "dmarc": True},
    )
    resp = client.post(f"/email/domains/{domain.pk}/verify/")
    assert resp.status_code == 302
    domain.refresh_from_db()
    assert domain.is_verified
    assert domain.dkim_ok and domain.spf_ok and domain.dmarc_ok
    assert domain.last_checked_at is not None


@pytest.mark.django_db
def test_verify_view_stays_pending_without_ownership(client, account, domain, monkeypatch):
    client.force_login(account.owner)
    monkeypatch.setattr(
        "apps.email.dnscheck.check_domain",
        lambda rec: {"verify": False, "dkim": True, "spf": False, "dmarc": False},
    )
    resp = client.post(f"/email/domains/{domain.pk}/verify/")
    assert resp.status_code == 302
    domain.refresh_from_db()
    assert not domain.is_verified
    assert domain.dkim_ok is True  # flags still refresh while pending


@pytest.mark.django_db
def test_create_view_mints_verification_token(client, account, monkeypatch):
    client.force_login(account.owner)

    from apps.email.providers.base import ProvisionResult, DkimResult

    class FakeProvider:
        def provision_domain(self, domain, selector="dkim"):
            return ProvisionResult(dkim=DkimResult(selector=selector, dkim_txt=DKIM_PUBLIC))

    monkeypatch.setattr("apps.email.views.get_mail_provider", lambda: FakeProvider())
    resp = client.post("/email/domains/create/", {"domain": "new.acme.com"})
    assert resp.status_code == 302
    d = EmailDomain.objects.get(domain="new.acme.com")
    assert d.verify_record_value.startswith("automator-domain-verification=")
    assert d.dkim_public_key == DKIM_PUBLIC
