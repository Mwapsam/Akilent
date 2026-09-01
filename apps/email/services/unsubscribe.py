"""Unsubscribe token generation and management."""
import secrets
from typing import TYPE_CHECKING

from django.urls import reverse

if TYPE_CHECKING:
    from apps.accounts.models import Account
    from apps.email.models import BulkEmailCampaign


def create_unsubscribe_token(
    account: "Account",
    email: str,
    campaign: "BulkEmailCampaign | None" = None,
) -> str:
    """Create an unsubscribe token for a recipient.

    Returns the token (not the URL). Use get_unsubscribe_url() to get the full URL.
    """
    from apps.email.models import UnsubscribeToken

    token = "unsub_" + secrets.token_urlsafe(48)
    UnsubscribeToken.objects.create(
        token=token,
        account=account,
        email=email,
        campaign=campaign,
    )
    return token


def get_unsubscribe_url(request, token: str) -> str:
    """Get the full unsubscribe URL for a token."""
    return request.build_absolute_uri(reverse("email-unsubscribe", args=[token]))


def get_unsubscribe_header(request, token: str) -> str:
    """Get the RFC 8058 List-Unsubscribe header value.

    Returns: <https://example.com/email/t/unsub/token123/>
    This header allows bulk-senders to comply with RFC 8058 and Gmail's
    bulk-sender requirements (2024+).
    """
    url = get_unsubscribe_url(request, token)
    return f"<{url}>"
