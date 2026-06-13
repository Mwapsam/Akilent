import logging

import requests

logger = logging.getLogger(__name__)


class BitrixClient:
    """Low-level REST client for the Bitrix24 REST API."""

    def __init__(self, domain: str, access_token: str):
        self.base_url = f"https://{domain}/rest"
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._access_token = access_token

    def call(self, method: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{method}"
        payload = {"auth": self._access_token, **(params or {})}
        response = self._session.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise BitrixAPIError(data.get("error"), data.get("error_description"))
        return data.get("result", data)

    def list(self, method: str, params: dict | None = None) -> list:
        """Paginate through a Bitrix list method and return all items."""
        params = dict(params or {})
        params.setdefault("start", 0)
        items = []
        while True:
            data = self.call(method, params)
            items.extend(data if isinstance(data, list) else [])
            if len(data) < 50:
                break
            params["start"] += 50
        return items


class BitrixAPIError(Exception):
    def __init__(self, code: str, description: str):
        self.code = code
        super().__init__(f"Bitrix24 API error [{code}]: {description}")
