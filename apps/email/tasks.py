"""Celery tasks for email provisioning and maintenance.

All heavy provider calls are handled here rather than in Django views, so HTTP
requests return immediately and retries happen transparently.

Queues:
  email     — mailbox/domain/alias provisioning tasks
  outbound  — send_email
  webhooks  — deliver_webhook
  celery    — prune_* maintenance tasks

ProvisioningJob is created by the caller before dispatching the task, then
updated here as the task runs (PENDING → RUNNING → SUCCESS | FAILED | RETRYING).
"""
from __future__ import annotations

import json
import logging

import requests
from celery import shared_task
from django.utils import timezone

from apps.email.exceptions import EmailProviderError
from apps.email.models import EmailMessage, Mailbox, ProvisioningJob, WebhookDelivery
from apps.email.providers import get_mail_provider
from apps.email.services import MailboxService, smtp_send
from apps.email.webhooks import EVENT_HEADER, SIGNATURE_HEADER, build_signature_header

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 60  # seconds

_WEBHOOK_MAX_RETRIES = 6
_WEBHOOK_TIMEOUT_SECONDS = 10


# ── Mailbox provisioning ──────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY,
    queue="email",
)
def provision_mailbox_async(
    self, mailbox_id: int, password: str, job_id: int | None = None
) -> None:
    """Create a mailbox on the mail server and record the outcome.

    The password is passed as a task arg (preserved across retries) and is
    never persisted to the Mailbox row or ProvisioningJob.
    """
    job = _get_job(job_id)
    if job:
        job.celery_task_id = self.request.id or ""
        job.mark_running()

    try:
        mb = Mailbox.objects.select_related("account").get(pk=mailbox_id)
    except Mailbox.DoesNotExist:
        logger.error("provision_mailbox_async: Mailbox %s not found", mailbox_id)
        if job:
            job.mark_failed("Mailbox record not found.")
        return

    if mb.status == Mailbox.Status.ACTIVE:
        if job:
            job.mark_success()
        return

    try:
        MailboxService(mb.account).provision(mb, password)
        if job:
            job.mark_success()
    except EmailProviderError as exc:
        is_last = self.request.retries >= _MAX_RETRIES
        mb.status = Mailbox.Status.FAILED
        mb.error = str(exc)[:5000]
        mb.save(update_fields=["status", "error"])
        if job:
            job.mark_failed(str(exc), retrying=not is_last)
        logger.error(
            "provision_mailbox_async: failed for %s (attempt %d/%d): %s",
            mb.email,
            self.request.retries + 1,
            _MAX_RETRIES,
            exc,
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY,
    queue="email",
)
def deprovision_mailbox_async(
    self, email: str, job_id: int | None = None
) -> None:
    """Delete a mailbox from the mail server."""
    job = _get_job(job_id)
    if job:
        job.celery_task_id = self.request.id or ""
        job.mark_running()

    try:
        get_mail_provider().delete_mailbox(email)
        if job:
            job.mark_success()
    except EmailProviderError as exc:
        is_last = self.request.retries >= _MAX_RETRIES
        if job:
            job.mark_failed(str(exc), retrying=not is_last)
        logger.error("deprovision_mailbox_async: failed for %s: %s", email, exc)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY,
    queue="email",
)
def change_password_async(
    self, email: str, new_password: str, job_id: int | None = None
) -> None:
    """Change a mailbox password asynchronously."""
    job = _get_job(job_id)
    if job:
        job.celery_task_id = self.request.id or ""
        job.mark_running()

    try:
        get_mail_provider().change_password(email, new_password)
        if job:
            job.mark_success()
    except EmailProviderError as exc:
        is_last = self.request.retries >= _MAX_RETRIES
        if job:
            job.mark_failed(str(exc), retrying=not is_last)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY,
    queue="email",
)
def set_quota_async(
    self,
    email: str,
    quota_mb: int,
    mailbox_id: int | None = None,
    job_id: int | None = None,
) -> None:
    """Update mailbox storage quota asynchronously."""
    job = _get_job(job_id)
    if job:
        job.celery_task_id = self.request.id or ""
        job.mark_running()

    try:
        get_mail_provider().set_quota(email, quota_mb)
        if mailbox_id:
            Mailbox.objects.filter(pk=mailbox_id).update(quota_mb=quota_mb)
        if job:
            job.mark_success()
    except EmailProviderError as exc:
        is_last = self.request.retries >= _MAX_RETRIES
        if job:
            job.mark_failed(str(exc), retrying=not is_last)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY,
    queue="email",
)
def rotate_dkim_async(
    self,
    domain: str,
    domain_record_id: int,
    new_selector: str,
    job_id: int | None = None,
) -> None:
    """Generate a new DKIM keypair under new_selector and update the EmailDomain row."""
    from apps.email.models import EmailDomain

    job = _get_job(job_id)
    if job:
        job.celery_task_id = self.request.id or ""
        job.mark_running()

    try:
        rec = get_mail_provider().rotate_dkim(domain, new_selector=new_selector)
        EmailDomain.objects.filter(pk=domain_record_id).update(
            dkim_public_key=rec.public_key_txt,
            dkim_selector=rec.selector,
        )
        if job:
            job.mark_success()
    except EmailProviderError as exc:
        is_last = self.request.retries >= _MAX_RETRIES
        if job:
            job.mark_failed(str(exc), retrying=not is_last)
        raise self.retry(exc=exc)


