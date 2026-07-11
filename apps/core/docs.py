"""Static developer documentation registry.

Pages are curated, server-rendered guides (bodies live in
``templates/docs/<slug>.html``) covering the public Developer Platform: the
versioned REST API, SMTP relay, and webhooks — plus the auth/error/rate-limit
model shared across all of them. No database, no admin churn, same pattern
as ``apps.core.help``.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DocPage:
    slug: str
    title: str
    summary: str

    @property
    def template(self) -> str:
        return f"docs/{self.slug}.html"


PAGES = [
    DocPage(
        "index",
        "Developer Platform",
        "A versioned REST API, per-domain SMTP relay, and signed webhooks for transactional email.",
    ),
    DocPage(
        "authentication",
        "Authentication",
        "API keys and SMTP relay credentials — creating, rotating, and revoking them.",
    ),
    DocPage(
        "messages",
        "Send a message",
        "POST /api/v1/messages — request, response, and status codes.",
    ),
    DocPage(
        "templates",
        "Templates",
        "Create and manage reusable email templates for /api/v1/templates.",
    ),
    DocPage(
        "campaigns",
        "Campaigns",
        "Send to many recipients at once with POST /api/v1/campaigns.",
    ),
    DocPage(
        "smtp",
        "SMTP relay",
        "Send directly over SMTP using a per-domain relay credential.",
    ),
    DocPage(
        "webhooks",
        "Webhooks",
        "Subscribe to delivery, open, and click events with signed callbacks.",
    ),
    DocPage(
        "errors",
        "Error codes",
        'The {"error": {"code", "message"}} envelope and what each code means.',
    ),
    DocPage(
        "rate-limits",
        "Rate limits",
        "Per-minute request throttling vs. the monthly volume cap — two different layers.",
    ),
    DocPage(
        "changelog",
        "Versioning & changelog",
        "How API versions work, and what's changed since launch.",
    ),
]

_BY_SLUG = {p.slug: p for p in PAGES}


def get_page(slug: str):
    return _BY_SLUG.get(slug)
