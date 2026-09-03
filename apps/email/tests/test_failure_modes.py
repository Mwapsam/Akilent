"""Cross-cutting failure-mode coverage for the SES send/ingest paths.

Reputation is the product's foundation, so the unhappy paths — throttling,
provider outages, replayed / out-of-order SNS events, and blanket suppression
enforcement — are exercised here explicitly.
"""
import json

import pytest
from unittest.mock import patch

from django.test import RequestFactory

from apps.accounts.models import Account
from apps.email.exceptions import EmailProviderError
from apps.email.models import (
    EmailMessage,
    ProcessedSnsMessage,
    SuppressionListEntry,
)
from apps.email.ses_webhooks import ses_sns_webhook

TOPIC = "arn:aws:sns:us-east-1:123456789:test-topic"


def _notification(message: dict, sns_message_id: str = "sns-1") -> dict:
    return {
        "Type": "Notification",
        "MessageId": sns_message_id,
        "TopicArn": TOPIC,
        "Message": json.dumps(message),
        "Signature": "sig",
        "SigningCertUrl": "http://example.com/cert",
    }


def _post(payload: dict):
    rf = RequestFactory()
    req = rf.post(
        "/webhooks/ses/", data=json.dumps(payload), content_type="application/json"
    )
    with patch("apps.email.ses_webhooks._verify_sns_signature", return_value=True), \
         patch("apps.email.ses_webhooks._get_sns_topic_arn_if_allowed", return_value=TOPIC):
        return ses_sns_webhook(req)


@pytest.fixture
def account(db):
    return Account.objects.create(company_name="Acme")


@pytest.fixture
def message(account):
    return EmailMessage.objects.create(
        account=account, provider_message_id="pmid-1",
        from_email="s@acme.com", to_email="r@x.com", subject="Hi",
        status=EmailMessage.Status.SENT,
    )


# --- SNS replay / out-of-order --------------------------------------------------

@pytest.mark.django_db
def test_sns_replay_is_idempotent(account, message):
    payload = _notification(
        {
            "eventType": "Bounce",
            "bounce": {
                "bounceType": "Permanent",
                "bouncedRecipients": [{"emailAddress": "r@x.com"}],
            },
            "mail": {"messageId": "pmid-1"},
        },
        sns_message_id="dup-1",
    )
    assert _post(payload).status_code == 200
    assert _post(payload).status_code == 200  # replay

    assert SuppressionListEntry.objects.filter(email="r@x.com").count() == 1
    assert SuppressionListEntry.objects.get(email="r@x.com").bounce_count == 1
    assert ProcessedSnsMessage.objects.filter(pk="dup-1").count() == 1


@pytest.mark.django_db
def test_replay_survives_cache_flush(account, message):
    from django.core.cache import cache

    payload = _notification(
        {
            "eventType": "Complaint",
            "complaint": {"complainedRecipients": [{"emailAddress": "r@x.com"}]},
            "mail": {"messageId": "pmid-1"},
        },
        sns_message_id="dup-2",
    )
    assert _post(payload).status_code == 200
    cache.clear()  # cache fast-path gone; DB ledger must still stop the replay
    assert _post(payload).status_code == 200
    assert SuppressionListEntry.objects.filter(email="r@x.com").count() == 1


@pytest.mark.django_db
def test_delivery_after_bounce_does_not_resurrect(account, message):
    message.status = EmailMessage.Status.FAILED
    message.save(update_fields=["status"])

    _post(_notification(
        {"eventType": "Delivery", "mail": {"messageId": "pmid-1", "destination": ["r@x.com"]}},
        sns_message_id="del-1",
    ))
    message.refresh_from_db()
    assert message.status == EmailMessage.Status.FAILED


@pytest.mark.django_db
def test_reject_marks_message_failed(account, message):
    _post(_notification(
        {"eventType": "Reject", "reject": {"reason": "Bad content"},
         "mail": {"messageId": "pmid-1"}},
        sns_message_id="rej-1",
    ))
    message.refresh_from_db()
    assert message.status == EmailMessage.Status.FAILED
    assert "rejected" in message.error.lower()


