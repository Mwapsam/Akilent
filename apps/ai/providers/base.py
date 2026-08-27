"""Provider-agnostic AIProvider interface.

Any LLM backend (Anthropic Claude, OpenAI GPT, Google Gemini, local Llama, ...)
is supported by implementing this ABC. Business logic imports only from here
and from apps.ai.types — never from a concrete provider module.

Design principle: every method returns a typed dataclass from apps.ai.types,
never a raw dict. Adapters belong in the provider, not scattered across the
service layer.

PHASE 3 STUB: This file defines the interface. Phase 5 will add
Anthropic's Claude as the first concrete implementation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompletionResult:
    """Result of an LLM completion call."""

    text: str
    """The generated completion text."""

    model: str
    """Model that generated the completion."""

    usage: dict = None
    """Token usage: {'input_tokens': N, 'output_tokens': M}."""

    error: Optional[str] = None
    """Error message if the call failed."""

    def __post_init__(self):
        if self.usage is None:
            self.usage = {}


class AIProvider(ABC):
    """Abstract interface for an LLM (Large Language Model) backend.

    Implement all abstract methods to add a new provider. The factory in
    apps.ai.providers.__init__ will resolve the concrete class at runtime.

    Threading: instances are not thread-safe. Instantiate one per request,
    per Celery task, or per service call.

    PHASE 3 STUB: This interface is defined but has no implementations.
    Phase 5 will add Anthropic Claude (and optionally others).
    """

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> CompletionResult:
        """Generate a text completion from a prompt.

        Args:
            prompt: User message / question to complete.
            system: System prompt defining the model's behavior.
            max_tokens: Maximum length of the completion.
            temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative).

        Returns:
            CompletionResult with generated text and usage stats.

        Raises:
            AIProviderError: on network failure, rate limit, model error, etc.
        """


class AIProviderError(Exception):
    """Base exception for AI provider errors."""

    pass
