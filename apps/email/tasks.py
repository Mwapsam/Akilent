"""Celery tasks for email provisioning and maintenance.

All heavy provider calls are handled here rather than in Django views, so HTTP
requests return immediately and retries happen transparently.

Queues:
  email      â€” mailbox/domain/alias provisioning tasks
  outbound   â€” send_email, send_bulk_recipient_email
  campaigns  â€” dispatch_campaign
  webhooks   â€” deliver_webhook
  celery     â€” prune_* maintenance tasks

ProvisioningJob is created by the caller before dispatching the task, then
updated here as the task runs (PENDING â†’ RUNNING â†’ SUCCESS | FAILED | RETRYING).
"""
from __future__ import annotations

import json
import logging

import requests
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.email.exceptions import EmailProviderError
from apps.email.models import (
    BulkEmailCampaign,
    BulkEmailRecipient,
    EmailMessage,
    ProvisioningJob,
    WebhookDelivery,
)
from apps.email.providers import get_mail_provider, get_send_provider
from apps.email.services import render_template, validate_variables
from apps.email.types import OutboundEmail
from apps.email.webhooks import EVENT_HEADER, SIGNATURE_HEADER, build_signature_header

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY_BASE = 2  # seconds (exponential base)
_RETRY_DELAY_MULTIPLIER = 2  # exponential growth

_WEBHOOK_MAX_RETRIES = 6
_WEBHOOK_RETRY_DELAY_BASE = 10  # seconds (webhook retry base, longer than email)
_WEBHOOK_TIMEOUT_SECONDS = 10

_CAMPAIGN_CHUNK_SIZE = 500


def _exponential_backoff_delay(retry_count: int, base: int = _RETRY_DELAY_BASE, multiplier: int = _RETRY_DELAY_MULTIPLIER) -> int:
    """Calculate exponential backoff delay: base * (multiplier ** retry_count).

    Examples:
      retry 0 -> 2 sec
      retry 1 -> 4 sec
      retry 2 -> 8 sec
    """
    return base * (multiplier ** retry_count)


# â”€â”€ Domain provisioning â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY_BASE,
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


# â”€â”€ Email sending â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _send_email_message(task, msg: EmailMessage, text_body: str, html_body: str) -> None:
    """Shared send logic for both send_email and send_bulk_recipient_email.

    ``task`` is the bound Celery task instance (for retry/request.retries).
    """
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
            logger.debug("_send_email_message: tracking injection skipped: %s", exc)

    try:
        result = get_send_provider().send(OutboundEmail(
            from_email=msg.from_email,
            to_email=msg.to_email,
            subject=msg.subject,
            text_body=text_body,
            html_body=html_body,
        ))
        msg.mark_sent(result.provider_message_id)
        if msg.campaign_id:
            msg.campaign.increment_counts(sent=1)
            BulkEmailRecipient.objects.filter(message=msg).update(
                status=BulkEmailRecipient.Status.SENT
            )
            _maybe_complete_campaign(msg.campaign)
    except Exception as exc:
        msg.mark_failed(str(exc))
        logger.exception("_send_email_message: failed for EmailMessage %s", msg.pk)
        is_last = task.request.retries >= _MAX_RETRIES
        if is_last:
            # Quota was reserved at accept time (LimitChecker.check_email); a
            # message that never gets delivered shouldn't permanently burn it.
            try:
                from apps.billing.limits import LimitChecker

                LimitChecker(msg.account).release_email()
            except Exception:
                logger.exception(
                    "_send_email_message: failed to release quota for EmailMessage %s",
                    msg.pk,
                )
            if msg.campaign_id:
                msg.campaign.increment_counts(failed=1)
                BulkEmailRecipient.objects.filter(message=msg).update(
                    status=BulkEmailRecipient.Status.FAILED, error=str(exc)[:5000]
                )
                _maybe_complete_campaign(msg.campaign)
        delay = _exponential_backoff_delay(task.request.retries)
        raise task.retry(exc=exc, countdown=delay)


