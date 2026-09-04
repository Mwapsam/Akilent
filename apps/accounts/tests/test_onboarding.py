import pytest
from django.contrib.auth.models import User

from apps.accounts import onboarding as ob
from apps.accounts.models import Account, Invitation, Membership
from apps.email.models import EmailApiKey, EmailDomain


def _make_account(services=Account.Services.EMAIL, *, verified_email=True):
    user = User.objects.create_user("owner", "owner@example.com", "Sup3r-secret-pw")
    acc = Account.objects.create(
        company_name="Acme",
        selected_services=services,
        onboarding_state=Account.Onboarding.ACCOUNT_CREATED,
        email_verified=verified_email,
    )
    Membership.objects.create(user=user, account=acc, role=Membership.Role.OWNER)
    return acc


@pytest.fixture
def account(db):
    return _make_account()


# --- Checklist state --------------------------------------------------------

@pytest.mark.django_db
def test_fresh_email_account_steps(account):
    state = ob.get_state(account)
    assert state["complete"] is False
    keys = [s["key"] for s in state["steps"]]
    assert keys == ["account", "verify_email", "domain", "verify", "use", "team", "security"]
    assert state["next_step"]["key"] == "domain"  # email already verified in fixture


@pytest.mark.django_db
def test_unverified_email_is_the_first_gap(db):
    acc = _make_account(verified_email=False)
    assert ob.get_state(acc)["next_step"]["key"] == "verify_email"


@pytest.mark.django_db
def test_whatsapp_only_account_skips_email_steps(db, settings):
    settings.WHATSAPP_ENABLED = True
    acc = _make_account(Account.Services.WHATSAPP)
    keys = [s["key"] for s in ob.get_state(acc)["steps"]]
    assert keys == ["account", "verify_email", "whatsapp", "team", "security"]


@pytest.mark.django_db
def test_both_account_has_whatsapp_and_email_steps(db, settings):
    settings.WHATSAPP_ENABLED = True
    acc = _make_account(Account.Services.BOTH)
    keys = [s["key"] for s in ob.get_state(acc)["steps"]]
    assert keys == ["account", "verify_email", "whatsapp", "domain", "verify", "use", "team", "security"]


@pytest.mark.django_db
def test_whatsapp_selection_falls_back_to_email_when_disabled(db, settings):
    settings.WHATSAPP_ENABLED = False
    acc = _make_account(Account.Services.WHATSAPP)
    keys = [s["key"] for s in ob.get_state(acc)["steps"]]
    assert "domain" in keys and "whatsapp" not in keys


@pytest.mark.django_db
def test_next_step_advances_as_setup_progresses(account):
    EmailDomain.objects.create(account=account, domain="mail.acme.com")
    assert ob.get_state(account)["next_step"]["key"] == "verify"

    EmailDomain.objects.filter(account=account).update(status=EmailDomain.Status.VERIFIED)
    assert ob.get_state(account)["next_step"]["key"] == "use"


@pytest.mark.django_db
def test_team_step_is_optional_and_does_not_block_completion(account):
    EmailDomain.objects.create(
        account=account, domain="mail.acme.com", status=EmailDomain.Status.VERIFIED
    )
    EmailApiKey.objects.create(account=account)
    state = ob.get_state(account)
    assert state["complete"] is True
    assert state["next_step"] is None
    team = next(s for s in state["steps"] if s["key"] == "team")
    assert team["optional"] is True and team["done"] is False
    security = next(s for s in state["steps"] if s["key"] == "security")
    assert security["optional"] is True and security["done"] is False


@pytest.mark.django_db
def test_team_step_done_with_pending_invite(account):
    Invitation.objects.create(account=account, email="x@example.com", role="member")
    team = next(s for s in ob.get_state(account)["steps"] if s["key"] == "team")
    assert team["done"] is True


# --- Resumable state machine ----------------------------------------------

@pytest.mark.django_db
def test_advance_onboarding_email_only(account):
    assert account.onboarding_state == Account.Onboarding.ACCOUNT_CREATED
    assert ob.advance_onboarding(account) == "/email/domains/"
    account.refresh_from_db()
    assert account.onboarding_state == Account.Onboarding.DOMAIN_SETUP

    EmailDomain.objects.create(account=account, domain="mail.acme.com")
    assert ob.advance_onboarding(account) == ""
    account.refresh_from_db()
    assert account.onboarding_state == Account.Onboarding.COMPLETED


@pytest.mark.django_db
def test_advance_onboarding_both_does_whatsapp_first(db, settings):
    settings.WHATSAPP_ENABLED = True
    acc = _make_account(Account.Services.BOTH)
    assert ob.advance_onboarding(acc) == "/whatsapp/numbers/"
    acc.refresh_from_db()
    assert acc.onboarding_state == Account.Onboarding.WHATSAPP_SETUP


# --- Context processor + widget rendering --------------------------------

@pytest.mark.django_db
def test_widget_and_tour_render_when_incomplete(client, account):
    client.force_login(account.owner)
    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    assert b"Finish setup" in resp.content
    assert b"open-welcome-tour" in resp.content


@pytest.mark.django_db
def test_widget_hidden_once_setup_complete(client, account):
    EmailDomain.objects.create(
        account=account, domain="mail.acme.com", status=EmailDomain.Status.VERIFIED
    )
    EmailApiKey.objects.create(account=account)
    client.force_login(account.owner)
    resp = client.get("/dashboard/")
    assert b"Finish setup" not in resp.content
    assert b"open-welcome-tour" not in resp.content


@pytest.mark.django_db
def test_widget_not_shown_on_onboarding_page(client, account):
    client.force_login(account.owner)
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    assert b"Finish setup" not in resp.content