# ── Domain provisioning ───────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY,
    queue="email",
)
def provision_domain_async(
    self, domain_record_id: int, job_id: int | None = None
) -> None:
    """Provision a domain on the mail server (async path for slow operations)."""
    from apps.email.models import EmailDomain
    from apps.email.services import DomainService

    job = _get_job(job_id)
    if job:
        job.celery_task_id = self.request.id or ""
        job.mark_running()

    try:
        domain_record = EmailDomain.objects.select_related("account").get(
            pk=domain_record_id
        )
    except EmailDomain.DoesNotExist:
        if job:
            job.mark_failed("EmailDomain record not found.")
        return

    try:
        DomainService(domain_record.account).provision(domain_record)
        if job:
            job.mark_success()
    except EmailProviderError as exc:
        is_last = self.request.retries >= _MAX_RETRIES
        if job:
            job.mark_failed(str(exc), retrying=not is_last)
        raise self.retry(exc=exc)


# ── Email sending ─────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY,
    queue="outbound",
)
def send_email(
    self, email_message_id: int, text_body: str = "", html_body: str = ""
) -> None:
    """Send a queued EmailMessage and record the outcome."""
    try:
        msg = EmailMessage.objects.select_related("account").get(pk=email_message_id)
    except EmailMessage.DoesNotExist:
        logger.error("send_email: EmailMessage %s not found", email_message_id)
        return

    if msg.status == EmailMessage.Status.SENT:
        return

    if html_body:
        try:
            from apps.billing.limits import LimitChecker

            if LimitChecker(msg.account).has_feature("tracking_webhooks"):
                from apps.email.services import apply_tracking

                domain = (
                    msg.domain.domain
                    if msg.domain
                    else msg.from_email.rsplit("@", 1)[-1]
                )
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
    except Exception as exc:
        msg.mark_failed(str(exc))
        logger.exception("send_email: failed for EmailMessage %s", email_message_id)
        is_last = self.request.retries >= _MAX_RETRIES
        if is_last:
            # Quota was reserved at accept time (LimitChecker.check_email); a
            # message that never gets delivered shouldn't permanently burn it.
            try:
                from apps.billing.limits import LimitChecker

                LimitChecker(msg.account).release_email()
            except Exception:
                logger.exception(
                    "send_email: failed to release quota for EmailMessage %s",
                    email_message_id,
                )
        raise self.retry(exc=exc)


# ── Webhook delivery ──────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=_WEBHOOK_MAX_RETRIES,
    queue="webhooks",
)
def deliver_webhook(self, delivery_id: int) -> None:
    """POST a signed event payload to a WebhookEndpoint, with exponential backoff."""
    try:
        delivery = WebhookDelivery.objects.select_related("endpoint").get(pk=delivery_id)
    except WebhookDelivery.DoesNotExist:
        logger.error("deliver_webhook: WebhookDelivery %s not found", delivery_id)
        return

    if delivery.status == WebhookDelivery.Status.SUCCEEDED:
        return

    endpoint = delivery.endpoint
    body = json.dumps(delivery.payload).encode()
    signature = build_signature_header(endpoint.signing_secret, body)

    try:
        response = requests.post(
            endpoint.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                SIGNATURE_HEADER: signature,
                EVENT_HEADER: delivery.event_type,
            },
            timeout=_WEBHOOK_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        delivery.mark_succeeded(response.status_code)
    except Exception as exc:
        # requests.HTTPError carries a .response with a status code; a
        # connection/timeout failure has none.
        response_obj = getattr(exc, "response", None)
        response_code = response_obj.status_code if response_obj is not None else None

        is_last = self.request.retries >= _WEBHOOK_MAX_RETRIES
        delivery.mark_failed(response_code, exhausted=is_last)
        if is_last:
            endpoint.last_error = str(exc)[:2000]
            endpoint.save(update_fields=["last_error"])
            logger.error(
                "deliver_webhook: exhausted retries for delivery %s (%s): %s",
                delivery_id, delivery.event_type, exc,
            )
            return
        countdown = _RETRY_DELAY * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


# ── Maintenance ───────────────────────────────────────────────────────────────


@shared_task(queue="celery")
def prune_email_logs() -> int:
    """Delete EmailMessage rows older than each account's plan retention window."""
    from datetime import timedelta

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
    logger.info("prune_email_logs: deleted %d expired rows", total)
    return total


@shared_task(queue="celery")
def prune_tracking_tokens() -> int:
    """Delete stale EmailTrackingToken rows older than 90 days."""
    from datetime import timedelta

    from apps.email.models import EmailTrackingToken

    cutoff = timezone.now() - timedelta(days=90)
    deleted, _ = EmailTrackingToken.objects.filter(created_at__lt=cutoff).delete()
    logger.info("prune_tracking_tokens: deleted %d stale tokens", deleted)
    return deleted


@shared_task(queue="celery")
def prune_provisioning_jobs() -> int:
    """Delete completed ProvisioningJob rows older than 30 days."""
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(days=30)
    deleted, _ = ProvisioningJob.objects.filter(
        status__in=[ProvisioningJob.Status.SUCCESS, ProvisioningJob.Status.FAILED],
        completed_at__lt=cutoff,
    ).delete()
    logger.info("prune_provisioning_jobs: deleted %d old jobs", deleted)
    return deleted


# ── Legacy alias ──────────────────────────────────────────────────────────────
# Old beat schedule references provision_mailbox by name.
provision_mailbox = provision_mailbox_async


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_job(job_id: int | None) -> ProvisioningJob | None:
    if not job_id:
        return None
    try:
        return ProvisioningJob.objects.get(pk=job_id)
    except ProvisioningJob.DoesNotExist:
        logger.warning("_get_job: ProvisioningJob %s not found", job_id)
        return None
