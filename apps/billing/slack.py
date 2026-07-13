import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def post_message(text: str) -> None:
    """Post ``text`` to the configured Slack incoming webhook.

    A no-op (debug-logged) if SLACK_WEBHOOK_URL isn't set, and never raises —
    Slack being down must not block payment review, which also goes out by
    email.
    """
    url = getattr(settings, "SLACK_WEBHOOK_URL", "")
    if not url:
        logger.debug("slack.post_message: SLACK_WEBHOOK_URL not configured, skipping")
        return

    try:
        response = requests.post(url, json={"text": text}, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("slack.post_message: failed to post to Slack")
