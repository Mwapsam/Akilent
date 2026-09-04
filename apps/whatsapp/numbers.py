"""Account-scoped self-service management of WhatsApp Business numbers.

Replaces the former staff-only tenant CRUD: an account owner registers and
manages their own numbers (phone_number_id + access token) from the dashboard.
"""

import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_POST

from apps.accounts.utils import get_current_account
from apps.whatsapp.models import MessageLog
from apps.whatsapp.models.tenant import WhatsAppBusinessNumber

logger = logging.getLogger(__name__)


@login_required
def numbers_list(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")

    numbers = list(
        WhatsAppBusinessNumber.objects.filter(account=account).order_by(
            "phone_number_id"
        )
    )
    embedded_enabled = bool(settings.WHATSAPP_APP_ID and settings.WHATSAPP_CONFIG_ID)

    active_number = next(
        (n for n in numbers if n.is_active and n.access_token), None
    )
    # Embedded-Signup numbers store a PIN once registered on the Cloud API;
    # a manually-entered number legitimately has none.
    needs_registration = bool(
        active_number and active_number.waba_id and not active_number.verification_pin
    )

    try:
        from apps.billing.api import has_feature

        module_enabled = has_feature(account, "whatsapp")
    except Exception:  # pragma: no cover - billing optional in some setups
        module_enabled = False

    inbound_seen = MessageLog.objects.filter(
        account=account, direction=MessageLog.Direction.INBOUND
    ).exists()

    try:
        webhook_url = request.build_absolute_uri(reverse("whatsapp-webhook"))
    except NoReverseMatch:  # urls only mounted when WHATSAPP_ENABLED
        webhook_url = request.build_absolute_uri("/whatsapp/webhook/")

    steps = [
        {
            "label": "Connect a WhatsApp Business number",
            "done": bool(numbers),
            "hint": "Use “Connect with WhatsApp” below, or add one manually.",
        },
        {
            "label": "Number ready to send",
            "done": bool(active_number) and not needs_registration,
            "hint": "An active number with an access token, registered on the Cloud API.",
        },
        {
            "label": "Receiving messages from customers",
            "done": inbound_seen,
            "hint": "Send a message to the number from a phone to confirm inbound works.",
        },
        {
            "label": "WhatsApp enabled on your plan",
            "done": module_enabled,
            "hint": "Managed in billing / by your account admin.",
        },
    ]

    return render(
        request,
        "whatsapp/numbers.html",
        {
            "account": account,
            "numbers": numbers,
            "embedded_enabled": embedded_enabled,
            "wa_app_id": settings.WHATSAPP_APP_ID,
            "wa_config_id": settings.WHATSAPP_CONFIG_ID,
            "wa_graph_version": settings.WHATSAPP_GRAPH_VERSION,
            "steps": steps,
            "onboarding_complete": all(s["done"] for s in steps),
            "active_number": active_number,
            "needs_registration": needs_registration,
            "module_enabled": module_enabled,
            "webhook_url": webhook_url,
            "show_webhook_setup": request.user.is_staff,
        },
    )


@login_required
@require_POST
def connect_complete(request):
    """Finish Embedded Signup: exchange the code and store the number.

    Called by the front end after Meta's Embedded Signup popup returns an auth
    ``code`` plus the ``phone_number_id`` / ``waba_id`` session info.
    """
    account = get_current_account(request)
    if account is None:
        return JsonResponse({"error": "No account"}, status=400)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    code = (body.get("code") or "").strip()
    phone_number_id = (body.get("phone_number_id") or "").strip()
    waba_id = (body.get("waba_id") or "").strip()
    business_id = (body.get("business_id") or "").strip()

    if not code or not phone_number_id:
        return JsonResponse(
            {"error": "Onboarding did not return a code and phone number. Try again."},
            status=400,
        )

    existing = WhatsAppBusinessNumber.objects.filter(
        phone_number_id=phone_number_id
    ).first()
    if existing and existing.account_id != account.pk:
        return JsonResponse(
            {"error": "This WhatsApp number is already connected to another account."},
            status=409,
        )

    if existing is None:
        from apps.billing.limits import LimitChecker, PlanLimitExceeded
        try:
            LimitChecker(account).check_whatsapp_number()
        except PlanLimitExceeded as exc:
            return JsonResponse({"error": str(exc)}, status=403)

    import secrets

    from apps.whatsapp.embedded import (
        EmbeddedSignupError,
        exchange_code_for_token,
        register_phone_number,
        subscribe_app_to_waba,
    )

    try:
        token = exchange_code_for_token(code)
    except EmbeddedSignupError as exc:
        logger.error("connect_complete: token exchange failed: %s", exc)
        return JsonResponse({"error": f"Could not complete onboarding: {exc}"}, status=502)

    try:
        subscribe_app_to_waba(waba_id, token)
    except Exception as exc:  # best-effort; don't block the connection
        logger.warning("connect_complete: subscribe failed: %s", exc)

    # Register the number on the Cloud API so it can send (Tech Provider flow).
    pin = f"{secrets.randbelow(1_000_000):06d}"
    try:
        register_phone_number(phone_number_id, token, pin)
    except Exception as exc:  # best-effort; owner can retry from the dashboard
        logger.warning("connect_complete: phone registration failed: %s", exc)
        pin = None

    WhatsAppBusinessNumber.objects.update_or_create(
        phone_number_id=phone_number_id,
        defaults={
            "account": account,
            "access_token": token,
            "waba_id": waba_id or None,
            "business_id": business_id or None,
            "verification_pin": pin,
        },
    )
    logger.info(
        "connect_complete: connected number %s for account %s",
        phone_number_id, account.pk,
    )
    from apps.accounts import onboarding as ob
    from apps.accounts.models import Account

    was_onboarding = account.onboarding_state != Account.Onboarding.COMPLETED
    next_url = ob.advance_onboarding(account)
    if not next_url:
        next_url = "/onboarding/" if was_onboarding else "/whatsapp/numbers/"
    return JsonResponse({"ok": True, "redirect": next_url})


@login_required
def numbers_create(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")

    if request.method != "POST":
        return redirect("whatsapp-numbers")

    phone_number_id = (request.POST.get("phone_number_id") or "").strip()
    access_token = (request.POST.get("access_token") or "").strip()
    if not phone_number_id:
        messages.error(request, "phone_number_id is required.")
        return redirect("whatsapp-numbers")

    from apps.billing.limits import LimitChecker, PlanLimitExceeded

    try:
        LimitChecker(account).check_whatsapp_number()
    except PlanLimitExceeded as exc:
        messages.error(request, str(exc))
        return redirect("whatsapp-numbers")

    if WhatsAppBusinessNumber.objects.filter(phone_number_id=phone_number_id).exists():
        messages.error(request, "This phone_number_id is already registered.")
        return redirect("whatsapp-numbers")

    WhatsAppBusinessNumber.objects.create(
        account=account,
        phone_number_id=phone_number_id,
        access_token=access_token or None,
        waba_id=(request.POST.get("waba_id") or "").strip() or None,
        business_id=(request.POST.get("business_id") or "").strip() or None,
        display_number=(request.POST.get("display_number") or "").strip() or None,
    )
    messages.success(request, f"WhatsApp number {phone_number_id} registered.")

    from apps.accounts import onboarding as ob
    from apps.accounts.models import Account

    was_onboarding = account.onboarding_state != Account.Onboarding.COMPLETED
    next_url = ob.advance_onboarding(account)
    if next_url:
        return redirect(next_url)
    return redirect("onboarding" if was_onboarding else "whatsapp-numbers")


@login_required
def numbers_delete(request, pk):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("whatsapp-numbers")

    number = get_object_or_404(WhatsAppBusinessNumber, pk=pk, account=account)
    number.delete()
    messages.success(request, "WhatsApp number removed.")
    return redirect("whatsapp-numbers")
