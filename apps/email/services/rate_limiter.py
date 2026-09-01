"""Rate limiting for SES sends using token bucket algorithm.

Supports both Redis-backed (distributed) and in-process (single-worker) modes.
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket rate limiter for per-second flow control."""

    def __init__(self, rate: float, capacity: Optional[float] = None):
        """Initialize token bucket.

        Args:
            rate: Tokens per second (e.g., 14 for 14 sends/sec)
            capacity: Max tokens in bucket; defaults to rate (refill in 1 second)
        """
        self.rate = rate
        self.capacity = capacity or rate
        self.tokens = self.capacity
        self.last_refill = time.time()

    def acquire(self, tokens: float = 1.0, blocking: bool = True, timeout: float = 5.0) -> bool:
        """Try to acquire tokens from the bucket.

        Args:
            tokens: Number of tokens to acquire (default 1)
            blocking: Whether to wait until tokens available (up to timeout)
            timeout: Max seconds to wait if blocking=True

        Returns:
            True if tokens acquired, False if timeout or non-blocking and unavailable
        """
        start = time.time()
        while True:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(
                self.capacity,
                self.tokens + elapsed * self.rate
            )
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True

            if not blocking:
                return False

            if time.time() - start >= timeout:
                logger.warning(
                    "TokenBucket.acquire timeout after %.1f sec (rate=%.1f/sec, need %.1f tokens)",
                    timeout, self.rate, tokens
                )
                return False

            time.sleep(0.01)

    def wait_for(self, tokens: float = 1.0) -> float:
        """Blocking acquire with exponential backoff. Returns seconds waited."""
        start = time.time()
        retry_delay = 0.001
        max_delay = 1.0

        while not self.acquire(tokens, blocking=False):
            time.sleep(retry_delay)
            retry_delay = min(max_delay, retry_delay * 2)

        return time.time() - start


def get_ses_rate_limiter() -> TokenBucket:
    """Get or create the global SES rate limiter based on MailProviderSettings."""
    try:
        from apps.core.models import MailProviderSettings

        settings = MailProviderSettings.load()
        rate = settings.ses_send_rate_limit or 14
    except Exception as e:
        logger.warning("Failed to load SES rate limit from settings: %s; using default 14/sec", e)
        rate = 14

    if not hasattr(get_ses_rate_limiter, "_instance"):
        get_ses_rate_limiter._instance = TokenBucket(rate=rate)
    elif get_ses_rate_limiter._instance.rate != rate:
        get_ses_rate_limiter._instance.rate = rate
        logger.debug("Updated SES rate limit to %.1f/sec", rate)

    return get_ses_rate_limiter._instance
