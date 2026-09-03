"""Live DNS verification for sending domains.

Queries the customer's published records and reports, per record, whether the
expected value is present. Self-hosted (dnspython) — no external API or
per-account token. ``_resolve_txt`` / ``_resolve_cname`` are the only network
seams, so tests monkeypatch them instead of hitting real DNS.

The spec being checked comes from ``EmailDomain.dns_records()`` — either the
legacy column-derived list (Stalwart-style single-TXT DKIM) or the
``EmailDnsRecord``-backed list (AWS SES Easy DKIM = three CNAMEs).
"""
import logging
import re

import dns.resolver

logger = logging.getLogger(__name__)

# Keep checks snappy — DNS that isn't there yet should fail fast, not hang the
# request or the auto-poll.
_LIFETIME = 5.0


def _resolve_txt(name: str) -> list[str]:
    """Return the TXT values published at ``name`` (each fully concatenated)."""
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=_LIFETIME)
    except Exception as exc:  # NXDOMAIN, NoAnswer, Timeout, NoNameservers, …
        logger.debug("TXT lookup for %s failed: %s", name, exc)
        return []
    values = []
    for rdata in answers:
        # A TXT rdata is one or more byte strings; join them into the real value.
        parts = [
            p.decode("utf-8", "ignore") if isinstance(p, bytes) else str(p)
            for p in rdata.strings
        ]
        values.append("".join(parts))
    return values


def _resolve_cname(name: str) -> list[str]:
    """Return the CNAME target(s) published at ``name`` (trailing dot kept)."""
    try:
        answers = dns.resolver.resolve(name, "CNAME", lifetime=_LIFETIME)
    except Exception as exc:  # NXDOMAIN, NoAnswer, Timeout, NoNameservers, …
        logger.debug("CNAME lookup for %s failed: %s", name, exc)
        return []
    return [str(rdata.target) for rdata in answers]


def _norm(s: str) -> str:
    """Lower-case and strip all whitespace — for tolerant value comparison."""
    return re.sub(r"\s+", "", s or "").lower()


def _contains(name: str, needle: str) -> bool:
    if not needle:
        return False
    target = _norm(needle)
    return any(target in _norm(v) for v in _resolve_txt(name))


def _dkim_public_key(value: str) -> str:
    """Extract the base64 ``p=`` portion of a DKIM TXT value, if present."""
    m = re.search(r"p=([A-Za-z0-9+/=]+)", value or "")
    return m.group(1) if m else ""


def _spf_present(row: dict) -> bool:
    """True when an ``spf1`` record at the row's name carries every ``include:``
    mechanism named in the desired value.

    The desired value (``row["value"]``) is what the UI tells the customer to
    publish, e.g. ``v=spf1 include:amazonses.com ~all`` for SES or
    ``v=spf1 include:<relay-host> ~all`` for Stalwart — so the check follows the
    backend automatically instead of hard-coding a host.
    """
    name = row.get("name") or ""
    if not name:
        return False
    want = _norm(row.get("value") or "")
    includes = re.findall(r"include:[a-z0-9._-]+", want)
    published = [_norm(v) for v in _resolve_txt(name) if _norm(v).startswith("v=spf1")]
    if not published:
        return False
    if not includes:
        return True  # any spf1 record satisfies a spec with no include mechanism
    return any(all(inc in spf for inc in includes) for spf in published)


def _record_present(row: dict) -> bool:
    """True when the single DNS record described by ``row`` is live as specified."""
    key = row.get("key")
    rtype = (row.get("type") or "TXT").upper()
    name = row.get("name") or ""
    value = row.get("value") or ""
    if not name:
        return False

    if key == "spf":
        return _spf_present(row)

    if key == "dmarc":
        # Any valid DMARC1 policy at _dmarc.<domain> counts.
        return any(
            _norm(v).startswith("v=dmarc1") for v in _resolve_txt(name)
        )

    if rtype == "CNAME":
        if not value:
            return False
        want = _norm(value).rstrip(".")
        return any(_norm(v).rstrip(".") == want for v in _resolve_cname(name))

    # TXT
    if not value:
        return False
    if key == "dkim":
        # Legacy single-TXT DKIM: the published record must carry our p= base64.
        pub = _dkim_public_key(value)
        return bool(pub) and _contains(name, pub)
    return _contains(name, value)


def check_records(record) -> list[dict]:
    """Return ``record.dns_records()`` with each row's live ``ok`` recomputed."""
    return [{**row, "ok": _record_present(row)} for row in record.dns_records()]


def check_domain(record) -> dict:
    """Check every DNS record for ``record`` and return ``{key: found_bool}``.

    ``key`` is one of ``verify``, ``dkim``, ``spf``, ``dmarc`` (matching
    ``EmailDomain.dns_records()``). A key with several rows (SES DKIM has three)
    is ``True`` only when every one of its rows is live.
    """
    by_key: dict[str, list[bool]] = {}
    for row in check_records(record):
        by_key.setdefault(row["key"], []).append(row["ok"])

    return {
        key: (bool(by_key.get(key)) and all(by_key.get(key, [])))
        for key in ("verify", "dkim", "spf", "dmarc")
    }
