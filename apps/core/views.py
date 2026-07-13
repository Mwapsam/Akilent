import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Account
from apps.billing.models import Plan, Subscription, UsageSummary
from apps.core import docs as docs_kb
from apps.core import help as help_kb
from apps.core.models import SiteSettings
from apps.core.utils import admin_required

User = get_user_model()
logger = logging.getLogger(__name__)


# --- Customers ----------------------------------------------------------------

@admin_required
def customers(request):
    accounts = (
        Account.objects.all()
        .select_related("subscription", "subscription__plan")
        .annotate(member_count=Count("memberships", distinct=True))
        .order_by("company_name")
    )
    rows = [{
        "account": a,
        "owner": a.owner,
        "members": a.member_count,
        "subscription": getattr(a, "subscription", None),
        "emails_used": UsageSummary.get_current_email_usage(a),
    } for a in accounts]

    return render(request, "core/customers.html", {
        "rows": rows,
        "plans": Plan.objects.all().order_by("price_monthly"),
        "statuses": Subscription.STATUS_CHOICES,
    })


@admin_required
@require_POST
def customer_toggle(request, pk):
    a = get_object_or_404(Account, pk=pk)
    a.is_active = not a.is_active
    a.save(update_fields=["is_active"])
    messages.success(
        request, f"{a.company_name} {'activated' if a.is_active else 'deactivated'}."
    )
    return redirect("core:customers")


@admin_required
@require_POST
def customer_subscription(request, pk):
    a = get_object_or_404(Account, pk=pk)
    plan_id = request.POST.get("plan") or None
    plan = Plan.objects.filter(pk=plan_id).first() if plan_id else None
    status = request.POST.get("status")
    if plan is None or status not in dict(Subscription.STATUS_CHOICES):
        messages.error(request, "Pick a valid plan and status.")
        return redirect("core:customers")

    now = timezone.now()
    sub, created = Subscription.objects.get_or_create(
        account=a,
        defaults={"plan": plan, "status": status, "current_period_start": now},
    )
    if not created:
        sub.plan = plan
        sub.status = status
        if status == Subscription.CANCELLED:
            sub.cancelled_at = sub.cancelled_at or now
        elif status in (Subscription.ACTIVE, Subscription.TRIALING):
            sub.cancelled_at = None
            if not sub.current_period_end or sub.current_period_end < now:
                sub.current_period_end = now + timedelta(days=30)
        sub.save()
    messages.success(
        request, f"{a.company_name}: subscription set to {plan.name} ({status})."
    )
    return redirect("core:customers")


# --- Settings -----------------------------------------------------------------

@admin_required
def settings_page(request):
    site = SiteSettings.load()

    if request.method == "POST":
        site.app_name = (request.POST.get("app_name") or "Automator").strip() or "Automator"
        site.support_email = (request.POST.get("support_email") or "").strip()
        site.whatsapp_enabled = "whatsapp_enabled" in request.POST
        site.bitrix_enabled = "bitrix_enabled" in request.POST
        site.signups_enabled = "signups_enabled" in request.POST
        site.payments_enabled = "payments_enabled" in request.POST
        dp = request.POST.get("default_plan") or None
        site.default_plan = Plan.objects.filter(pk=dp).first() if dp else None
        try:
            site.default_trial_days = max(0, int(request.POST.get("default_trial_days") or 0))
        except ValueError:
            pass
        if request.FILES.get("logo"):
            site.logo = request.FILES["logo"]
        site.save()
        messages.success(request, "Settings saved.")
        return redirect("core:settings")

    return render(request, "core/settings.html", {
        "plans": Plan.objects.all().order_by("price_monthly"),
        "admins": User.objects.order_by("-is_superuser", "username"),
    })


# --- Help center (public knowledge base) --------------------------------------

def help_index(request):
    q = (request.GET.get("q") or "").strip()
    results = help_kb.search(q) if q else None
    return render(request, "help/index.html", {
        "q": q,
        "results": results,
        "categories": help_kb.grouped(),
    })


def help_article(request, slug):
    article = help_kb.get_article(slug)
    if article is None:
        raise Http404("No such help article")
    return render(request, "help/article.html", {
        "article": article,
        "related": help_kb.related_to(article),
    })


# --- Developer docs (public) ---------------------------------------------------

def docs_page(request, slug="index"):
    from django.conf import settings

    page = docs_kb.get_page(slug)
    if page is None:
        raise Http404("No such docs page")
    prev_page, next_page = docs_kb.neighbors(page)
    return render(request, page.template, {
        "page": page,
        "pages": docs_kb.PAGES,
        "prev_page": prev_page,
        "next_page": next_page,
        "smtp_relay_host": settings.SMTP_RELAY_HOST,
        "smtp_relay_port": settings.SMTP_RELAY_PORT,
    })


@admin_required
@require_POST
def user_toggle_admin(request, pk):
    u = get_object_or_404(User, pk=pk)
    if u == request.user:
        messages.error(request, "You can't change your own admin status.")
        return redirect("core:settings")
    u.is_superuser = not u.is_superuser
    if u.is_superuser:
        u.is_staff = True
    u.save(update_fields=["is_superuser", "is_staff"])
    messages.success(
        request,
        f"{u.get_username()} is {'now a platform admin' if u.is_superuser else 'no longer an admin'}.",
    )
    return redirect("core:settings")
