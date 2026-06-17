from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Account
from apps.billing.limits import LimitChecker, PlanLimitExceeded
from apps.billing.models import Plan, Subscription, UsageSummary


@pytest.fixture
def account(db):
    return Account.objects.create(company_name="Limit Co")


def _active_sub(account, **plan_kwargs):
    defaults = dict(slug="p", name="P", price_monthly=Decimal("10"), max_emails_per_month=2)
    defaults.update(plan_kwargs)
    plan = Plan.objects.create(**defaults)
    return Subscription.objects.create(
        account=account,
        plan=plan,
        status=Subscription.ACTIVE,
        current_period_start=timezone.now(),
    )


@pytest.mark.django_db
def test_no_subscription_blocks(account):
    with pytest.raises(PlanLimitExceeded):
        LimitChecker(account).check_email()


@pytest.mark.django_db
def test_email_quota_enforced(account):
    _active_sub(account, max_emails_per_month=2)
    # Under the cap: allowed.
    LimitChecker(account).check_email()
    # At the cap: blocked.
    UsageSummary.increment_emails(account)
    UsageSummary.increment_emails(account)
    with pytest.raises(PlanLimitExceeded):
        LimitChecker(account).check_email()


@pytest.mark.django_db
def test_unlimited_quota_never_blocks(account):
    _active_sub(account, max_emails_per_month=-1)
    UsageSummary.increment_emails(account)
    LimitChecker(account).check_email()  # no raise


@pytest.mark.django_db
def test_feature_gate(account):
    _active_sub(account, tracking_webhooks=False)
    checker = LimitChecker(account)
    assert checker.has_feature("tracking_webhooks") is False
    with pytest.raises(PlanLimitExceeded):
        checker.require_feature("tracking_webhooks", "tracking")
