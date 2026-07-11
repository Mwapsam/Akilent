import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from apps.accounts.forms import SignupForm
from apps.accounts.models import Account, Membership
from apps.accounts.utils import get_current_account, set_current_account

logger = logging.getLogger(__name__)


def _send_verification_email(request, user):
    """Queue an email with a tokened link to activate their account.

    Sent via Celery rather than inline so a slow/unreachable mail server
    retries in the background instead of 500ing the request — the account
    (User/Account/Membership) is already committed by the time this runs.
    """
    from apps.accounts.tasks import send_verification_email
    from apps.core.models import SiteSettings

    site_name = SiteSettings.load().app_name or "Automator"
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("verify_email", kwargs={"uidb64": uid, "token": token})
    link = request.build_absolute_uri(path)

    send_verification_email.delay(user.pk, site_name, link)


def signup(request):
    """Self-service signup: create a User, an Account, and an owner Membership.

    The user is created inactive and must confirm their email before they can
    sign in (Django's auth backend refuses inactive users, which gates all
    provisioning and sending). The trial Subscription is created by the billing
    post_save signal on Account.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    from apps.core.models import SiteSettings
    if not SiteSettings.load().signups_enabled:
        messages.error(request, "Public sign-ups are currently disabled. Contact us if you need access.")
        return redirect(f"{reverse('landing')}#pricing")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.email = form.cleaned_data["email"]
                # Email is the login credential (EmailBackend); username is
                # just an internal identifier Django's User model requires.
                user.username = form.cleaned_data["email"][:150]
                user.is_active = False
                user.save()

                account = Account.objects.create(
                    company_name=form.cleaned_data["company_name"],
                )
                Membership.objects.create(
                    user=user, account=account, role=Membership.Role.OWNER
                )
                _apply_selected_plan(account, form.cleaned_data.get("plan"))

            _send_verification_email(request, user)
            logger.info(
                "signup: created inactive account %s for user %s", account.pk, user.pk
            )
            return render(request, "accounts/verify_email_sent.html", {"email": user.email})
    else:
        form = SignupForm(initial={"plan": request.GET.get("plan", "")})

    return render(request, "accounts/signup.html", {"form": form})


def _apply_selected_plan(account, plan_slug):
    """Honor the plan chosen on the landing page's pricing cards, if any.

    The trial Subscription is auto-created by the billing post_save signal;
    this just swaps its plan so a "Choose Professional" click doesn't
    silently land the user on the default/trial plan instead.
    """
    if not plan_slug:
        return
    from apps.billing.models import Plan

    plan = Plan.objects.filter(slug=plan_slug, is_active=True).first()
    if plan is None:
        return
    subscription = getattr(account, "subscription", None)
    if subscription is None:
        return
    subscription.plan = plan
    if plan.trial_days:
        subscription.trial_ends_at = timezone.now() + timedelta(days=plan.trial_days)
        subscription.status = subscription.__class__.TRIALING
    else:
        subscription.trial_ends_at = None
        subscription.status = subscription.__class__.ACTIVE
    subscription.save(update_fields=["plan", "trial_ends_at", "status"])


def verify_email(request, uidb64, token):
    """Activate a user from a signup verification link and sign them in."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and user.is_active:
        # Already verified — send them to sign in rather than error.
        return redirect("login")

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=["is_active"])
        login(request, user, backend="apps.accounts.backends.EmailBackend")
        membership = Membership.objects.filter(user=user).select_related("account").first()
        if membership is not None:
            set_current_account(request, membership.account)
        logger.info("verify_email: activated user %s", user.pk)
        return redirect("onboarding")

    return render(request, "accounts/verify_email_invalid.html", status=400)


def resend_verification(request):
    """Let a user request a fresh verification link if the first one was lost or expired.

    Avoids confirming/denying account existence to a third party by always
    showing the same "check your email" outcome for a not-found or already
    inactive account; an already-active account is sent straight to sign in
    since that's more helpful than a silent no-op.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        user = User.objects.filter(email__iexact=email).first()
        if user is not None and user.is_active:
            messages.info(request, "That account is already verified — sign in below.")
            return redirect("login")
        if user is not None and not user.is_active:
            _send_verification_email(request, user)
            logger.info("resend_verification: resent link to user %s", user.pk)
        return render(request, "accounts/verify_email_sent.html", {"email": email})

    return render(request, "accounts/resend_verification.html", {
        "email": request.GET.get("email", ""),
    })


def landing(request):
    """Public marketing landing page."""
    from apps.billing.models import Plan

    plans = Plan.objects.filter(is_active=True).order_by("price_monthly")
    return render(request, "accounts/landing.html", {"plans": plans})


@login_required
def onboarding(request):
    from apps.accounts import onboarding as ob

    account = get_current_account(request)
    if account is None:
        return redirect("signup")

    state = ob.get_state(account)
    return render(request, "accounts/onboarding.html", {
        "account": account,
        **state,
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

    from apps.accounts import onboarding as ob

    state = ob.get_state(account)
    stats = _email_stats(account, subscription)

    return render(
        request,
        "accounts/dashboard.html",
        {
            "account": account,
            "numbers": numbers,
            "email_domains": email_domains,
            "subscription": subscription,
            "bitrix_connection": bitrix_connection,
            "onboarding_complete": state["complete"],
            "onboarding_done": state["required_done"],
            "onboarding_total": state["required_total"],
            "onboarding_next": state["next_step"],
            **stats,
        },
    )


def _email_stats(account, subscription):
    """Real-data dashboard aggregates for an account (no open/click tracking)."""
    from django.db.models import Count, Q
    from django.utils import timezone

    from apps.email.models import EmailDomain, EmailMessage, Mailbox

    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    msgs = EmailMessage.objects.filter(account=account)
    month = msgs.filter(created_at__gte=month_start).aggregate(
        sent=Count("id", filter=Q(status=EmailMessage.Status.SENT)),
        failed=Count("id", filter=Q(status=EmailMessage.Status.FAILED)),
        queued=Count("id", filter=Q(status=EmailMessage.Status.QUEUED)),
    )
    attempted = month["sent"] + month["failed"]
    success_rate = round(month["sent"] / attempted * 100) if attempted else None

    domains = EmailDomain.objects.filter(account=account).aggregate(
        total=Count("id"),
        verified=Count("id", filter=Q(status=EmailDomain.Status.VERIFIED)),
    )
    mailboxes = Mailbox.objects.filter(account=account).aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status=Mailbox.Status.ACTIVE)),
    )

    emails_used = month["sent"] + month["failed"] + month["queued"]
    email_quota = getattr(getattr(subscription, "plan", None), "max_emails_per_month", None)
    usage_pct = None
    if email_quota and email_quota > 0:
        usage_pct = min(round(emails_used / email_quota * 100), 100)

    return {
        "sent_month": month["sent"],
        "failed_month": month["failed"],
        "success_rate_display": f"{success_rate}%" if success_rate is not None else "—",
        "failed_sub": f"{month['failed']} failed this month",
        "domains_verified": domains["verified"],
        "domains_sub": f"of {domains['total']} total",
        "mailboxes_active": mailboxes["active"],
        "mailboxes_sub": f"of {mailboxes['total']} total",
        "emails_used": emails_used,
        "email_quota": email_quota,
        "usage_pct": usage_pct,
        "recent_sends": list(msgs.order_by("-created_at")[:6]),
    }
