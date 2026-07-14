import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender="accounts.Account")
def seed_starter_email_templates(sender, instance, created, **kwargs):
    """Give every newly created account a few ready-to-edit email templates.

    Mirrors apps.billing.signals.auto_create_trial: a post_save receiver on
    Account fires exactly once per real account creation (signup, Django
    admin, shell) and never on invitations to an existing account, since
    those never call Account.objects.create. get_or_create-by-slug keeps it
    idempotent if this ever runs twice for the same account.
    """
    if not created:
        return
    try:
        from apps.email.models import EmailTemplate
        from apps.email.starter_templates import STARTER_TEMPLATES

        for starter in STARTER_TEMPLATES:
            defaults = {k: v for k, v in starter.items() if k != "slug"}
            EmailTemplate.objects.get_or_create(
                account=instance, slug=starter["slug"], defaults=defaults
            )
    except Exception:
        logger.exception(
            "seed_starter_email_templates: failed to seed starter templates for account %s",
            instance.pk,
        )
