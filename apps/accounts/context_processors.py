from django.conf import settings


def feature_flags(request):
    """Expose the soft-disable feature flags to all templates."""
    return {
        "WHATSAPP_ENABLED": settings.WHATSAPP_ENABLED,
    }


def onboarding_status(request):
    """Expose onboarding progress for the floating widget + welcome tour.

    Only returns ``onboarding`` while there's still required setup to do, so the
    widget/tour disappear once the workspace is set up. Defensive: never breaks
    rendering (anonymous users, no workspace, un-migrated DB).
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    try:
        from apps.accounts import onboarding as ob
        from apps.accounts.utils import get_current_account

        account = get_current_account(request)
        if account is None:
            return {}
        state = ob.get_state(account)
        if state["complete"]:
            return {}
        return {"onboarding": state}
    except Exception:
        return {}
