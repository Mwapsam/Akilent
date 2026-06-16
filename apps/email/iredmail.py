"""Thin client for the iredmail-api REST service.

Provisions a tenant's sending domain + DKIM, and manages mailboxes and aliases
on the iRedMail server. Mirrors the small ``requests`` + timeout style of
``apps.email.mailcow`` (which this replaces).

Auth: log in once with the iRedMail admin credentials at ``POST /api/login`` to
receive a JWT, then send it on every other request via the ``x-api-token``
header. The token is cached in Django's cache and re-fetched on expiry / 401.
"""

import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_TOKEN_CACHE_KEY = "iredmail:jwt"
_TOKEN_TTL = 12 * 60 * 60  # 12h; the API issues longer-lived tokens, re-login on 401.


class IRedMailError(Exception):
    pass


class IRedMailClient:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        self.base_url = (base_url or settings.IREDMAIL_API_BASE or "").rstrip("/")
        self.username = username or settings.IREDMAIL_ADMIN_USER
        self.password = password or settings.IREDMAIL_ADMIN_PASSWORD
        if not self.base_url or not self.username or not self.password:
            raise IRedMailError(
                "IREDMAIL_API_BASE, IREDMAIL_ADMIN_USER and "
                "IREDMAIL_ADMIN_PASSWORD must be configured."
            )

    # --- auth -----------------------------------------------------------

    def _login(self) -> str:
        url = f"{self.base_url}/api/login"
        try:
            resp = requests.post(
                url,
                json={"username": self.username, "password": self.password},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise IRedMailError(f"iRedMail login failed: {exc}") from exc
        token = data.get("token") or data.get("jwt") or data.get("access_token")
        if not token:
            raise IRedMailError("iRedMail login returned no token.")
        cache.set(_TOKEN_CACHE_KEY, token, _TOKEN_TTL)
        return token

    def _token(self, refresh: bool = False) -> str:
        if refresh:
            return self._login()
        return cache.get(_TOKEN_CACHE_KEY) or self._login()

    def _headers(self, token: str) -> dict:
        return {"x-api-token": token, "Content-Type": "application/json"}

    # --- request plumbing ----------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        token = self._token()
        try:
            resp = requests.request(
                method, url, json=payload, headers=self._headers(token), timeout=_TIMEOUT
            )
            # Token expired/invalid — re-login once and retry.
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
                    detail = resp_obj.json().get("error") or resp_obj.text
                except ValueError:
                    detail = resp_obj.text
            raise IRedMailError(f"{method} {path} failed: {detail or exc}") from exc
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

    def _put(self, path: str, payload: dict | None = None) -> dict:
        return self._request("PUT", path, payload)

    def _delete(self, path: str) -> dict:
        return self._request("DELETE", path)

    # --- domains --------------------------------------------------------

    def list_domains(self) -> dict:
        return self._get("domains")

    def add_domain(self, domain: str) -> dict:
        return self._post(f"domain/{domain}")

    def delete_domain(self, domain: str) -> dict:
        return self._delete(f"domain/{domain}")

    def set_domain_status(self, domain: str, active: bool) -> dict:
        return self._put(f"domain/{domain}/status", {"active": bool(active)})

    def get_dkim(self, domain: str) -> dict:
        """Return the DKIM DNS TXT record for a domain.

        The API may return a bare string or a dict; normalise to
        ``{"dkim_txt": <value>}``.
        """
        data = self._get(f"domain/{domain}/dkim")
        if isinstance(data, str):
            return {"dkim_txt": data}
        if isinstance(data, dict):
            value = (
                data.get("dkim_txt")
                or data.get("value")
                or data.get("txt")
                or data.get("record")
                or data.get("dkim")
                or ""
            )
            return {"dkim_txt": value, "selector": data.get("selector")}
        return {"dkim_txt": ""}

    def provision_sending_domain(self, domain: str, selector: str = "dkim") -> dict:
        """Add the domain and return its DKIM record for DNS.

        iRedMail auto-generates the DKIM key on domain creation. Returns the
        same shape the views expect: ``{"dkim_txt": ..., "selector": ...}``.
        """
        self.add_domain(domain)
        dkim = self.get_dkim(domain) or {}
        return {
            "dkim_txt": dkim.get("dkim_txt", ""),
            "selector": dkim.get("selector") or selector,
        }

    # --- mailboxes ------------------------------------------------------

    def list_mailboxes(self, domain: str) -> dict:
        return self._get(f"mailboxes/{domain}")

    def add_mailbox(
        self, email: str, password: str, name: str = "", quota: int | None = None
    ) -> dict:
        payload = {"password": password, "name": name}
        if quota is not None:
            payload["quota"] = quota
        return self._post(f"mailbox/{email}", payload)

    def delete_mailbox(self, email: str) -> dict:
        return self._delete(f"mailbox/{email}")

    def change_password(self, email: str, password: str) -> dict:
        return self._put(f"mailbox/{email}/password", {"password": password})

    def update_quota(self, email: str, quota: int) -> dict:
        return self._put(f"mailbox/{email}/quota", {"quota": quota})

    # --- aliases --------------------------------------------------------

    def add_alias(self, email: str, goto: str) -> dict:
        return self._post(f"alias/{email}", {"goto": goto})
