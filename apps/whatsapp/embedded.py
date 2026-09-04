"""WhatsApp Embedded Signup token exchange (Tech Provider onboarding).

After the customer completes Meta's Embedded Signup popup, the front end has:
  - an OAuth ``code`` (from FB.login),
  - the ``phone_number_id`` and ``waba_id`` (from the WA_EMBEDDED_SIGNUP message).

We exchange the code for a business-integration access token and (best-effort)
subscribe our app to the WABA so webhooks flow.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"
_TIMEOUT = 15


class EmbeddedSignupError(Exception):
    pass


def _version() -> str:
    return getattr(settings, "WHATSAPP_GRAPH_VERSION", "v21.0")


def exchange_code_for_token(code: str) -> str:
    """Exchange the Embedded Signup auth code for an access token."""
    if not settings.WHATSAPP_APP_ID or not settings.WHATSAPP_APP_SECRET:
        raise EmbeddedSignupError(
            "WHATSAPP_APP_ID and WHATSAPP_APP_SECRET must be configured."
        )
    resp = requests.get(
        f"{GRAPH}/{_version()}/oauth/access_token",
        params={
            "client_id": settings.WHATSAPP_APP_ID,
            "client_secret": settings.WHATSAPP_APP_SECRET,
            "code": code,
        },
        timeout=_TIMEOUT,
    )
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or "access_token" not in data:
        raise EmbeddedSignupError(
            (data.get("error") or {}).get("message")
            or f"Token exchange failed ({resp.status_code})"
        )
    return data["access_token"]


def subscribe_app_to_waba(waba_id: str, access_token: str) -> None:
    """Subscribe our app to the WABA so message webhooks are delivered."""
    if not waba_id:
        return
    resp = requests.post(
        f"{GRAPH}/{_version()}/{waba_id}/subscribed_apps",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        logger.warning(
            "subscribe_app_to_waba: failed for waba=%s (%s): %s",
            waba_id, resp.status_code, resp.text[:300],
        )


def register_phone_number(phone_number_id: str, access_token: str, pin: str) -> None:
    """Register a number on the Cloud API — required before it can send.

    Meta's Tech Provider Embedded Signup hands us a WABA + phone number, but the
    number is not usable on the Cloud API until it is registered (otherwise sends
    fail with error 133010). This call also sets the two-step verification PIN
    for numbers that don't have one yet.

    Raises:
        EmbeddedSignupError: if registration is rejected.
    """
    resp = requests.post(
        f"{GRAPH}/{_version()}/{phone_number_id}/register",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"messaging_product": "whatsapp", "pin": pin},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        data = resp.json() if resp.content else {}
        raise EmbeddedSignupError(
            (data.get("error") or {}).get("message")
            or f"Phone number registration failed ({resp.status_code})"
        )
