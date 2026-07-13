from datetime import timedelta

from django.utils import timezone

from .models import Subscription


def activate_subscription(account, plan, payment_method: str, *, fw_customer_email=None) -> Subscription:
    """Activate (or renew) an account's subscription to ``plan``.

    Shared by every payment method — Flutterwave's checkout callback and
    webhook, and manual-payment admin approval — so activation semantics
    (period length, status, cancellation reset) stay identical regardless of
    which gateway triggered them.
    """
    now = timezone.now()
    defaults = {
        "plan": plan,
        "status": Subscription.ACTIVE,
        "current_period_start": now,
        "current_period_end": now + timedelta(days=30),
        "payment_method": payment_method,
        "trial_ends_at": None,
        "cancelled_at": None,
    }
    if fw_customer_email:
        defaults["fw_customer_email"] = fw_customer_email

    sub, _ = Subscription.objects.update_or_create(account=account, defaults=defaults)
    return sub
