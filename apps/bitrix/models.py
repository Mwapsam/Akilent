from django.db import models


class BitrixAccount(models.Model):
    portal_url = models.URLField(unique=True)
    client_id = models.CharField(max_length=255)
    client_secret = models.TextField()
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()
    scope = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        return self.portal_url