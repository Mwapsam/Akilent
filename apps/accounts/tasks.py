import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 60  # seconds


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY,
    queue="celery",
)
def send_verification_email(self, user_id: int, site_name: str, link: str) -> None:
    """Render and send the account-verification email in the background.

    Keeping this off the signup request path means an unreachable mail
    server retries via Celery instead of 500ing a signup whose account
    (User/Account/Membership) has already been committed.
    """
    from django.contrib.auth.models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("send_verification_email: user %s not found", user_id)
        return

    ctx = {"user": user, "site_name": site_name, "link": link}
    subject = render_to_string("accounts/verify_email_subject.txt", ctx).strip()
    body = render_to_string("accounts/verify_email.txt", ctx)
    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.exception("send_verification_email: failed for user %s", user_id)
        raise self.retry(exc=exc)
