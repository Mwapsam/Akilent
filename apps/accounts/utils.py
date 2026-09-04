"""Helpers for resolving the account a logged-in user is currently acting on."""

from apps.accounts.models import Account, Membership

_SESSION_KEY = "current_account_id"


def get_current_account(request):
    """Return the ``Account`` the request's user is currently scoped to.

    Resolution order:
      1. The account id pinned in the session (if the user still belongs to it).
      2. The user's owner membership, else their first membership.
    Returns ``None`` for anonymous users or users without any membership.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None

    memberships = Membership.objects.filter(user=user).select_related("account")

    pinned = request.session.get(_SESSION_KEY)
    if pinned:
        for m in memberships:
            if m.account_id == pinned:
                return m.account

    owner = memberships.filter(role=Membership.Role.OWNER).first()
    membership = owner or memberships.first()
    if membership is None:
        return None

    set_current_account(request, membership.account)
    return membership.account


def set_current_account(request, account: Account) -> None:
    request.session[_SESSION_KEY] = account.pk


def user_accounts(user):
    return Account.objects.filter(memberships__user=user).distinct()


def is_ajax(request) -> bool:
    """Was this request made via fetch/XHR with the conventional marker header?"""
    return request.headers.get("x-requested-with") == "XMLHttpRequest"
