import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.models import Account
from apps.accounts.utils import get_current_account
from apps.email import dnscheck
from apps.email.models import (
    EmailAlias,
    EmailApiKey,
    EmailDomain,
    EmailMessage,
    EmailTrackingEvent,
    EmailTrackingToken,
    Mailbox,
)
from apps.email.providers import MailProviderError
from apps.email.services import AliasService, DomainService, MailboxService
from apps.email.tasks import provision_mailbox

logger = logging.getLogger(__name__)


def _is_admin(request) -> bool:
    return bool(getattr(request.user, "is_superuser", False))


def _scoped(manager, request, account):
    qs = manager.all()
    if not _is_admin(request):
        qs = qs.filter(account=account)
    return qs


# --- AJAX helpers -------------------------------------------------------------

def is_ajax(request) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _toast(response, kind: str, message: str):
    from urllib.parse import quote
    response["X-Toast"] = f"{kind}|{quote(message)}"
    return response


def _ajax_error(message: str, status: int = 400):
    return JsonResponse({"error": message}, status=status)


def _domain_card(request, record):
    return render(request, "email/_domain_card.html", {
        "d": record,
        "is_admin": _is_admin(request),
    })


_MSG_LEVEL = {"success": messages.SUCCESS, "warning": messages.WARNING, "danger": messages.ERROR}


def _mailbox_row(request, mb):
    return render(request, "email/_mailbox_row.html", {
        "mb": mb, "is_admin": _is_admin(request),
    })


# --- Dashboard ----------------------------------------------------------------

@login_required
def domains_list(request):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    domains = _scoped(EmailDomain.objects, request, account).select_related("account")
    recent = _scoped(EmailMessage.objects, request, account)[:25]
    api_key = (
        EmailApiKey.objects.filter(account=account, is_active=True).first()
        if account
        else None
    )
    new_api_key_plaintext = request.session.pop("new_api_key", None) if account else None
    from apps.billing.limits import LimitChecker

    email_apis_enabled = admin or (account and LimitChecker(account).has_feature("email_apis"))

    return render(request, "email/domains.html", {
        "account": account,
        "is_admin": admin,
        "domains": domains,
        "api_key": api_key,
        "new_api_key_plaintext": new_api_key_plaintext,
        "recent": recent,
        "email_apis_enabled": email_apis_enabled,
        "accounts": Account.objects.order_by("company_name") if admin else None,
    })


@login_required
@require_POST
def domain_create(request):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    ajax = is_ajax(request)
    domain = (request.POST.get("domain") or "").strip().lower()
    if not domain:
        if ajax:
            return _ajax_error("Domain is required.")
        messages.error(request, "Domain is required.")
        return redirect("email-domains")

    if EmailDomain.objects.filter(domain=domain).exists():
        msg = "That domain is already registered."
        if ajax:
            return _ajax_error(msg)
        messages.error(request, msg)
        return redirect("email-domains")

    if account is None:
        account = Account.objects.filter(pk=request.POST.get("account_id")).first()
        if account is None:
            msg = "Select an account to attach this domain to."
            if ajax:
                return _ajax_error(msg)
            messages.error(request, msg)
            return redirect("email-domains")

    record = EmailDomain.objects.create(account=account, domain=domain)
    record.ensure_verification_token()
    record.save(update_fields=["verify_record_name", "verify_record_value"])

    kind, message = "success", f"{domain} added — add the DNS records below, then run the DNS check."
    try:
        DomainService(account, actor=request.user).provision(record)
    except MailProviderError as exc:
        record.status = EmailDomain.Status.FAILED
        record.save(update_fields=["status"])
        logger.error("domain_create: mail server error for %s: %s", domain, exc)
        kind, message = "danger", f"Provisioning failed: {exc}"

    if ajax:
        return _toast(_domain_card(request, record), kind, message)
    messages.add_message(request, _MSG_LEVEL[kind], message)
    return redirect("email-domains")


