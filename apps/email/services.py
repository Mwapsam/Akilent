"""Transactional email sending and self-hosted open/click tracking.

Tracking is fully Django-native — no external API call. Each outgoing link is
rewritten to a Django redirect endpoint; an open-pixel is injected before
</body>. Both resolve through EmailTrackingToken rows, which are minted here
and consumed by the tracking views (apps.email.views.tracking_open / click).
"""

import logging
import re
import secrets

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

logger = logging.getLogger(__name__)

_HREF_RE = re.compile(r'href="(https?://[^"]+)"', re.IGNORECASE)
_MAX_TRACKED_LINKS = 25


def _tracking_base() -> str:
    base_domain = getattr(settings, "BASE_DOMAIN", "") or settings.ALLOWED_HOSTS[0]
    scheme = "http" if settings.DEBUG else "https"
    return f"{scheme}://{base_domain}"


def _mint_token(message, recipient: str, url: str = "") -> str:
    from apps.email.models import EmailTrackingToken
    token = secrets.token_urlsafe(32)
    EmailTrackingToken.objects.create(
        token=token,
        message=message,
        recipient=recipient,
        url=url,
    )
    return token


def apply_tracking(html_body: str, message, recipient: str, domain: str) -> str:
    """Rewrite outgoing HTML to inject open-pixel and click-tracking URLs.

    `message` must be an EmailMessage ORM instance (not a raw ID).
    Best-effort: returns the original HTML unchanged on any failure so tracking
    never blocks delivery.
    """
    if not html_body:
        return html_body
    try:
        base = _tracking_base()

        open_token = _mint_token(message, recipient, url="")
        open_url = f"{base}/email/t/open/{open_token}/"

        seen: dict[str, str] = {}
        count = 0

        def _rewrite(match: re.Match) -> str:
            nonlocal count
            url = match.group(1)
            if count >= _MAX_TRACKED_LINKS:
                return match.group(0)
            if url not in seen:
                click_token = _mint_token(message, recipient, url=url)
                seen[url] = f"{base}/email/t/click/{click_token}/"
                count += 1
            return f'href="{seen[url]}"'

        html = _HREF_RE.sub(_rewrite, html_body)
        pixel = (
            f'<img src="{open_url}" width="1" height="1" alt="" style="display:none">'
        )
        idx = html.lower().rfind("</body>")
        return html[:idx] + pixel + html[idx:] if idx != -1 else html + pixel
    except Exception:
        logger.exception("apply_tracking: unexpected error; sending untracked")
        return html_body


def smtp_send(
    from_email: str,
    to_email: str,
    subject: str,
    text_body: str = "",
    html_body: str = "",
) -> str:
    """Send one email through the configured SMTP relay (Stalwart submission port).

    Returns the local Message-ID. Raises on failure (caller logs/marks failed).
    """
    connection = get_connection(
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        username=settings.EMAIL_HOST_USER,
        password=settings.EMAIL_HOST_PASSWORD,
        use_tls=settings.EMAIL_USE_TLS,
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body or " ",
        from_email=from_email,
        to=[to_email],
        connection=connection,
    )
    if html_body:
        message.attach_alternative(html_body, "text/html")

    message.send(fail_silently=False)
    return message.extra_headers.get("Message-ID", "")
