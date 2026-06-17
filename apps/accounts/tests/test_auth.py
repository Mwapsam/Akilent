import pytest
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import Account, Membership
from apps.billing.models import Plan, Subscription

SIGNUP_URL = "/signup/"


@pytest.fixture
def trial_plan(db):
    # Present so the auto_create_trial signal attaches a trial subscription.
    return Plan.objects.create(slug=Plan.TRIAL, name="Trial", price_monthly=0, trial_days=14)


@pytest.mark.django_db
def test_signup_creates_inactive_user_and_sends_email(client, trial_plan):
    resp = client.post(
        SIGNUP_URL,
        {
            "username": "newuser",
            "email": "new@example.com",
            "company_name": "New Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    assert resp.status_code == 200  # "verify your email" page

    user = User.objects.get(username="newuser")
    assert user.is_active is False
    account = Account.objects.get(company_name="New Co")
    assert Membership.objects.filter(user=user, account=account, role=Membership.Role.OWNER).exists()
    # Trial subscription was attached by the signal.
    assert Subscription.objects.filter(account=account).exists()
    # A verification email went out.
    assert len(mail.outbox) == 1
    assert "new@example.com" in mail.outbox[0].to


@pytest.mark.django_db
def test_inactive_user_cannot_log_in(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "username": "pending",
            "email": "pending@example.com",
            "company_name": "Pending Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    logged_in = client.login(username="pending", password="Sup3r-secret-pw")
    assert logged_in is False


@pytest.mark.django_db
def test_verify_email_activates_user(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "username": "confirmme",
            "email": "confirm@example.com",
            "company_name": "Confirm Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    user = User.objects.get(username="confirmme")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    resp = client.get(reverse("verify_email", kwargs={"uidb64": uid, "token": token}))
    assert resp.status_code == 302
    user.refresh_from_db()
    assert user.is_active is True


@pytest.mark.django_db
def test_verify_email_rejects_bad_token(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "username": "badtoken",
            "email": "bad@example.com",
            "company_name": "Bad Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    user = User.objects.get(username="badtoken")
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    resp = client.get(reverse("verify_email", kwargs={"uidb64": uid, "token": "wrong-token"}))
    assert resp.status_code == 400
    user.refresh_from_db()
    assert user.is_active is False
