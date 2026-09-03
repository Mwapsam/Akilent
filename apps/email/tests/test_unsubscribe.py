"""List-Unsubscribe (RFC 8058) wiring for bulk sends."""
import pytest
from django.contrib.auth.models import User

from apps.accounts.models import Account, Membership
from apps.email.models import (
    BulkEmailCampaign,
    EmailDomain,
    EmailMessage,
    UnsubscribeToken,
)
from apps.email.services.unsubscribe import (
    build_list_unsubscribe_headers,
    create_unsubscribe_token,
    get_unsubscribe_url,
)


@pytest.fixture
def account(db):
    user = User.objects.create_user("owner", "owner@example.com", "pw")
    acc = Account.objects.create(company_name="Acme")
    Membership.objects.create(user=user, account=acc, role=Membership.Role.OWNER)
    return acc


@pytest.mark.django_db
def test_token_fits_column_and_is_unique(account):
    t1 = create_unsubscribe_token(account, "a@x.com")
    t2 = create_unsubscribe_token(account, "a@x.com")
    assert t1 != t2
    assert len(t1) <= 64
    assert UnsubscribeToken.objects.filter(token=t1, is_used=False).exists()


@pytest.mark.django_db
def test_build_headers_shape(account, settings):
    settings.DEFAULT_FROM_EMAIL = "no-reply@acme.com"
    settings.BASE_DOMAIN = "app.acme.com"
    settings.DEBUG = False
    headers = build_list_unsubscribe_headers(account, "rcpt@x.com")

    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    lu = headers["List-Unsubscribe"]
    assert lu.startswith("<https://app.acme.com/email/t/unsub/")
    assert "<mailto:no-reply@acme.com?subject=unsubscribe>" in lu

    token = UnsubscribeToken.objects.filter(email="rcpt@x.com").get().token
    assert token in lu


@pytest.mark.django_db
def test_get_unsubscribe_url_without_request(account, settings):
    settings.BASE_DOMAIN = "app.acme.com"
    settings.DEBUG = False
    url = get_unsubscribe_url("unsub_abc")
    assert url == "https://app.acme.com/email/t/unsub/unsub_abc/"


@pytest.mark.django_db
def test_send_path_attaches_headers_for_campaign(account, monkeypatch):
    domain = EmailDomain.objects.create(
        account=account, domain="acme.com", status=EmailDomain.Status.VERIFIED
    )
    campaign = BulkEmailCampaign.objects.create(
        account=account, domain=domain,
        from_email="news@acme.com", subject_override="Hi",
    )
    msg = EmailMessage.objects.create(
        account=account, domain=domain, campaign=campaign,
        from_email="news@acme.com", to_email="rcpt@x.com", subject="Hi",
    )

    captured = {}

    class _Provider:
        def send(self, outbound):
            captured["headers"] = dict(outbound.headers)
            from apps.email.types import SendResult
            return SendResult(success=True, provider_message_id="mid-1")

    monkeypatch.setattr("apps.email.tasks.get_send_provider", lambda: _Provider())
    monkeypatch.setattr(
        "apps.email.services.suppression.is_suppressed", lambda acc, em: False
    )

    from apps.email.tasks import _send_email_message

    class _Task:
        class request:
            retries = 0

    _send_email_message(_Task, msg, "text", "<p>html</p>")

    assert captured["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "List-Unsubscribe" in captured["headers"]
    assert UnsubscribeToken.objects.filter(email="rcpt@x.com", campaign=campaign).exists()


@pytest.mark.django_db
def test_send_path_no_headers_for_transactional(account, monkeypatch):
    msg = EmailMessage.objects.create(
        account=account, from_email="app@acme.com", to_email="rcpt@x.com", subject="Hi",
    )
    captured = {}

    class _Provider:
        def send(self, outbound):
            captured["headers"] = dict(outbound.headers)
            from apps.email.types import SendResult
            return SendResult(success=True, provider_message_id="mid-2")

    monkeypatch.setattr("apps.email.tasks.get_send_provider", lambda: _Provider())
    monkeypatch.setattr(
        "apps.email.services.suppression.is_suppressed", lambda acc, em: False
    )

    from apps.email.tasks import _send_email_message

    class _Task:
        class request:
            retries = 0

    _send_email_message(_Task, msg, "text", "")
    assert captured["headers"] == {}


@pytest.mark.django_db
def test_one_click_post_unsubscribes(client, account):
    token = create_unsubscribe_token(account, "gone@x.com")
    resp = client.post(
        f"/email/t/unsub/{token}/",
        data="List-Unsubscribe=One-Click",
        content_type="application/x-www-form-urlencoded",
    )
    assert resp.status_code == 200
    UnsubscribeToken.objects.get(token=token, is_used=True)

    from apps.email.services.suppression import is_suppressed
    assert is_suppressed(account, "gone@x.com")
