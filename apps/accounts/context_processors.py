from django.conf import settings


def feature_flags(request):
    """Expose the soft-disable feature flags to all templates."""
    return {
        "WHATSAPP_ENABLED": settings.WHATSAPP_ENABLED,
        "BITRIX_ENABLED": settings.BITRIX_ENABLED,
    }
