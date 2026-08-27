"""Meta Cloud API WhatsApp provider implementation.

This is the production provider for sending WhatsApp messages via Meta's
official Cloud API. It handles:
  - Text, template, and media messages
  - Media URL retrieval and download
  - Read receipt marking

Errors are raised as WhatsAppProviderError; adapting is the provider's
responsibility, not the caller's.
"""
import logging

import requests

from apps.whatsapp.providers.base import WhatsAppProvider, WhatsAppProviderError
from apps.whatsapp.types import (
    MediaUrlResult,
    ReadReceiptResult,
    SendResult,
)

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


class MetaCloudAPIProvider(WhatsAppProvider):
    """Meta Cloud API implementation of WhatsAppProvider.

    Sends messages via Meta's official WhatsApp Cloud API.
    """

    def __init__(self, access_token: str, phone_number_id: str):
        """Initialize with Meta API credentials.

        Args:
            access_token: Meta API bearer token.
            phone_number_id: WhatsApp Business Phone Number ID.
        """
        self.phone_number_id = phone_number_id
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        """Build a full Graph API URL from a path."""
        return f"{GRAPH_API_BASE}/{path}"

    def _post_message(self, payload: dict) -> dict:
        """Post a message payload to the Cloud API.

        Returns the raw API response dict.

        Raises:
            WhatsAppProviderError: on network or API error.
        """
        url = self._url(f"{self.phone_number_id}/messages")
        try:
            response = self._session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise WhatsAppProviderError(f"Failed to post message: {e}") from e

    def send_text(self, to: str, body: str) -> SendResult:
        """Send a plain text message."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "text",
            "text": {"body": body},
        }
        try:
            result = self._post_message(payload)
            return SendResult(
                message_id=result["messages"][0]["id"],
                success=True,
            )
        except (KeyError, IndexError, WhatsAppProviderError) as e:
            logger.error(f"Failed to send text message to {to}: {e}")
            return SendResult(
                message_id="",
                success=False,
                error=str(e),
            )

    def send_template(
        self,
        to: str,
        template_name: str,
        language: str,
        components: list,
    ) -> SendResult:
        """Send a pre-approved template message."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components,
            },
        }
        try:
            result = self._post_message(payload)
            return SendResult(
                message_id=result["messages"][0]["id"],
                success=True,
            )
        except (KeyError, IndexError, WhatsAppProviderError) as e:
            logger.error(f"Failed to send template '{template_name}' to {to}: {e}")
            return SendResult(
                message_id="",
                success=False,
                error=str(e),
            )

    def send_media(
        self,
        to: str,
        media_type: str,
        media_id: str,
        caption: str = "",
    ) -> SendResult:
        """Send a media message (image, video, document, audio)."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": media_type,
            media_type: {"id": media_id, "caption": caption},
        }
        try:
            result = self._post_message(payload)
            return SendResult(
                message_id=result["messages"][0]["id"],
                success=True,
            )
        except (KeyError, IndexError, WhatsAppProviderError) as e:
            logger.error(f"Failed to send {media_type} to {to}: {e}")
            return SendResult(
                message_id="",
                success=False,
                error=str(e),
            )

    def get_media_url(self, media_id: str) -> MediaUrlResult:
        """Retrieve the download URL for an uploaded media file."""
        url = self._url(media_id)
        try:
            response = self._session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return MediaUrlResult(
                url=data["url"],
                media_type=data.get("mime_type", "application/octet-stream"),
                size_bytes=data.get("file_size"),
            )
        except requests.RequestException as e:
            raise WhatsAppProviderError(f"Failed to get media URL for {media_id}: {e}") from e

    def download_media(self, media_url: str) -> bytes:
        """Download media from a provider-supplied URL."""
        try:
            response = self._session.get(media_url, timeout=30)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            raise WhatsAppProviderError(f"Failed to download media: {e}") from e

    def mark_as_read(self, message_id: str) -> ReadReceiptResult:
        """Mark an inbound message as read."""
        url = self._url(f"{self.phone_number_id}/messages")
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        try:
            response = self._session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return ReadReceiptResult(success=True)
        except requests.RequestException as e:
            logger.error(f"Failed to mark message {message_id} as read: {e}")
            return ReadReceiptResult(
                success=False,
                error=str(e),
            )
