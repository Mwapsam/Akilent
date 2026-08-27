"""AI provider factory.

PHASE 3 STUB: This factory is defined but no concrete implementations exist yet.
Phase 5 will add Anthropic Claude as the first implementation.

Usage (Phase 5+):
    from apps.ai.providers import get_ai_provider
    provider = get_ai_provider(account)
    result = provider.complete(prompt="What is 2+2?", system="You are a math tutor.")
"""
import logging

logger = logging.getLogger(__name__)

from apps.ai.providers.base import AIProvider, AIProviderError

_ALIASES: dict[str, str] = {
    # Phase 5: add implementations
    # "anthropic": "apps.ai.providers.anthropic.AnthropicProvider",
}


def get_ai_provider(account) -> AIProvider:
    """Return an AIProvider instance for the given account.

    Args:
        account: An apps.accounts.Account instance.

    Returns:
        An AIProvider implementation.

    Raises:
        AIProviderError: if no provider is configured.

    PHASE 3 STUB: No implementations exist yet.
    """
    raise AIProviderError(
        "AI provider not configured. Phase 5 will add Anthropic Claude implementation."
    )


__all__ = [
    "get_ai_provider",
    "AIProvider",
    "AIProviderError",
]
