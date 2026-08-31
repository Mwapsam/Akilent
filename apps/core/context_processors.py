from django.conf import settings


def site_context(request):
    """Expose branding + effective feature flags to every template.

    Effective WhatsApp = env flag AND the admin's UI toggle (the UI can
    only further disable, since URLs/Celery are wired from env at boot).
    Defensive: never breaks rendering if the table isn't migrated yet.
    """
    wa = settings.WHATSAPP_ENABLED
    site = None
    signups = True
    try:
        from apps.core.models import SiteSettings

        site = SiteSettings.load()
        wa = wa and site.whatsapp_enabled
        signups = site.signups_enabled
    except Exception:
        pass

    return {
        "site": site,
        "WHATSAPP_ENABLED": wa,
        "SIGNUPS_ENABLED": signups,
    }