@pytest.mark.django_db
def test_delivery_delay_is_acknowledged_without_state_change(account, message):
    resp = _post(_notification(
        {"eventType": "DeliveryDelay", "mail": {"messageId": "pmid-1"}},
        sns_message_id="dly-1",
    ))
    assert resp.status_code == 200
    message.refresh_from_db()
    assert message.status == EmailMessage.Status.SENT


# --- Provider outage / throttling --------------------------------------------

@pytest.mark.django_db
def test_provider_outage_retries_and_keeps_message(account, monkeypatch):
    msg = EmailMessage.objects.create(
        account=account, from_email="s@acme.com", to_email="r@x.com", subject="Hi",
    )
    monkeypatch.setattr(
        "apps.email.services.suppression.is_suppressed", lambda a, e: False
    )

    class _Down:
        def send(self, outbound):
            raise EmailProviderError("EndpointConnectionError: could not connect")

    monkeypatch.setattr("apps.email.tasks.get_send_provider", lambda: _Down())

    from apps.email.tasks import _send_email_message

    retried = {}

    class _Task:
        class request:
            retries = 0

        def retry(self, exc=None, countdown=None):
            retried["hit"] = True
            raise RuntimeError("retry")

    with pytest.raises(RuntimeError):
        _send_email_message(_Task(), msg, "t", "")

    assert retried.get("hit") is True
    msg.refresh_from_db()
    assert msg.status == EmailMessage.Status.FAILED  # marked, not deleted
    assert EmailMessage.objects.filter(pk=msg.pk).exists()


@pytest.mark.django_db
def test_retry_exhaustion_pages_operators(account, monkeypatch):
    msg = EmailMessage.objects.create(
        account=account, from_email="s@acme.com", to_email="r@x.com", subject="Hi",
    )
    monkeypatch.setattr(
        "apps.email.services.suppression.is_suppressed", lambda a, e: False
    )
    monkeypatch.setattr(
        "apps.email.tasks.get_send_provider",
        lambda: type("P", (), {"send": lambda s, o: (_ for _ in ()).throw(EmailProviderError("boom"))})(),
    )
    alerts = []
    monkeypatch.setattr("apps.billing.slack.post_message", lambda t: alerts.append(t))

    from apps.email.tasks import _send_email_message, _MAX_RETRIES

    class _Task:
        class request:
            retries = _MAX_RETRIES  # last attempt

        def retry(self, exc=None, countdown=None):
            raise RuntimeError("retry")

    with pytest.raises(RuntimeError):
        _send_email_message(_Task(), msg, "t", "")

    assert len(alerts) == 1
    assert "after" in alerts[0].lower()


# --- Suppression enforcement is total ---------------------------------------

@pytest.mark.django_db
def test_every_send_path_refuses_a_suppressed_address(account, monkeypatch):
    SuppressionListEntry.objects.create(
        account=account, email="blocked@x.com",
        reason=SuppressionListEntry.Reason.BOUNCE,
    )

    # 1) task-level shared send body
    from apps.email.tasks import _send_email_message

    msg = EmailMessage.objects.create(
        account=account, from_email="s@acme.com", to_email="blocked@x.com", subject="Hi",
    )
    sent = []
    monkeypatch.setattr(
        "apps.email.tasks.get_send_provider",
        lambda: type("P", (), {"send": lambda s, o: sent.append(o)})(),
    )

    class _Task:
        class request:
            retries = 0

    _send_email_message(_Task(), msg, "t", "")
    msg.refresh_from_db()
    assert msg.status == EmailMessage.Status.FAILED
    assert sent == []

    # 2) system email path (global suppression)
    from apps.email.services.suppression import is_suppressed_globally
    assert is_suppressed_globally("blocked@x.com") is True

    from apps.email.services import send as send_mod
    provider_calls = []
    monkeypatch.setattr(
        send_mod, "get_send_provider",
        lambda: type("P", (), {"send": lambda s, o: provider_calls.append(o)})(),
        raising=False,
    )
    send_mod.send_system_email("blocked@x.com", "S", "body")
    assert provider_calls == []
