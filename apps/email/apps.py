from django.apps import AppConfig


class EmailConfig(AppConfig):
    name = "apps.email"
    label = "email_service"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        import apps.email.signals  # noqa: F401
