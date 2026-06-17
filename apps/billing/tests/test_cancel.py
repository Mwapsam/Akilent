from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.accounts.models import Account, Membership
from apps.billing.models import Plan, Subscription

CANCEL_URL = "/billing/cancel/"


@pytest.fixture
def account(db):
    return Account.objects.create(company_name="Cancel Co")


@pytest.fixture
def active_subscription(db, account):
    plan = Plan.objects.create(slug="starter", name="Starter", price_monthly=Decimal("19"))
    return Subscription.objects.create(
        account=account,
        plan=plan,
        status=Subscription.ACTIVE,
        current_period_start=timezone.now(),
    )


def _member(account, username, role):
    user = User.objects.create_user(username=username, password="pw12345!")
    Membership.objects.create(user=user, account=account, role=role)
    return user


@pytest.mark.django_db
def test_member_cannot_cancel(client, account, active_subscription):
    user = _member(account, "member1", Membership.Role.MEMBER)
    client.force_login(user)
    resp = client.post(CANCEL_URL)
    assert resp.status_code == 302
    active_subscription.refresh_from_db()
    assert active_subscription.status == Subscription.ACTIVE


@pytest.mark.django_db
def test_owner_can_cancel(client, account, active_subscription):
    user = _member(account, "owner1", Membership.Role.OWNER)
    client.force_login(user)
    resp = client.post(CANCEL_URL)
    assert resp.status_code == 302
    active_subscription.refresh_from_db()
    assert active_subscription.status == Subscription.CANCELLED
