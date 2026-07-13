from django.db import models


class SiteSettings(models.Model):
    """Platform-wide configuration, edited by admins in the Settings page.

    A singleton (always pk=1) loaded via ``SiteSettings.load()``.
    """

    # Branding
    app_name = models.CharField(max_length=100, default="Automator")
    logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    support_email = models.EmailField(blank=True, default="")

    # Feature flags (UI layer). WhatsApp/Bitrix also require the matching env
    # flag at boot to wire URLs/Celery — these can only further *disable* them.
    whatsapp_enabled = models.BooleanField(default=True)
    bitrix_enabled = models.BooleanField(default=True)
    signups_enabled = models.BooleanField(default=True)
    payments_enabled = models.BooleanField(default=True)

    # New-signup defaults
    default_plan = models.ForeignKey(
        "billing.Plan", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    default_trial_days = models.PositiveIntegerField(default=14)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return self.app_name

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