def _maybe_complete_campaign(campaign: BulkEmailCampaign) -> None:
    """Mark a campaign COMPLETED once no recipients are PENDING/QUEUED.

    Called after each recipient's terminal send outcome â€” dispatch_campaign
    only re-enqueues itself while PENDING rows remain, so the final
    QUEUED -> SENT/FAILED transitions (which happen asynchronously, after the
    last chunk was dispatched) are what actually close out the campaign.
    """
    still_open = BulkEmailRecipient.objects.filter(
        campaign=campaign,
        status__in=[BulkEmailRecipient.Status.PENDING, BulkEmailRecipient.Status.QUEUED],
    ).exists()
    if not still_open:
        campaign.mark_completed()


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY_BASE,
    queue="outbound",
)
def send_email(
    self, email_message_id: int, text_body: str = "", html_body: str = ""
) -> None:
    """Send a queued EmailMessage and record the outcome."""
    try:
        msg = EmailMessage.objects.select_related("account", "campaign").get(
            pk=email_message_id
        )
    except EmailMessage.DoesNotExist:
        logger.error("send_email: EmailMessage %s not found", email_message_id)
        return

    if msg.status == EmailMessage.Status.SENT:
        return

    _send_email_message(self, msg, text_body, html_body)


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY_BASE,
    queue="outbound",
)
def send_bulk_recipient_email(self, email_message_id: int) -> None:
    """Render the EmailMessage's template/recipient variables, then send.

    Same retry/tracking/quota-release behavior as send_email â€” the only
    difference is content is resolved from EmailMessage.template +
    BulkEmailRecipient.variables rather than passed in directly.
    """
    try:
        msg = EmailMessage.objects.select_related(
            "account", "campaign", "template"
        ).get(pk=email_message_id)
    except EmailMessage.DoesNotExist:
        logger.error(
            "send_bulk_recipient_email: EmailMessage %s not found", email_message_id
        )
        return

    if msg.status == EmailMessage.Status.SENT:
        return

    recipient = BulkEmailRecipient.objects.filter(message=msg).first()
    variables = recipient.variables if recipient else {}

    campaign = msg.campaign
    if msg.template_id:
        missing = validate_variables(msg.template, variables)
        if missing:
            logger.warning(
                "send_bulk_recipient_email: template %s missing variables %s for recipient %s",
                msg.template_id, missing, msg.to_email,
            )
        subject, text_body, html_body = render_template(msg.template, variables)
    else:
        from apps.email.services import render_string

        campaign_html = campaign.html_override if campaign else ""
        campaign_text = campaign.text_override if campaign else ""
        campaign_subject = campaign.subject_override if campaign else ""

        subject = render_string(campaign_subject, variables)
        text_body = render_string(campaign_text, variables)
        html_body = render_string(campaign_html, variables)

    msg.subject = subject
    msg.rendered_subject = subject
    msg.rendered_text = text_body
    msg.rendered_html = html_body
    msg.save(update_fields=["subject", "rendered_subject", "rendered_text", "rendered_html"])

    _send_email_message(self, msg, text_body, html_body)


