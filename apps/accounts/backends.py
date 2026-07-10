from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User


class EmailBackend(ModelBackend):
    """Authenticate by email address instead of username.

    Django's ``AuthenticationForm`` always submits the credential under the
    ``username`` kwarg, so we accept it under that name and just treat the
    value as an email. ``ModelBackend`` stays registered alongside this one
    (see AUTHENTICATION_BACKENDS) so admin/username-based auth keeps working.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        try:
            user = User._default_manager.get(email__iexact=username)
        except User.DoesNotExist:
            # Run the hasher anyway to keep response time constant whether or
            # not the email matches an account (mirrors ModelBackend).
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
