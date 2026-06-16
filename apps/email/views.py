import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.models import Account
from apps.accounts.utils import get_current_account
from apps.email.iredmail import IRedMailClient, IRedMailError
from apps.email.models import (
    EmailAlias,
    EmailApiKey,
    EmailDomain,
    EmailMessage,
    Mailbox,
)
from apps.email.tasks import provision_mailbox, send_email

logger = logging.getLogger(__name__)


def _is_admin(request) -> bool:
    """Superusers manage every tenant's email resources without restriction.

    (Proper role-based access control will replace this blanket check later.)
    """
    return bool(getattr(request.user, "is_superuser", False))


def _scoped(manager, request, account):
    """Limit a queryset to ``account``, or return all rows for an admin."""
    qs = manager.all()
    if not _is_admin(request):
        qs = qs.filter(account=account)
    return qs


# --- Dashboard (server-rendered, account-scoped) ------------------------------

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
    return render(request, "email/domains.html", {
        "account": account,
        "is_admin": admin,
        "domains": domains,
        "api_key": api_key,
        "recent": recent,
        # Admins choose which tenant a new domain belongs to.
        "accounts": Account.objects.order_by("company_name") if admin else None,
    })


@login_required
@require_POST
def domain_create(request):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    domain = (request.POST.get("domain") or "").strip().lower()
    if not domain:
        messages.error(request, "Domain is required.")
        return redirect("email-domains")

    if EmailDomain.objects.filter(domain=domain).exists():
        messages.error(request, "That domain is already registered.")
        return redirect("email-domains")

    # An admin without their own workspace picks which tenant owns the domain.
    if account is None:
        account = Account.objects.filter(pk=request.POST.get("account_id")).first()
        if account is None:
            messages.error(request, "Select an account to attach this domain to.")
            return redirect("email-domains")

    record = EmailDomain.objects.create(account=account, domain=domain)
    try:
        client = IRedMailClient()
        result = client.provision_sending_domain(domain, selector=record.dkim_selector)
        record.dkim_public_key = result.get("dkim_txt", "")
        record.dkim_selector = result.get("selector", record.dkim_selector)
        record.save(update_fields=["dkim_public_key", "dkim_selector"])
        messages.success(
            request,
            f"{domain} provisioned. Add the DNS records below, then verify.",
        )
    except IRedMailError as exc:
        record.status = EmailDomain.Status.FAILED
        record.save(update_fields=["status"])
        logger.error("domain_create: mail server error for %s: %s", domain, exc)
        messages.error(request, f"Provisioning failed: {exc}")
    return redirect("email-domains")


