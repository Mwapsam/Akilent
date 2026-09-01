from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm
from django.contrib.auth.models import User

from apps.accounts.models import Invitation


class SignupForm(UserCreationForm):
    """Sign-up form: an email, a password, and the company (tenant) name.

    Email is the login credential (see apps.accounts.backends.EmailBackend),
    so there's no separate username field — ``User.username`` is set from the
    email in the signup view.
    """

    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    company_name = forms.CharField(max_length=255, required=True, label="Company name")
    # Carries the plan chosen on the pricing cards through validation errors;
    # never shown to the user, just round-tripped.
    plan = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = User
        fields = ("email", "company_name", "plan", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class LoginForm(AuthenticationForm):
    """Log in with email + password instead of username."""

    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )

    error_messages = {
        "invalid_login": (
            "Please enter a correct email and password. Note that both fields "
            "may be case-sensitive."
        ),
        "inactive": (
            "This account hasn't been verified yet. Check your email for a "
            "confirmation link, or resend it below."
        ),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # AuthenticationForm.__init__ clamps this to User.username's
        # max_length (150); restore the normal email length limit.
        self.fields["username"].max_length = 254
        self.fields["username"].widget.attrs["maxlength"] = 254


class ProfileForm(forms.ModelForm):
    """Lets a signed-in user edit their own name and email."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
        labels = {"first_name": "First name", "last_name": "Last name", "email": "Email"}

    def clean_email(self):
        email = self.cleaned_data["email"]
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email


class InviteForm(forms.Form):
    """Invite a teammate to the current workspace by email + role."""

    email = forms.EmailField()
    role = forms.ChoiceField(choices=Invitation.INVITE_ROLES)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class AcceptInvitationForm(UserCreationForm):
    """Create a User when accepting an invitation (email comes from the invite)."""

    class Meta:
        model = User
        fields = ("username", "password1", "password2")


class PasswordResetForm(PasswordResetForm):
    """Password reset form that sends via the configured email provider (SES/SMTP).

    Overrides Django's default send_mail to use send_system_email instead,
    which respects suppression lists and the active send provider.
    """

    def send_mail(
        self, subject_template_name, email_template_name, context, from_email,
        to_email, html_email_template_name=None,
    ):
        from django.template.loader import render_to_string
        from apps.email.services.send import send_system_email

        subject = render_to_string(subject_template_name, context).strip()
        message = render_to_string(email_template_name, context)
        html_message = None
        if html_email_template_name is not None:
            html_message = render_to_string(html_email_template_name, context)

        try:
            send_system_email(
                to_email=to_email,
                subject=subject,
                text_body=message,
                html_body=html_message or "",
            )
        except Exception as exc:
            raise forms.ValidationError(f"Failed to send password reset email: {exc}")
