import pytest

try:
    from moto import mock_aws
except ImportError:
    pytest.skip(allow_module_level=True, reason="moto not installed")

import boto3
from django.test import TestCase
from apps.email.providers.ses.provider import SesProvider
from unittest.mock import patch


class SesProviderTests(TestCase):
    def setUp(self):
        # mock_aws must stay active for the whole test, not just setUp —
        # using it as a decorator on setUp() only mocks boto3 while setUp
        # itself runs; every test method would then hit real AWS. Starting
        # it here and stopping via addCleanup keeps it active for the
        # duration of each test.
        self.mock_aws = mock_aws()
        self.mock_aws.start()
        self.addCleanup(self.mock_aws.stop)

        self.ses_client = boto3.client("sesv2", region_name="us-east-1")
        self.provider = SesProvider()

    def test_create_domain(self):
        domain = "example.com"
        result = self.provider.create_domain(domain)
        self.assertTrue(result.success)

        # Verify identity exists in SES as a domain identity (EmailIdentity
        # is the correct sesv2 param name; the identity itself should be
        # the bare domain, not "noreply@domain" — a domain identity is
        # DNS/DKIM-verified, an address identity is link-verified).
        response = self.ses_client.get_email_identity(EmailIdentity=domain)
        self.assertEqual(response["IdentityType"], "DOMAIN")

    def test_create_domain_already_exists(self):
        domain = "example.com"
        self.provider.create_domain(domain)
        # Second call should still be successful
        result = self.provider.create_domain(domain)
        self.assertTrue(result.success)

    def test_verify_domain_success(self):
        domain = "example.com"
        self.provider.create_domain(domain)

        with patch("botocore.client.BaseClient._make_api_call") as mock_call:
            mock_call.return_value = {
                "Attributes": {"VerificationStatus": "SUCCESS"}
            }
            result = self.provider.verify_domain(domain)
            self.assertTrue(result.success)

    def test_verify_domain_failure(self):
        domain = "example.com"
        self.provider.create_domain(domain)

        with patch("botocore.client.BaseClient._make_api_call") as mock_call:
            mock_call.return_value = {
                "Attributes": {"VerificationStatus": "FAILED"}
            }
            result = self.provider.verify_domain(domain)
            self.assertFalse(result.success)

    def test_get_dkim(self):
        domain = "example.com"
        self.provider.create_domain(domain)

        with patch("botocore.client.BaseClient._make_api_call") as mock_call:
            mock_call.return_value = {
                "Attributes": {"DkimTokens": ["token123", "token456", "token789"]}
            }
            dkim = self.provider.get_dkim(domain)
            self.assertIsNotNone(dkim)
            self.assertEqual(dkim.selector, "amazonses")
            self.assertEqual(dkim.public_key, "token123.dkim.amazonses.com")
            self.assertTrue(dkim.is_cname)

    def test_get_dkim_records_returns_all_tokens(self):
        domain = "example.com"
        self.provider.create_domain(domain)

        with patch("botocore.client.BaseClient._make_api_call") as mock_call:
            mock_call.return_value = {
                "Attributes": {"DkimTokens": ["token123", "token456", "token789"]}
            }
            records = self.provider.get_dkim_records(domain)
            self.assertEqual(len(records), 3)
            self.assertEqual(
                [r.public_key for r in records],
                [
                    "token123.dkim.amazonses.com",
                    "token456.dkim.amazonses.com",
                    "token789.dkim.amazonses.com",
                ],
            )

    def test_delete_domain(self):
        domain = "example.com"
        self.provider.create_domain(domain)
        result = self.provider.delete_domain(domain)
        self.assertTrue(result.success)