@login_required
@require_POST
def domain_verify(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    try:
        dkim = IRedMailClient().get_dkim(record.domain) or {}
        if dkim.get("dkim_txt"):
            record.dkim_public_key = dkim["dkim_txt"]
            record.dkim_ok = True
            record.status = EmailDomain.Status.VERIFIED
            record.verified_at = timezone.now()
            record.save(update_fields=[
                "dkim_public_key", "dkim_ok", "status", "verified_at",
            ])
            messages.success(request, f"{record.domain} verified.")
        else:
            messages.error(request, "DKIM not found on the mail server yet.")
    except IRedMailError as exc:
        messages.error(request, f"Verification failed: {exc}")
    return redirect("email-domains")


@login_required
@require_POST
def domain_toggle(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    new_active = not record.is_active
    try:
        IRedMailClient().set_domain_status(record.domain, new_active)
        record.is_active = new_active
        record.save(update_fields=["is_active"])
        state = "enabled" if new_active else "disabled"
        messages.success(request, f"{record.domain} {state}.")
    except IRedMailError as exc:
        messages.error(request, f"Could not update {record.domain}: {exc}")
    return redirect("email-domains")


@login_required
@require_POST
def domain_delete(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    try:
        IRedMailClient().delete_domain(record.domain)
    except IRedMailError as exc:
        messages.error(request, f"Could not delete {record.domain}: {exc}")
        return redirect("email-domains")
    domain_name = record.domain
    record.delete()
    messages.success(request, f"{domain_name} deleted.")
    return redirect("email-domains")


@login_required
@require_POST
def key_create(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")
    EmailApiKey.objects.filter(account=account).update(is_active=False)
    EmailApiKey.objects.create(account=account, name="default")
    messages.success(request, "New API key generated.")
    return redirect("email-domains")


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

    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    name = (request.POST.get("name") or "").strip()
    try:
        quota_mb = int(request.POST.get("quota_mb") or 1024)
    except ValueError:
        quota_mb = 1024

    if not email or not password:
        messages.error(request, "Email and password are required.")
        return redirect("email-mailboxes")

    domain_part = email.rsplit("@", 1)[-1]
    domain = _scoped(EmailDomain.objects, request, current).filter(
        domain=domain_part, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        messages.error(request, f"'{domain_part}' is not a verified sending domain.")
        return redirect("email-mailboxes")

    # The mailbox belongs to whichever tenant owns the domain.
    account = domain.account

    if Mailbox.objects.filter(email=email).exists():
        messages.error(request, "That mailbox already exists.")
        return redirect("email-mailboxes")

    if not admin:
        from apps.billing.limits import LimitChecker, PlanLimitExceeded
        try:
            LimitChecker(account).check_mailbox()
        except PlanLimitExceeded as exc:
            messages.error(request, str(exc))
            return redirect("email-mailboxes")

    mb = Mailbox.objects.create(
        account=account, domain=domain, email=email, name=name, quota_mb=quota_mb
    )
    transaction.on_commit(lambda: provision_mailbox.delay(mb.id, password))
    messages.success(request, f"Provisioning {email}…")
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_delete(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    try:
        IRedMailClient().delete_mailbox(mb.email)
        mb.delete()
        messages.success(request, "Mailbox deleted.")
    except IRedMailError as exc:
        messages.error(request, f"Delete failed: {exc}")
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_password(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    password = request.POST.get("password") or ""
    if not password:
        messages.error(request, "Password is required.")
        return redirect("email-mailboxes")
    try:
        IRedMailClient().change_password(mb.email, password)
        messages.success(request, f"Password updated for {mb.email}.")
    except IRedMailError as exc:
        messages.error(request, f"Password change failed: {exc}")
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_quota(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    try:
        quota_mb = int(request.POST.get("quota_mb") or mb.quota_mb)
    except ValueError:
        messages.error(request, "Quota must be a number (MB).")
        return redirect("email-mailboxes")
    try:
        IRedMailClient().update_quota(mb.email, quota_mb)
        mb.quota_mb = quota_mb
        mb.save(update_fields=["quota_mb"])
        messages.success(request, f"Quota updated for {mb.email}.")
    except IRedMailError as exc:
        messages.error(request, f"Quota update failed: {exc}")
    return redirect("email-mailboxes")


@login_required
@require_POST
def alias_create(request):
    admin = _is_admin(request)
    current = get_current_account(request)
    if current is None and not admin:
        return redirect("dashboard")

    address = (request.POST.get("address") or "").strip().lower()
    goto = (request.POST.get("goto") or "").strip().lower()
    if not address or not goto:
        messages.error(request, "Both the alias and forwarding address are required.")
        return redirect("email-mailboxes")

    domain_part = address.rsplit("@", 1)[-1]
    domain = _scoped(EmailDomain.objects, request, current).filter(
        domain=domain_part, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        messages.error(request, f"'{domain_part}' is not a verified sending domain.")
        return redirect("email-mailboxes")

    try:
        IRedMailClient().add_alias(address, goto)
        EmailAlias.objects.create(
            account=domain.account, domain=domain, address=address, goto=goto
        )
        messages.success(request, f"Alias {address} → {goto} created.")
    except IRedMailError as exc:
        messages.error(request, f"Alias creation failed: {exc}")
    return redirect("email-mailboxes")


# --- Transactional send API ---------------------------------------------------

def _authenticate(request):
    """Resolve the EmailApiKey from the request, or return None."""
    header = request.headers.get("X-Api-Key") or ""
    if not header:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            header = auth.removeprefix("Bearer ").strip()
    if not header:
        return None
    return EmailApiKey.objects.filter(key=header, is_active=True).select_related(
        "account"
    ).first()


@csrf_exempt
@require_POST
def api_send(request):
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

    from_domain = from_email.rsplit("@", 1)[-1].lower()
    domain = EmailDomain.objects.filter(
        account=account, domain=from_domain, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        return JsonResponse(
            {"error": f"'{from_domain}' is not a verified sending domain for this account"},
            status=403,
        )

    from apps.billing.limits import LimitChecker, PlanLimitExceeded
    try:
        LimitChecker(account).check_email()
    except PlanLimitExceeded as exc:
        return JsonResponse({"error": str(exc)}, status=403)

    msg = EmailMessage.objects.create(
        account=account,
        domain=domain,
        from_email=from_email,
        to_email=to_email,
        subject=subject,
    )
    api_key.touch()
    transaction.on_commit(
        lambda: send_email.delay(msg.id, text_body=text_body, html_body=html_body)
    )
    return JsonResponse({"id": msg.id, "status": msg.status}, status=202)
