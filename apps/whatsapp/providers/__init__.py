"""WhatsApp provider factory.

Resolves the configured backend at runtime via Django settings.
Currently only Meta Cloud API is supported; additional providers
can be added by implementing WhatsAppProvider ABC.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

from apps.whatsapp.providers.base import WhatsAppProvider, WhatsAppProviderError
from apps.whatsapp.providers.meta import MetaCloudAPIProvider

_ALIASES: dict[str, str] = {
    "meta": "apps.whatsapp.providers.meta.MetaCloudAPIProvider",
}


def get_whatsapp_provider(account) -> WhatsAppProvider:
    """Return a WhatsAppProvider instance for the given account.

    Args:
        account: An apps.accounts.Account instance.

    Returns:
        A WhatsAppProvider implementation (currently MetaCloudAPIProvider).

    Raises:
        WhatsAppProviderError: if no active business number is configured.
    """
    from apps.whatsapp.models import WhatsAppBusinessNumber

    # Get the first active business number for this account
    number = WhatsAppBusinessNumber.objects.filter(
        account=account,
        is_active=True,
    ).first()

    if not number:
        raise WhatsAppProviderError(
            f"No active WhatsApp Business Number configured for account {account.slug}"
        )

    if not number.access_token:
        raise WhatsAppProviderError(
            f"Business number {number.phone_number_id} is missing access token"
        )

    # Currently hardcoded to Meta; future versions could make this configurable
    # via WHATSAPP_PROVIDER_BACKEND Django setting or database configuration
    return MetaCloudAPIProvider(
        access_token=number.access_token,
        phone_number_id=number.phone_number_id,
    )


__all__ = [
    "get_whatsapp_provider",
    "WhatsAppProvider",
    "WhatsAppProviderError",
    "MetaCloudAPIProvider",
]
