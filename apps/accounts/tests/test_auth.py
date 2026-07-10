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
            "email": "new@example.com",
            "company_name": "New Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    assert resp.status_code == 200  # "verify your email" page

    user = User.objects.get(email="new@example.com")
    assert user.is_active is False
    assert user.username == "new@example.com"  # email is the login credential
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
            "email": "pending@example.com",
            "company_name": "Pending Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    logged_in = client.login(username="pending@example.com", password="Sup3r-secret-pw")
    assert logged_in is False


@pytest.mark.django_db
def test_active_user_can_log_in_with_email(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "email": "active@example.com",
            "company_name": "Active Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    user = User.objects.get(email="active@example.com")
    user.is_active = True
    user.save(update_fields=["is_active"])

    logged_in = client.login(username="active@example.com", password="Sup3r-secret-pw")
    assert logged_in is True


@pytest.mark.django_db
def test_login_view_accepts_email_field(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "email": "viaform@example.com",
            "company_name": "Via Form Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    user = User.objects.get(email="viaform@example.com")
    user.is_active = True
    user.save(update_fields=["is_active"])

    resp = client.post(
        reverse("login"),
        {"username": "viaform@example.com", "password": "Sup3r-secret-pw"},
    )
    assert resp.status_code == 302
    assert resp.url == "/dashboard/"


@pytest.mark.django_db
def test_verify_email_activates_user(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "email": "confirm@example.com",
            "company_name": "Confirm Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    user = User.objects.get(email="confirm@example.com")
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
            "email": "bad@example.com",
            "company_name": "Bad Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    user = User.objects.get(email="bad@example.com")
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    resp = client.get(reverse("verify_email", kwargs={"uidb64": uid, "token": "wrong-token"}))
    assert resp.status_code == 400
    user.refresh_from_db()
    assert user.is_active is False


@pytest.mark.django_db
def test_signup_honors_plan_query_param(client, trial_plan):
    pro_plan = Plan.objects.create(
        slug=Plan.PROFESSIONAL, name="Professional", price_monthly=49, trial_days=14
    )
    client.post(
        SIGNUP_URL + "?plan=professional",
        {
            "email": "pro@example.com",
            "company_name": "Pro Co",
            "plan": "professional",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    account = Account.objects.get(company_name="Pro Co")
    subscription = Subscription.objects.get(account=account)
    assert subscription.plan_id == pro_plan.pk


@pytest.mark.django_db
def test_signup_ignores_unknown_plan_slug(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "email": "planless@example.com",
            "company_name": "Planless Co",
            "plan": "not-a-real-plan",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    account = Account.objects.get(company_name="Planless Co")
    subscription = Subscription.objects.get(account=account)
    assert subscription.plan_id == trial_plan.pk


@pytest.mark.django_db
def test_signup_disabled_redirects_to_landing_pricing(client, trial_plan, settings):
    from apps.core.models import SiteSettings

    site = SiteSettings.load()
    site.signups_enabled = False
    site.save(update_fields=["signups_enabled"])

    resp = client.get(SIGNUP_URL)
    assert resp.status_code == 302
    assert resp.url == "/#pricing"


@pytest.mark.django_db
def test_resend_verification_sends_new_link_for_inactive_user(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "email": "resend@example.com",
            "company_name": "Resend Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    assert len(mail.outbox) == 1

    resp = client.post(reverse("resend-verification"), {"email": "resend@example.com"})
    assert resp.status_code == 200
    assert len(mail.outbox) == 2
    assert "resend@example.com" in mail.outbox[1].to


@pytest.mark.django_db
def test_resend_verification_unknown_email_shows_same_page(client, trial_plan):
    resp = client.post(reverse("resend-verification"), {"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_resend_verification_already_active_redirects_to_login(client, trial_plan):
    client.post(
        SIGNUP_URL,
        {
            "email": "verified@example.com",
            "company_name": "Verified Co",
            "password1": "Sup3r-secret-pw",
            "password2": "Sup3r-secret-pw",
        },
    )
    user = User.objects.get(email="verified@example.com")
    user.is_active = True
    user.save(update_fields=["is_active"])

    resp = client.post(reverse("resend-verification"), {"email": "verified@example.com"})
    assert resp.status_code == 302
    assert resp.url == reverse("login")