@login_required
@require_POST
def domain_verify(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    ajax = is_ajax(request)

    if record.ensure_verification_token():
        record.save(update_fields=["verify_record_name", "verify_record_value"])

    results = dnscheck.check_domain(record)
    record.dkim_ok = results["dkim"]
    record.spf_ok = results["spf"]
    record.dmarc_ok = results["dmarc"]
    record.last_checked_at = timezone.now()
    fields = ["dkim_ok", "spf_ok", "dmarc_ok", "last_checked_at"]

    newly_verified = results["verify"] and not record.is_verified
    if results["verify"] and record.status != EmailDomain.Status.VERIFIED:
        record.status = EmailDomain.Status.VERIFIED
        record.verified_at = timezone.now()
        fields += ["status", "verified_at"]
    record.save(update_fields=fields)

    if record.is_verified:
        missing = [r["label"] for r in record.dns_records() if r["required"] and not r["ok"]]
        if newly_verified:
            kind, message = "success", f"{record.domain} verified — you're ready to send."
        elif missing:
            kind, message = "warning", f"Ownership confirmed, but {', '.join(missing)} isn't live in DNS yet."
        else:
            kind, message = "success", f"{record.domain}: all records look good."
    elif not results["verify"]:
        kind, message = "warning", (
            "We couldn't find your verification record yet. DNS changes can take "
            "a while to propagate — we'll keep checking automatically."
        )
    else:
        kind, message = "success", "DNS status updated."

    if ajax:
        return _toast(_domain_card(request, record), kind, message)
    messages.add_message(request, _MSG_LEVEL[kind], message)
    return redirect("email-domains")


@login_required
@require_POST
def domain_toggle(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    new_active = not record.is_active
    try:
        service = DomainService(record.account, actor=request.user)
        if new_active:
            service.enable(record)
        else:
            service.disable(record)
        kind = "success"
        message = f"{record.domain} {'enabled' if new_active else 'disabled'}."
    except MailProviderError as exc:
        kind, message = "danger", f"Could not update {record.domain}: {exc}"

    if ajax:
        return _toast(_domain_card(request, record), kind, message)
    messages.add_message(
        request, messages.SUCCESS if kind == "success" else messages.ERROR, message
    )
    return redirect("email-domains")


@login_required
@require_POST
def domain_delete(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    try:
        DomainService(record.account, actor=request.user).deprovision(record)
    except MailProviderError as exc:
        msg = f"Could not delete {record.domain}: {exc}"
        if ajax:
            return _ajax_error(msg)
        messages.error(request, msg)
        return redirect("email-domains")
    domain_name = record.domain
    record.delete()
    if ajax:
        return _toast(HttpResponse(""), "success", f"{domain_name} deleted.")
    messages.success(request, f"{domain_name} deleted.")
    return redirect("email-domains")


@login_required
@require_POST
def key_create(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")
    EmailApiKey.objects.filter(account=account).update(is_active=False)
    _, raw_key = EmailApiKey.create_for_account(account, name="default")
    request.session["new_api_key"] = raw_key
    messages.success(request, "New API key generated — copy it now, it won't be shown again.")
    return redirect("email-domains")


# --- Insights -----------------------------------------------------------------

def _build_engagement_stats(domain_name: str) -> list[dict]:
    """Return per-day open/click counts for a verified domain."""
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    rows = (
        EmailTrackingEvent.objects
        .filter(message__domain__domain=domain_name)
        .annotate(day=TruncDate("occurred_at"))
        .values("day", "kind")
        .annotate(count=Count("id"))
        .order_by("day")
    )

    # Pivot into [{day, opens, clicks}]
    pivot: dict = {}
    for row in rows:
        day = str(row["day"])
        entry = pivot.setdefault(day, {"day": day, "opens": 0, "clicks": 0})
        if row["kind"] == EmailTrackingEvent.Kind.OPEN:
            entry["opens"] += row["count"]
        else:
            entry["clicks"] += row["count"]
    return list(pivot.values())


@login_required
def insights(request):
    from apps.billing.limits import LimitChecker

    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    has_analytics = admin or (account and LimitChecker(account).has_feature("detailed_analytics"))
    domains = list(
        _scoped(EmailDomain.objects, request, account).filter(
            status=EmailDomain.Status.VERIFIED
        )
    )
    domain_names = [d.domain for d in domains]
    selected = request.GET.get("domain") or (domain_names[0] if domain_names else "")

    logs, stats, error = [], [], None
    if has_analytics and selected and selected in domain_names:
        log_qs = EmailMessage.objects.filter(domain__domain=selected)
        if not admin and account:
            log_qs = log_qs.filter(account=account)
        logs = list(log_qs.order_by("-created_at")[:100])
        stats = _build_engagement_stats(selected)

    return render(request, "email/insights.html", {
        "account": account,
        "is_admin": admin,
        "has_analytics": has_analytics,
        "domains": domains,
        "selected": selected,
        "logs": logs,
        "stats": stats,
        "error": error,
    })


# --- Mailboxes & aliases ------------------------------------------------------

@login_required
def mailbox_list(request):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    domains = _scoped(EmailDomain.objects, request, account).filter(
        status=EmailDomain.Status.VERIFIED
    )
    mailboxes = _scoped(Mailbox.objects, request, account).select_related("domain")
    aliases = _scoped(EmailAlias.objects, request, account).select_related("domain")
    return render(request, "email/mailboxes.html", {
        "account": account,
        "is_admin": admin,
        "domains": domains,
        "mailboxes": mailboxes,
        "aliases": aliases,
    })


@login_required
@require_POST
def mailbox_create(request):
    admin = _is_admin(request)
    current = get_current_account(request)
    if current is None and not admin:
        return redirect("dashboard")

    ajax = is_ajax(request)
    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    name = (request.POST.get("name") or "").strip()
    try:
        quota_mb = int(request.POST.get("quota_mb") or 1024)
    except ValueError:
        quota_mb = 1024

    def fail(msg):
        if ajax:
            return _ajax_error(msg)
        messages.error(request, msg)
        return redirect("email-mailboxes")

    if not email or not password:
        return fail("Email and password are required.")

    domain_part = email.rsplit("@", 1)[-1]
    domain = _scoped(EmailDomain.objects, request, current).filter(
        domain=domain_part, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        return fail(f"'{domain_part}' is not a verified sending domain.")

    account = domain.account

    if Mailbox.objects.filter(email=email).exists():
        return fail("That mailbox already exists.")

    note = ""
    if not admin:
        from apps.billing.limits import LimitChecker, PlanLimitExceeded
        lc = LimitChecker(account)
        try:
            lc.check_mailbox()
        except PlanLimitExceeded as exc:
            return fail(str(exc))
        cap = lc.mailbox_storage_cap_mb()
        if cap and quota_mb > cap:
            quota_mb = cap
            note = f" (storage capped at {cap} MB by your plan)"

    mb = Mailbox.objects.create(
        account=account, domain=domain, email=email, name=name, quota_mb=quota_mb
    )
    transaction.on_commit(lambda: provision_mailbox.delay(mb.id, password))
    msg = f"Provisioning {email}{note}…"
    if ajax:
        return _toast(_mailbox_row(request, mb), "success", msg)
    messages.success(request, msg)
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_delete(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    email = mb.email
    try:
        MailboxService(mb.account, actor=request.user).deprovision(mb)
        mb.delete()
    except MailProviderError as exc:
        if ajax:
            return _ajax_error(f"Delete failed: {exc}")
        messages.error(request, f"Delete failed: {exc}")
        return redirect("email-mailboxes")
    if ajax:
        return _toast(HttpResponse(""), "success", f"{email} deleted.")
    messages.success(request, "Mailbox deleted.")
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_password(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    password = request.POST.get("password") or ""
    if not password:
        if ajax:
            return _ajax_error("Password is required.")
        messages.error(request, "Password is required.")
        return redirect("email-mailboxes")
    try:
        MailboxService(mb.account, actor=request.user).change_password(mb, password)
    except MailProviderError as exc:
        if ajax:
            return _ajax_error(f"Password change failed: {exc}")
        messages.error(request, f"Password change failed: {exc}")
        return redirect("email-mailboxes")
    if ajax:
        return _toast(HttpResponse(""), "success", f"Password updated for {mb.email}.")
    messages.success(request, f"Password updated for {mb.email}.")
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_quota(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    try:
        quota_mb = int(request.POST.get("quota_mb") or mb.quota_mb)
    except ValueError:
        if ajax:
            return _ajax_error("Quota must be a number (MB).")
        messages.error(request, "Quota must be a number (MB).")
        return redirect("email-mailboxes")
    note = ""
    if not admin:
        from apps.billing.limits import LimitChecker
        cap = LimitChecker(mb.account).mailbox_storage_cap_mb()
        if cap and quota_mb > cap:
            quota_mb = cap
            note = f" (capped at {cap} MB by plan)"
    try:
        MailboxService(mb.account, actor=request.user).set_quota(mb, quota_mb)
    except MailProviderError as exc:
        if ajax:
            return _ajax_error(f"Quota update failed: {exc}")
        messages.error(request, f"Quota update failed: {exc}")
        return redirect("email-mailboxes")
    if ajax:
        return _toast(_mailbox_row(request, mb), "success", f"Quota updated for {mb.email}{note}.")
    messages.success(request, f"Quota updated for {mb.email}{note}.")
    return redirect("email-mailboxes")


@login_required
@require_POST
def alias_create(request):
    admin = _is_admin(request)
    current = get_current_account(request)
    if current is None and not admin:
        return redirect("dashboard")

    ajax = is_ajax(request)
    address = (request.POST.get("address") or "").strip().lower()
    goto = (request.POST.get("goto") or "").strip().lower()

    def fail(msg):
        if ajax:
            return _ajax_error(msg)
        messages.error(request, msg)
        return redirect("email-mailboxes")

    if not address or not goto:
        return fail("Both the alias and forwarding address are required.")

    domain_part = address.rsplit("@", 1)[-1]
    domain = _scoped(EmailDomain.objects, request, current).filter(
        domain=domain_part, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        return fail(f"'{domain_part}' is not a verified sending domain.")

    if not admin:
        from apps.billing.limits import LimitChecker, PlanLimitExceeded
        try:
            LimitChecker(domain.account).check_alias()
        except PlanLimitExceeded as exc:
            return fail(str(exc))

    alias = EmailAlias(account=domain.account, domain=domain, address=address, goto=goto)
    try:
        AliasService(domain.account, actor=request.user).provision(alias)
    except MailProviderError as exc:
        return fail(f"Alias creation failed: {exc}")
    alias.save()

    if ajax:
        resp = render(request, "email/_alias_row.html", {"a": alias, "is_admin": admin})
        return _toast(resp, "success", f"Alias {address} → {goto} created.")
    messages.success(request, f"Alias {address} → {goto} created.")
    return redirect("email-mailboxes")


# --- Open / click tracking endpoints ------------------------------------------
# These are unauthenticated GET endpoints hit by mail clients, not logged-in
# users. No CSRF needed (GET-only). Fail silently so broken tokens don't 500.

_TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)


def tracking_open(request, token: str):
    """Record an open event and return a 1×1 transparent GIF."""
    try:
        t = EmailTrackingToken.objects.select_related("message").get(token=token)
        EmailTrackingEvent.objects.create(
            message=t.message,
            kind=EmailTrackingEvent.Kind.OPEN,
            ip=request.META.get("REMOTE_ADDR"),
            ua=(request.META.get("HTTP_USER_AGENT") or "")[:512],
        )
    except EmailTrackingToken.DoesNotExist:
        pass
    return HttpResponse(_TRANSPARENT_GIF, content_type="image/gif")


def tracking_click(request, token: str):
    """Record a click event and redirect to the original URL."""
    destination = "/"
    try:
        t = EmailTrackingToken.objects.select_related("message").get(token=token)
        destination = t.url or "/"
        EmailTrackingEvent.objects.create(
            message=t.message,
            kind=EmailTrackingEvent.Kind.CLICK,
            url=t.url,
            ip=request.META.get("REMOTE_ADDR"),
            ua=(request.META.get("HTTP_USER_AGENT") or "")[:512],
        )
    except EmailTrackingToken.DoesNotExist:
        pass
    return redirect(destination)


# --- Transactional send API ---------------------------------------------------

def _authenticate(request):
    header = request.headers.get("X-Api-Key") or ""
    if not header:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            header = auth.removeprefix("Bearer ").strip()
    if not header:
        return None
    return EmailApiKey.authenticate(header)


@csrf_exempt
@require_POST
def api_send(request):
    """Deprecated shim for POST /api/v1/messages (apps.api.views.MessageCreateView).

    Kept working so integrations built against the original path don't break
    mid-rollout; shares its actual send logic with the versioned endpoint via
    apps.api.services so the two can never drift out of the same behavior.
    """
    from apps.api.services import UnverifiedDomainError, create_and_queue_message
    from apps.billing.limits import PlanLimitExceeded

    api_key = _authenticate(request)
    if api_key is None:
        return JsonResponse({"error": "Invalid or missing API key"}, status=401)

    account = api_key.account

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from_email = (body.get("from") or "").strip()
    to_email = (body.get("to") or "").strip()
    subject = body.get("subject") or ""
    text_body = body.get("text") or ""
    html_body = body.get("html") or ""

    if not from_email or not to_email:
        return JsonResponse({"error": "'from' and 'to' are required"}, status=400)

    try:
        msg = create_and_queue_message(
            account=account,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
    except UnverifiedDomainError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    except PlanLimitExceeded as exc:
        return JsonResponse({"error": str(exc)}, status=403)

    api_key.touch()
    response = JsonResponse({"id": msg.id, "status": msg.status}, status=202)
    response["Deprecation"] = "true"
    response["Link"] = '</api/v1/messages>; rel="successor-version"'
    return response
