"""Shared business logic for sending a transactional message via the API.

Used by both the versioned apps.api.views.MessageCreateView and the legacy
apps.email.views.api_send shim, so the two entry points can never drift.
"""
from __future__ import annotations

from django.db import transaction

from apps.billing.limits import LimitChecker
from apps.email.models import EmailDomain, EmailMessage
from apps.email.tasks import send_email


class UnverifiedDomainError(Exception):
    """The `from` address's domain isn't a verified sending domain for the account."""

    def __init__(self, domain: str):
        self.domain = domain
        super().__init__(
            f"'{domain}' is not a verified sending domain for this account"
        )


def create_and_queue_message(
    *,
    account,
    from_email: str,
    to_email: str,
    subject: str = "",
    text_body: str = "",
    html_body: str = "",
) -> EmailMessage:
    """Validate, reserve quota, and queue a transactional send.

    Raises UnverifiedDomainError or apps.billing.limits.PlanLimitExceeded on
    rejection — callers translate those into their own response shape.
    """
    from_domain = from_email.rsplit("@", 1)[-1].lower()
    domain = EmailDomain.objects.filter(
        account=account, domain=from_domain, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        raise UnverifiedDomainError(from_domain)

    lc = LimitChecker(account)
    lc.require_feature("email_apis", "the email API & SMTP relay")
    lc.check_email()

    msg = EmailMessage.objects.create(
        account=account,
        domain=domain,
        from_email=from_email,
        to_email=to_email,
        subject=subject,
    )
    transaction.on_commit(
        lambda: send_email.delay(msg.id, text_body=text_body, html_body=html_body)
    )
    return msg
