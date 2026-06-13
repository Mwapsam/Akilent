import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


class WhatsAppClient:
    """Thin wrapper around the Meta WhatsApp Cloud API."""

    def __init__(self, access_token: str, phone_number_id: str):
        self.phone_number_id = phone_number_id
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{GRAPH_API_BASE}/{path}"

    def send_text(self, to: str, body: str) -> dict:
        payload = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "text",
            "text": {"body": body},
        }
        return self._post_message(payload)

    def send_template(self, to: str, template_name: str, language: str, components: list) -> dict:
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
        return self._post_message(payload)

    def send_media(self, to: str, media_type: str, media_id: str, caption: str = "") -> dict:
        payload = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": media_type,
            media_type: {"id": media_id, "caption": caption},
        }
        return self._post_message(payload)

    def _post_message(self, payload: dict) -> dict:
        url = self._url(f"{self.phone_number_id}/messages")
        response = self._session.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_media_url(self, media_id: str) -> str:
        url = self._url(media_id)
        response = self._session.get(url, timeout=10)
        response.raise_for_status()
        return response.json()["url"]

    def download_media(self, media_url: str) -> bytes:
        response = self._session.get(media_url, timeout=30)
        response.raise_for_status()
        return response.content

    def mark_as_read(self, message_id: str) -> None:
        url = self._url(f"{self.phone_number_id}/messages")
        self._session.post(url, json={
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }, timeout=10)
