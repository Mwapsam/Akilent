from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Account
from apps.billing.models import Plan, Subscription


@pytest.fixture
def plan(db):
    return Plan.objects.create(
        slug="starter",
        name="Starter",
        price_monthly=Decimal("19.00"),
        max_emails_per_month=10000,
        tracking_webhooks=False,
    )


@pytest.fixture
def account(db):
    # No Trial plan exists in the test DB, so the auto_create_trial signal is a
    # no-op here — we attach subscriptions explicitly in each test.
    return Account.objects.create(company_name="Acme Inc")


@pytest.fixture
def subscription(db, account, plan):
    return Subscription.objects.create(
        account=account,
        plan=plan,
        status=Subscription.PAST_DUE,
        current_period_start=timezone.now(),
    )
