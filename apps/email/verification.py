"""Shared domain-verification logic.

``refresh_domain`` runs the live DNS check for a sending domain, persists the
per-record and rollup readiness flags, and transitions the domain
PENDING → VERIFIED once ownership is proven (and, for AWS SES, once SES itself
reports the identity verified). Both the ``domain_verify`` view and the
``reverify_pending_domains`` Celery task call this so the rules stay in one place.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from apps.email import dnscheck
from apps.email.models import EmailDomain

logger = logging.getLogger(__name__)


def _ses_identity_verified(record: EmailDomain, provider) -> bool:
    """Ask the mail provider whether it considers the identity verified.

    Tolerant: any provider error (unsupported, transient AWS failure) is treated
    as "not yet verified" rather than raising, so a flaky API call never blocks
    or crashes verification — the next poll retries.
    """
    try:
        if provider is None:
            from apps.email.providers import get_mail_provider

            provider = get_mail_provider()
        return bool(provider.verify_domain(record.domain).success)
    except Exception:
        logger.exception("provider.verify_domain failed for %s", record.domain)
        return False


def refresh_domain(record: EmailDomain, *, provider=None) -> dict:
    """Re-check DNS for ``record``, persist status, return the check_domain dict.

    Side effects (all saved):
      * ``spf_ok`` / ``dkim_ok`` / ``dmarc_ok`` / ``last_checked_at`` on the domain
      * ``is_ok`` / ``checked_at`` on each EmailDnsRecord row (SES domains)
      * ``status`` → VERIFIED + ``verified_at`` when ownership is satisfied
    """
    if record.ensure_verification_token():
        record.save(update_fields=["verify_record_name", "verify_record_value"])

    rows = dnscheck.check_records(record)

    now = timezone.now()
    # Persist per-record readiness for EmailDnsRecord-backed (SES) domains so the
    # domain card can show a green tick per CNAME.
    db_rows = {(r.key, r.name): r for r in record.dns_record_rows.all()} if record.pk else {}
    to_update = []
    for row in rows:
        db_row = db_rows.get((row["key"], row["name"]))
        if db_row is not None and (db_row.is_ok != row["ok"] or db_row.checked_at is None):
            db_row.is_ok = row["ok"]
            db_row.checked_at = now
            to_update.append(db_row)
    if to_update:
        from apps.email.models import EmailDnsRecord

        EmailDnsRecord.objects.bulk_update(to_update, ["is_ok", "checked_at"])

    agg: dict[str, list[bool]] = {}
    for row in rows:
        agg.setdefault(row["key"], []).append(row["ok"])

    def _ok(key: str) -> bool:
        vals = agg.get(key) or []
        return bool(vals) and all(vals)

    record.dkim_ok = _ok("dkim")
    record.spf_ok = _ok("spf")
    record.dmarc_ok = _ok("dmarc")
    record.last_checked_at = now
    fields = ["dkim_ok", "spf_ok", "dmarc_ok", "last_checked_at"]

    ownership_ok = _ok("verify")
    if ownership_ok and record.is_ses_backed():
        # SES verifies a domain by checking the same DKIM CNAMEs; require both
        # our DNS check and SES's own status so we never mark a domain sendable
        # before SES will actually accept mail from it.
        ownership_ok = _ok("dkim") and _ses_identity_verified(record, provider)

    if ownership_ok and record.status != EmailDomain.Status.VERIFIED:
        record.status = EmailDomain.Status.VERIFIED
        record.verified_at = now
        fields += ["status", "verified_at"]

    record.save(update_fields=fields)

    return {k: _ok(k) for k in ("verify", "dkim", "spf", "dmarc")}
