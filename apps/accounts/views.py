import logging

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from apps.accounts.forms import SignupForm
from apps.accounts.models import Account, Membership
from apps.accounts.utils import get_current_account, set_current_account

logger = logging.getLogger(__name__)


def signup(request):
    """Self-service signup: create a User, an Account, and an owner Membership.

    The trial Subscription is created by the billing post_save signal on Account.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.email = form.cleaned_data["email"]
                user.save()

                account = Account.objects.create(
                    company_name=form.cleaned_data["company_name"],
                )
                Membership.objects.create(
                    user=user, account=account, role=Membership.Role.OWNER
                )

            login(request, user)
            set_current_account(request, account)
            logger.info("signup: created account %s for user %s", account.pk, user.pk)
            return redirect("onboarding")
    else:
        form = SignupForm()

    return render(request, "accounts/signup.html", {"form": form})


def landing(request):
    """Public marketing landing page."""
    from apps.billing.models import Plan

    plans = Plan.objects.filter(is_active=True).order_by("price_monthly")
    return render(request, "accounts/landing.html", {"plans": plans})


def _onboarding_state(account):
    """Compute the onboarding checklist for an account."""
    from django.conf import settings

    from apps.email.models import EmailApiKey, EmailDomain

    has_verified_domain = EmailDomain.objects.filter(
        account=account, status=EmailDomain.Status.VERIFIED
    ).exists()
    has_domain = has_verified_domain or EmailDomain.objects.filter(account=account).exists()
    has_key = EmailApiKey.objects.filter(account=account, is_active=True).exists()

    steps = [
        {
            "key": "account",
            "title": "Create your account",
            "desc": "Your workspace is ready.",
            "done": True,
            "url": None,
            "cta": None,
        },
    ]

    if settings.WHATSAPP_ENABLED:
        from apps.whatsapp.models.tenant import WhatsAppBusinessNumber

        steps.append({
            "key": "whatsapp",
            "title": "Connect a WhatsApp number",
            "desc": "Register your phone number ID and access token to start messaging.",
            "done": WhatsAppBusinessNumber.objects.filter(account=account).exists(),
            "url": "/whatsapp/numbers/",
            "cta": "Add number",
        })

    steps += [
        {
            "key": "email_domain",
            "title": "Add a sending domain",
            "desc": "Provision a domain and verify its DKIM to send transactional email.",
            "done": has_domain,
            "url": "/email/domains/",
            "cta": "Add domain",
        },
        {
            "key": "email_key",
            "title": "Generate an email API key",
            "desc": "Use it to send via POST /email/send/.",
            "done": has_key,
            "url": "/email/domains/",
            "cta": "Create key",
        },
    ]
    complete = all(s["done"] for s in steps)
    done_count = sum(1 for s in steps if s["done"])
    return steps, complete, done_count


@login_required
def onboarding(request):
    account = get_current_account(request)
    if account is None:
        return redirect("signup")

    steps, complete, done_count = _onboarding_state(account)
    return render(request, "accounts/onboarding.html", {
        "account": account,
        "steps": steps,
        "complete": complete,
        "done_count": done_count,
        "total": len(steps),
    })


@login_required
def dashboard(request):
    account = get_current_account(request)
    if account is None:
        # Authenticated user with no tenant (e.g. a staff-only admin user).
        return render(request, "accounts/dashboard.html", {"account": None})

    from django.conf import settings

    numbers = []
    if settings.WHATSAPP_ENABLED:
        from apps.whatsapp.models.tenant import WhatsAppBusinessNumber

        numbers = WhatsAppBusinessNumber.objects.filter(account=account).order_by(
            "phone_number_id"
        )

    email_domains = []
    try:
        from apps.email.models import EmailDomain

        email_domains = list(
            EmailDomain.objects.filter(account=account).order_by("domain")
        )
    except Exception:
        pass

    subscription = getattr(account, "subscription", None)
    bitrix_connection = getattr(account, "bitrix_connection", None)

    _steps, onboarding_complete, done_count = _onboarding_state(account)

    return render(
        request,
        "accounts/dashboard.html",
        {
            "account": account,
            "numbers": numbers,
            "email_domains": email_domains,
            "subscription": subscription,
            "bitrix_connection": bitrix_connection,
            "onboarding_complete": onboarding_complete,
            "onboarding_done": done_count,
            "onboarding_total": len(_steps),
        },
    )
