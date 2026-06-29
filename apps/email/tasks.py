import logging

from celery import shared_task

from apps.email.models import EmailMessage, Mailbox
from apps.email.providers import MailProviderError, get_mail_provider
from apps.email.services import smtp_send

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


@shared_task
def prune_email_logs():
    """Delete EmailMessage logs older than each account's plan retention window.

    Enforces the per-plan "{N} days log retention" feature. Runs daily.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.billing.models import Subscription

    total = 0
    subs = Subscription.objects.select_related("plan").filter(
        status__in=[Subscription.ACTIVE, Subscription.TRIALING]
    )
    for sub in subs:
        days = getattr(sub.plan, "log_retention_days", 0) or 0
        if days <= 0:
            continue
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = EmailMessage.objects.filter(
            account_id=sub.account_id, created_at__lt=cutoff
        ).delete()
        total += deleted
    logger.info("prune_email_logs: deleted %s expired log rows", total)
    return total


@shared_task
def prune_tracking_tokens():
    """Delete stale EmailTrackingToken rows older than 90 days. Runs daily."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.email.models import EmailTrackingToken

    cutoff = timezone.now() - timedelta(days=90)
    deleted, _ = EmailTrackingToken.objects.filter(created_at__lt=cutoff).delete()
    logger.info("prune_tracking_tokens: deleted %s stale tokens", deleted)
    return deleted


@shared_task(bind=True, max_retries=_MAX_ATTEMPTS, default_retry_delay=60)
def provision_mailbox(self, mailbox_id: int, password: str):
    """Create a mailbox on Stalwart and record the outcome.

    The password is passed as a task arg (preserved across retries) and never
    persisted on the Mailbox row.
    """
    try:
        mb = Mailbox.objects.get(pk=mailbox_id)
    except Mailbox.DoesNotExist:
        logger.error("provision_mailbox: Mailbox %s not found", mailbox_id)
        return

    if mb.status == Mailbox.Status.ACTIVE:
        return

    try:
        get_mail_provider().create_mailbox(
            email=mb.email, password=password, name=mb.name, quota_mb=mb.quota_mb
        )
        mb.status = Mailbox.Status.ACTIVE
        mb.error = None
        mb.save(update_fields=["status", "error"])
    except MailProviderError as exc:
        mb.status = Mailbox.Status.FAILED
        mb.error = str(exc)[:5000]
        mb.save(update_fields=["status", "error"])
        logger.error("provision_mailbox: failed for %s: %s", mb.email, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=_MAX_ATTEMPTS, default_retry_delay=60)
def send_email(self, email_message_id: int, text_body: str = "", html_body: str = ""):
    """Send a queued EmailMessage and record the outcome.

    The body is passed as task args (and thus preserved across retries) rather
    than persisted on the log row.
    """
    try:
        msg = EmailMessage.objects.select_related("account").get(pk=email_message_id)
    except EmailMessage.DoesNotExist:
        logger.error("send_email: EmailMessage %s not found", email_message_id)
        return

    if msg.status == EmailMessage.Status.SENT:
        return

    # Open/click tracking is a plan capability — inject only when included.
    if html_body:
        try:
            from apps.billing.limits import LimitChecker

            if LimitChecker(msg.account).has_feature("tracking_webhooks"):
                from apps.email.services import apply_tracking

                domain = msg.domain.domain if msg.domain else msg.from_email.rsplit("@", 1)[-1]
                html_body = apply_tracking(html_body, msg, msg.to_email, domain)
        except Exception as exc:
            logger.debug("send_email: tracking injection skipped: %s", exc)

    try:
        message_id = smtp_send(
            from_email=msg.from_email,
            to_email=msg.to_email,
            subject=msg.subject,
            text_body=text_body,
            html_body=html_body,
        )
        msg.mark_sent(message_id)

        try:
            from apps.billing.models import UsageSummary
            UsageSummary.increment_emails(msg.account)
        except Exception as exc:
            logger.debug("send_email: usage increment skipped: %s", exc)
    except Exception as exc:
        msg.mark_failed(str(exc))
        logger.exception("send_email: failed for EmailMessage %s", email_message_id)
        raise self.retry(exc=exc)
