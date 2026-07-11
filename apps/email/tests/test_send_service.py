import pytest
from django.core import mail

from apps.email.services import smtp_send


@pytest.mark.django_db
def test_smtp_send_delivers_text_and_html():
    message_id = smtp_send(
        from_email="hello@mail.acme.com",
        to_email="recipient@example.com",
        subject="Hi there",
        text_body="Plain text body",
        html_body="<p>Rich body</p>",
    )
    assert isinstance(message_id, str)
    assert len(mail.outbox) == 1

    sent = mail.outbox[0]
    assert sent.from_email == "hello@mail.acme.com"
    assert sent.to == ["recipient@example.com"]
    assert sent.subject == "Hi there"
    assert sent.body == "Plain text body"
    # html_body is attached as an alternative, not the primary body.
    alt_bodies = [content for content, mimetype in sent.alternatives]
    assert "<p>Rich body</p>" in alt_bodies


@pytest.mark.django_db
def test_smtp_send_without_html_sends_text_only():
    smtp_send(
        from_email="hello@mail.acme.com",
        to_email="recipient@example.com",
        subject="Plain",
        text_body="Just text",
    )
    sent = mail.outbox[0]
    assert sent.alternatives == []


@pytest.mark.django_db
def test_smtp_send_uses_configured_relay_settings(settings, monkeypatch):
    captured = {}
    from apps.email.services import send as send_module

    real_get_connection = send_module.get_connection

    def _spy_get_connection(**kwargs):
        captured.update(kwargs)
        return real_get_connection(**kwargs)

    monkeypatch.setattr(send_module, "get_connection", _spy_get_connection)
    settings.EMAIL_HOST = "relay.example.com"
    settings.EMAIL_PORT = 587
    settings.EMAIL_HOST_USER = "relay-user"

    smtp_send(
        from_email="hello@mail.acme.com",
        to_email="recipient@example.com",
        subject="Hi",
        text_body="Body",
    )
    assert captured["host"] == "relay.example.com"
    assert captured["port"] == 587
    assert captured["username"] == "relay-user"
