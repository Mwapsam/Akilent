import pytest
from django.contrib.auth.models import User

from apps.accounts import onboarding as ob
from apps.accounts.models import Account, Invitation, Membership
from apps.email.models import EmailApiKey, EmailDomain


@pytest.fixture
def account(db):
    user = User.objects.create_user("owner", "owner@example.com", "Sup3r-secret-pw")
    acc = Account.objects.create(company_name="Acme")
    Membership.objects.create(user=user, account=acc, role=Membership.Role.OWNER)
    return acc


# --- State --------------------------------------------------------------------

@pytest.mark.django_db
def test_fresh_account_only_has_account_step_done(account):
    state = ob.get_state(account)
    assert state["complete"] is False
    assert state["required_done"] == 1  # just "Create your account"
    # The next required step is adding a domain.
    assert state["next_step"]["key"] == "domain"
    keys = [s["key"] for s in state["steps"]]
    assert keys == ["account", "domain", "verify", "use", "team"]


@pytest.mark.django_db
def test_next_step_advances_as_setup_progresses(account):
    EmailDomain.objects.create(account=account, domain="mail.acme.com")
    assert ob.get_state(account)["next_step"]["key"] == "verify"

    EmailDomain.objects.filter(account=account).update(status=EmailDomain.Status.VERIFIED)
    assert ob.get_state(account)["next_step"]["key"] == "use"


@pytest.mark.django_db
def test_team_step_is_optional_and_does_not_block_completion(account):
    # Complete every required step; leave the optional "team" step undone.
    domain = EmailDomain.objects.create(
        account=account, domain="mail.acme.com", status=EmailDomain.Status.VERIFIED
    )
    EmailApiKey.objects.create(account=account)
    state = ob.get_state(account)
    assert state["complete"] is True
    assert state["next_step"] is None
    team = next(s for s in state["steps"] if s["key"] == "team")
    assert team["optional"] is True and team["done"] is False


@pytest.mark.django_db
def test_team_step_done_with_pending_invite(account):
    Invitation.objects.create(account=account, email="x@example.com", role="member")
    team = next(s for s in ob.get_state(account)["steps"] if s["key"] == "team")
    assert team["done"] is True


# --- Context processor + widget rendering ------------------------------------

@pytest.mark.django_db
def test_widget_and_tour_render_when_incomplete(client, account):
    client.force_login(account.owner)
    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    assert b"Finish setup" in resp.content          # floating launcher
    assert b"open-welcome-tour" in resp.content     # tour wired up


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
    # Full checklist page, not the floating launcher.
    assert b"Finish setup" not in resp.content
