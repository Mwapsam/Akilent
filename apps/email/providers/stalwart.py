"""Thin adapter over the Stalwart Mail Server HTTP Management API.

Auth: POST /api/auth with admin credentials → Bearer JWT, cached in Redis for
55 minutes (Stalwart tokens default to 1 hour). Auto-refreshes on 401.

Configuration (settings.py / .env):
    STALWART_API_BASE     — e.g. http://stalwart:8080  (internal only, not public)
    STALWART_ADMIN_USER   — admin account login
    STALWART_ADMIN_PASSWORD
"""

import logging

import requests
from django.conf import settings
from django.core.cache import cache

from .base import DkimResult, MailProvider, MailProviderError, ProvisionResult

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_TOKEN_CACHE_KEY = "stalwart:jwt"
_TOKEN_TTL = 55 * 60  # 55 min; renew before Stalwart's default 1-hour expiry


class StalwartProvider(MailProvider):

    def __init__(self):
        self.base = (getattr(settings, "STALWART_API_BASE", "") or "").rstrip("/")
        self.user = getattr(settings, "STALWART_ADMIN_USER", "")
        self.password = getattr(settings, "STALWART_ADMIN_PASSWORD", "")
        if not self.base or not self.user or not self.password:
            raise MailProviderError(
                "STALWART_API_BASE, STALWART_ADMIN_USER and "
                "STALWART_ADMIN_PASSWORD must be configured."
            )

    # --- auth ------------------------------------------------------------

    def _login(self) -> str:
        url = f"{self.base}/api/auth"
        try:
            resp = requests.post(
                url,
                auth=(self.user, self.password),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise MailProviderError(f"Stalwart login failed: {exc}") from exc
        token = data.get("data") or data.get("token") or data.get("access_token")
        if not token:
            raise MailProviderError("Stalwart login returned no token.")
        cache.set(_TOKEN_CACHE_KEY, token, _TOKEN_TTL)
        return token

    def _token(self, refresh: bool = False) -> str:
        if refresh:
            return self._login()
        return cache.get(_TOKEN_CACHE_KEY) or self._login()

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # --- request plumbing ------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base}/api/{path.lstrip('/')}"
        token = self._token()
        try:
            resp = requests.request(
                method, url, json=payload, headers=self._headers(token), timeout=_TIMEOUT
            )
            if resp.status_code == 401:
                token = self._token(refresh=True)
                resp = requests.request(
                    method, url, json=payload, headers=self._headers(token), timeout=_TIMEOUT
                )
            resp.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            resp_obj = getattr(exc, "response", None)
            if resp_obj is not None:
                try:
                    detail = resp_obj.json().get("detail") or resp_obj.text
                except ValueError:
                    detail = resp_obj.text
            raise MailProviderError(
                f"Stalwart {method} {path} failed: {detail or exc}"
            ) from exc
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, payload: dict | None = None) -> dict:
        return self._request("POST", path, payload)

    def _patch(self, path: str, payload: dict | None = None) -> dict:
        return self._request("PATCH", path, payload)

    def _delete(self, path: str) -> dict:
        return self._request("DELETE", path)

    # --- domain ----------------------------------------------------------

    def provision_domain(self, domain: str, selector: str = "dkim") -> ProvisionResult:
        """Create domain + generate DKIM keypair on Stalwart, return public-key TXT."""
        self._post(f"domain/{domain}")
        self._post("dkim", {
            "domain": domain,
            "selector": selector,
            "algorithm": "rsa-sha256",
        })
        data = self._get(f"domain/{domain}/dkim/{selector}")
        dkim_txt = (
            data.get("data")
            or data.get("record")
            or data.get("txt")
            or data.get("value")
            or ""
        )
        return ProvisionResult(dkim=DkimResult(selector=selector, dkim_txt=dkim_txt))

    def delete_domain(self, domain: str) -> None:
        self._delete(f"domain/{domain}")

    def set_domain_active(self, domain: str, active: bool) -> None:
        self._patch(f"domain/{domain}", {"disabled": not active})

    # --- mailbox ---------------------------------------------------------

    def create_mailbox(
        self,
        email: str,
        password: str,
        name: str = "",
        quota_mb: int | None = None,
    ) -> None:
        local, domain = email.rsplit("@", 1)
        payload: dict = {
            "name": name or local,
            "password": password,
            "email": [email],
            "type": "individual",
        }
        if quota_mb:
            payload["quota"] = quota_mb * 1024 * 1024
        self._post(f"account/{email}", payload)

    def delete_mailbox(self, email: str) -> None:
        self._delete(f"account/{email}")

    def change_password(self, email: str, password: str) -> None:
        self._patch(f"account/{email}", {"password": password})

    def set_quota(self, email: str, quota_mb: int) -> None:
        self._patch(f"account/{email}", {"quota": quota_mb * 1024 * 1024})

    # --- alias -----------------------------------------------------------

    def create_alias(self, address: str, goto: str) -> None:
        self._post(f"alias/{address}", {"addresses": [goto]})
