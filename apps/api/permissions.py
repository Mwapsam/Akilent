from rest_framework.permissions import BasePermission

from apps.billing.limits import LimitChecker


class HasEmailApiFeature(BasePermission):
    """Gate on the account's plan including the email API & SMTP relay feature.

    request.user is the authenticated Account (see EmailApiKeyAuthentication);
    this is a plan-tier gate, distinct from ApiKeyRateThrottle's per-minute
    request-rate limiting and LimitChecker.check_email()'s monthly volume cap.
    """

    message = "Your plan does not include the email API & SMTP relay. Upgrade to send."

    def has_permission(self, request, view) -> bool:
        account = request.user
        if account is None:
            return False
        return LimitChecker(account).has_feature("email_apis")