# â”€â”€ Bulk campaign fan-out â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@shared_task(
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY_BASE,
    queue="campaigns",
)
def dispatch_campaign(self, campaign_id: int) -> None:
    """Fan a BulkEmailCampaign out to EmailMessage rows, one bounded chunk at a time.

    Self-chaining: processes up to _CAMPAIGN_CHUNK_SIZE PENDING recipients,
    then re-enqueues itself if more remain. Keeps each task run bounded and
    restart-safe if a worker dies mid-campaign.

    Quota is reserved per chunk (LimitChecker.reserve_bulk) with partial-send
    semantics: recipients beyond the account's remaining monthly quota are
    marked FAILED with a quota-exceeded error, but the rest of the chunk (and
    campaign) still proceeds.
    """
    from apps.billing.limits import LimitChecker

    try:
        campaign = BulkEmailCampaign.objects.select_related("account", "domain", "template").get(
            pk=campaign_id
        )
    except BulkEmailCampaign.DoesNotExist:
        logger.error("dispatch_campaign: BulkEmailCampaign %s not found", campaign_id)
        return

    if campaign.status in (
        BulkEmailCampaign.Status.CANCELLED,
        BulkEmailCampaign.Status.COMPLETED,
    ):
        return

    campaign.mark_sending()

    chunk = list(
        BulkEmailRecipient.objects.filter(
            campaign=campaign, status=BulkEmailRecipient.Status.PENDING
        ).order_by("pk")[:_CAMPAIGN_CHUNK_SIZE]
    )

    if not chunk:
        remaining = BulkEmailRecipient.objects.filter(
            campaign=campaign,
            status__in=[BulkEmailRecipient.Status.PENDING, BulkEmailRecipient.Status.QUEUED],
        ).exists()
        if not remaining:
            campaign.mark_completed()
        return

    from apps.email.models import SuppressionListEntry

    # Check for suppressed recipients (bounces, complaints, unsubscribes)
    suppressed_emails = set(
        SuppressionListEntry.objects.filter(
            account=campaign.account,
            email__in=[r.to_email for r in chunk],
        ).values_list("email", flat=True)
    )

    to_process, suppressed_recipients = [], []
    for r in chunk:
        if r.to_email in suppressed_emails:
            suppressed_recipients.append(r)
        else:
            to_process.append(r)

    if suppressed_recipients:
        BulkEmailRecipient.objects.filter(
            pk__in=[r.pk for r in suppressed_recipients]
        ).update(
            status=BulkEmailRecipient.Status.FAILED,
            error="Recipient is suppressed (bounce, complaint, or unsubscribe).",
        )
        campaign.increment_counts(failed=len(suppressed_recipients))

    granted = LimitChecker(campaign.account).reserve_bulk(len(to_process))
    to_send, to_fail = to_process[:granted], to_process[granted:]

    if to_fail:
        BulkEmailRecipient.objects.filter(
            pk__in=[r.pk for r in to_fail]
        ).update(
            status=BulkEmailRecipient.Status.FAILED,
            error="Monthly email limit reached.",
        )
        campaign.increment_counts(failed=len(to_fail))

    if to_send:
        messages = [
            EmailMessage(
                account=campaign.account,
                domain=campaign.domain,
                template=campaign.template,
                campaign=campaign,
                from_email=campaign.from_email,
                to_email=r.to_email,
                subject=campaign.template.subject if campaign.template else campaign.subject_override,
            )
            for r in to_send
        ]
        created = EmailMessage.objects.bulk_create(messages)

        for recipient, msg in zip(to_send, created):
            recipient.message = msg
            recipient.status = BulkEmailRecipient.Status.QUEUED
        BulkEmailRecipient.objects.bulk_update(to_send, ["message", "status"])
        campaign.increment_counts(queued=len(to_send))

        message_ids = [m.pk for m in created]
        transaction.on_commit(
            lambda ids=message_ids: [send_bulk_recipient_email.delay(mid) for mid in ids]
        )

    still_pending = BulkEmailRecipient.objects.filter(
        campaign=campaign, status=BulkEmailRecipient.Status.PENDING
    ).exists()
    if still_pending:
        dispatch_campaign.delay(campaign_id)
    else:
        # Remaining QUEUED rows complete asynchronously via
        # _maybe_complete_campaign, called from each recipient's send task.
        _maybe_complete_campaign(campaign)


# â”€â”€ Webhook delivery â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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
        countdown = _exponential_backoff_delay(self.request.retries, base=_WEBHOOK_RETRY_DELAY_BASE, multiplier=_RETRY_DELAY_MULTIPLIER)
        raise self.retry(exc=exc, countdown=countdown)


# â”€â”€ Maintenance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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





# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _get_job(job_id: int | None) -> ProvisioningJob | None:
    if not job_id:
        return None
    try:
        return ProvisioningJob.objects.get(pk=job_id)
    except ProvisioningJob.DoesNotExist:
        logger.warning("_get_job: ProvisioningJob %s not found", job_id)
        return None


