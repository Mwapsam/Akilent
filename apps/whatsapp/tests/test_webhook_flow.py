"""
Phase 0: Characterization tests for WhatsApp webhook → message logging flow.

These tests lock down the current end-to-end behavior before Phase 1
refactors the internals to add event publishing. They verify:

1. Webhook signature verification (GET handshake, POST HMAC)
2. WebhookEventLog is created with correct event_type and payload
3. Events are correctly classified (message vs status vs invalid)

These are NOT testing task execution or full end-to-end message processing
(that will be tested after Phase 1 fixes existing bugs). They are testing
the critical webhook → log creation path, which Phase 1 will extend by
adding event publishing without changing this current behavior.

These MUST pass before and after Phase 1 — they are the safety net
proving we haven't broken inbound WhatsApp's webhook receipt and logging.
"""
import hashlib
import hmac
import json
from io import BytesIO

from django.http import HttpRequest
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account
from apps.whatsapp.models import WebhookEventLog
from apps.whatsapp.models.tenant import WhatsAppBusinessNumber
from apps.whatsapp.views import WhatsAppWebhookView


def _make_webhook_request(method: str, payload: dict, app_secret: str) -> HttpRequest:
    """Create a Django HttpRequest with proper HMAC signature."""
    body = json.dumps(payload).encode()
    signature = hmac.new(
        key=app_secret.encode(),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    request = HttpRequest()
    request.method = method
    request.path = "/whatsapp/webhook/"
    request._stream = BytesIO(body)
    request.META = {
        "CONTENT_TYPE": "application/json",
        "HTTP_X_HUB_SIGNATURE_256": f"sha256={signature}",
        "REMOTE_ADDR": "1.2.3.4",
    }
    request._body = body
    return request


@override_settings(
    WHATSAPP_VERIFY_TOKEN="test_verify_token",
    WHATSAPP_APP_SECRET="test_app_secret",
)
class WhatsAppWebhookSignatureTest(TestCase):
    """Test webhook signature verification."""

    def test_get_handshake_with_valid_token(self):
        """Test GET handshake verification succeeds with correct token."""
        request = HttpRequest()
        request.method = "GET"
        request.GET = {
            "hub.mode": "subscribe",
            "hub.verify_token": "test_verify_token",
            "hub.challenge": "test_challenge_123",
        }

        view = WhatsAppWebhookView()
        response = view.get(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "test_challenge_123")

    def test_get_handshake_with_invalid_token(self):
        """Test GET handshake verification fails with wrong token."""
        request = HttpRequest()
        request.method = "GET"
        request.GET = {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "test_challenge_123",
        }

        view = WhatsAppWebhookView()
        response = view.get(request)
        self.assertEqual(response.status_code, 403)

    def test_post_with_invalid_signature_rejected(self):
        """Test POST with bad HMAC signature is rejected."""
        payload = {"test": "data"}
        body = json.dumps(payload).encode()

        request = HttpRequest()
        request.method = "POST"
        request._stream = BytesIO(body)
        request.META = {
            "CONTENT_TYPE": "application/json",
            "HTTP_X_HUB_SIGNATURE_256": "sha256=invalidsignature",
            "REMOTE_ADDR": "1.2.3.4",
        }
        request._body = body

        view = WhatsAppWebhookView()
        response = view.post(request)
        self.assertEqual(response.status_code, 403)
        # No WebhookEventLog should be created
        self.assertEqual(WebhookEventLog.objects.count(), 0)


@override_settings(
    WHATSAPP_VERIFY_TOKEN="test_verify_token",
    WHATSAPP_APP_SECRET="test_app_secret",
)
class WhatsAppWebhookLoggingTest(TestCase):
    """Test webhook → WebhookEventLog logging."""

    def setUp(self):
        # Create test account + WhatsApp number (for tenant resolution in tasks)
        self.account = Account.objects.create(
            company_name="Test Co",
            slug="test-co",
        )
        self.number = WhatsAppBusinessNumber.objects.create(
            account=self.account,
            display_number="+1234567890",
            phone_number_id="123456789",
            waba_id="waba_123",
            access_token="test_token_123",
            is_active=True,
        )

    def test_webhook_creates_message_log(self):
        """Test POST webhook creates WebhookEventLog with message event_type."""
        ts = int(timezone.now().timestamp())
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid_123",
                                        "from": "27123456789",
                                        "timestamp": str(ts),
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                                "contacts": [{"profile": {"name": "Test User"}}],
                            }
                        }
                    ]
                }
            ]
        }

        request = _make_webhook_request("POST", payload, "test_app_secret")
        view = WhatsAppWebhookView()
        response = view.post(request)

        self.assertEqual(response.status_code, 200)

        # WebhookEventLog was created with correct attributes
        logs = WebhookEventLog.objects.all()
        self.assertEqual(logs.count(), 1)
        log = logs[0]
        self.assertEqual(log.event_type, "message")
        self.assertEqual(log.source, WebhookEventLog.Source.WHATSAPP)
        self.assertFalse(log.processed)  # Not yet processed by async task
        self.assertEqual(log.payload, payload)

    def test_webhook_creates_status_log(self):
        """Test POST webhook creates WebhookEventLog with status event_type."""
        ts = int(timezone.now().timestamp())
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "123456789"},
                                "statuses": [
                                    {
                                        "id": "wamid_outbound_123",
                                        "status": "delivered",
                                        "timestamp": str(ts),
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        request = _make_webhook_request("POST", payload, "test_app_secret")
        view = WhatsAppWebhookView()
        response = view.post(request)

        self.assertEqual(response.status_code, 200)

        log = WebhookEventLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.event_type, "status")
        self.assertEqual(log.source, WebhookEventLog.Source.WHATSAPP)
        self.assertFalse(log.processed)
        self.assertEqual(log.payload, payload)

    def test_webhook_logs_invalid_json(self):
        """Test invalid JSON in webhook body is logged as invalid_json event."""
        body = b"not valid json{{"
        signature = hmac.new(
            key="test_app_secret".encode(),
            msg=body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        request = HttpRequest()
        request.method = "POST"
        request._stream = BytesIO(body)
        request.META = {
            "CONTENT_TYPE": "application/json",
            "HTTP_X_HUB_SIGNATURE_256": f"sha256={signature}",
            "REMOTE_ADDR": "1.2.3.4",
        }
        request._body = body

        view = WhatsAppWebhookView()
        response = view.post(request)
        self.assertEqual(response.status_code, 200)

        # WebhookEventLog was created with event_type="invalid_json"
        log = WebhookEventLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.event_type, "invalid_json")
        self.assertTrue(log.processed)  # marked as processed (nothing more to do)
        self.assertIn("Body was not valid JSON", log.error_message)

    def test_webhook_logs_unknown_event_type(self):
        """Test webhook with unknown change type is logged."""
        ts = int(timezone.now().timestamp())
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "unknown_field",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "123456789"},
                            },
                        }
                    ]
                }
            ]
        }

        request = _make_webhook_request("POST", payload, "test_app_secret")
        view = WhatsAppWebhookView()
        response = view.post(request)

        self.assertEqual(response.status_code, 200)

        log = WebhookEventLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.event_type, "unknown_field")
        self.assertFalse(log.processed)
        self.assertEqual(log.payload, payload)
