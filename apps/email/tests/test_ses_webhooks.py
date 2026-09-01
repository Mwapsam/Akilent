import pytest
import json
import requests
from unittest.mock import patch, MagicMock
from django.test import TestCase, RequestFactory
from apps.email.ses_webhooks import ses_sns_webhook
from apps.email.models import SuppressionListEntry
from apps.email.models import EmailMessage
from apps.core.models import Account

class SesWebhookTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.account = Account.objects.create(name="Test Account")
        self.email_msg = EmailMessage.objects.create(
            account=self.account,
            provider_message_id="msg-123",
            subject="Test Subject",
            from_email="test@example.com",
            to_email="recipient@example.com"
        )

    def test_ses_sns_webhook_subscription_confirmation(self):
        payload = {
            "Type": "SubscriptionConfirmation",
            "SubscribeURL": "http://example.com/confirm",
            "Signature": "valid-sig",
            "SigningCertUrl": "http://example.com/cert"
        }

        with patch("apps.email.ses_webhooks._verify_sns_signature", return_value=True):
            with patch("requests.get") as mock_get:
                request = self.rf.post("/webhooks/ses/", data=json.dumps(payload), content_type="application/json")
                response = ses_sns_webhook(request)

                self.assertEqual(response.status_code, 200)
                mock_get.assert_called_once_with("http://example.com/confirm", timeout=5)

    def test_ses_sns_webhook_invalid_signature(self):
        payload = {
            "Type": "Notification",
            "Signature": "invalid-sig",
            "SigningCertUrl": "http://example.com/cert"
        }

        with patch("apps.email.ses_webhooks._verify_sns_signature", return_value=False):
            request = self.rf.post("/webhooks/ses/", data=json.dumps(payload), content_type="application/json")
            response = ses_sns_webhook(request)

            self.assertEqual(response.status_code, 403)

    def test_ses_sns_webhook_bounce(self):
        payload = {
            "Type": "Notification",
            "Message": json.dumps({
                "eventType": "Bounce",
                "bounce": {
                    "bounceType": "Permanent",
                    "bouncedRecipients": [{"emailAddress": "recipient@example.com"}]
                },
                "mail": {"messageId": "msg-123"}
            }),
            "Signature": "valid-sig",
            "SigningCertUrl": "http://example.com/cert"
        }

        with patch("apps.email.ses_webhooks._verify_sns_signature", return_value=True):
            request = self.rf.post("/webhooks/ses/", data=json.dumps(payload), content_type="application/json")
            response = ses_sns_webhook(request)

            self.assertEqual(response.status_code, 200)
            suppression = SuppressionListEntry.objects.filter(email="recipient@example.com").first()
            self.assertIsNotNone(suppression)
            self.assertEqual(suppression.reason, SuppressionListEntry.Reason.BOUNCE)

    def test_ses_sns_webhook_complaint(self):
        payload = {
            "Type": "Notification",
            "Message": json.dumps({
                "eventType": "Complaint",
                "complaint": {
                    "complainedRecipients": [{"emailAddress": "recipient@example.com"}]
                },
                "mail": {"messageId": "msg-123"}
            }),
            "Signature": "valid-sig",
            "SigningCertUrl": "http://example.com/cert"
        }

        with patch("apps.email.ses_webhooks._verify_sns_signature", return_value=True):
            request = self.rf.post("/webhooks/ses/", data=json.dumps(payload), content_type="application/json")
            response = ses_sns_webhook(request)

            self.assertEqual(response.status_code, 200)
            suppression = SuppressionListEntry.objects.filter(email="recipient@example.com").first()
            self.assertIsNotNone(suppression)
            self.assertEqual(suppression.reason, SuppressionListEntry.Reason.COMPLAINT)
