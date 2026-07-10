"""Onboarding checklist state.

Single source of truth for "where is this workspace in its setup?", shared by
the onboarding page, the dashboard banner, and the always-available floating
widget (via the context processor). Steps reflect the real customer journey:
add a domain → verify DNS → start using email → invite the team.
"""
from django.conf import settings


def get_state(account) -> dict:
    from apps.accounts.models import Invitation, Membership
    from apps.email.models import EmailApiKey, EmailDomain, Mailbox

    has_domain = EmailDomain.objects.filter(account=account).exists()
    has_verified = EmailDomain.objects.filter(
        account=account, status=EmailDomain.Status.VERIFIED
    ).exists()
    # Deep-link straight to the unverified domain's DNS-check panel instead of
    # just the bare list, so the "verify" step doesn't dump the user back at
    # square one.
    pending_domain = (
        EmailDomain.objects.filter(account=account)
        .exclude(status=EmailDomain.Status.VERIFIED)
        .order_by("pk")
        .first()
    )
    verify_url = f"/email/domains/#domain-card-{pending_domain.pk}" if pending_domain else "/email/domains/"
    has_mailbox = Mailbox.objects.filter(account=account).exists()
    has_key = EmailApiKey.objects.filter(account=account, is_active=True).exists()
    has_team = (
        Membership.objects.filter(account=account).count() > 1
        or Invitation.objects.filter(account=account, accepted_at__isnull=True).exists()
    )

    steps = [
        {
            "key": "account", "title": "Create your account",
            "desc": "Your workspace is ready to go.",
            "done": True, "url": None, "cta": None,
            "icon": "check-circle", "optional": False,
        },
    ]

    if settings.WHATSAPP_ENABLED:
        from apps.whatsapp.models.tenant import WhatsAppBusinessNumber

        steps.append({
            "key": "whatsapp", "title": "Connect a WhatsApp number",
            "desc": "Register your phone number ID and token to start messaging.",
            "done": WhatsAppBusinessNumber.objects.filter(account=account).exists(),
            "url": "/whatsapp/numbers/", "cta": "Add number",
            "icon": "chat", "optional": False,
        })

    steps += [
        {
            "key": "domain", "title": "Add a sending domain",
            "desc": "Add the domain you'll send email from, e.g. mail.yourcompany.com.",
            "done": has_domain, "url": "/email/domains/", "cta": "Add domain",
            "icon": "globe", "optional": False,
        },
        {
            "key": "verify", "title": "Verify your domain",
            "desc": "Add the DNS records and run the DNS check to switch sending on.",
            "done": has_verified, "url": verify_url, "cta": "Verify DNS",
            "icon": "check-circle", "optional": False,
        },
        {
            "key": "use", "title": "Create a mailbox or API key",
            "desc": "Make your first mailbox, or generate an API key to send programmatically.",
            "done": has_mailbox or has_key, "url": "/email/mailboxes/", "cta": "Create mailbox",
            "icon": "inbox", "optional": False,
        },
        {
            "key": "team", "title": "Invite your team",
            "desc": "Bring colleagues into your workspace so they can help manage email.",
            "done": has_team, "url": "/settings/team/", "cta": "Invite teammate",
            "icon": "user", "optional": True,
        },
    ]

    required = [s for s in steps if not s["optional"]]
    required_done = sum(1 for s in required if s["done"])
    complete = required_done == len(required)
    # Only essentials drive "next up", so finishing them surfaces the
    # completion state even if an optional step (e.g. inviting the team) remains.
    next_step = next((s for s in steps if not s["done"] and not s["optional"]), None)
    return {
        "steps": steps,
        "complete": complete,
        "required_done": required_done,
        "required_total": len(required),
        "done_count": sum(1 for s in steps if s["done"]),
        "total": len(steps),
        "next_step": next_step,
        "pct": round(required_done / len(required) * 100) if required else 100,
    }
