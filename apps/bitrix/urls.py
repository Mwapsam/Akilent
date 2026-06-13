from django.urls import path

from .views import connect, callback


urlpatterns = [
    path(
        "connect/",
        connect,
        name="bitrix-connect",
    ),
    path(
        "callback/",
        callback,
        name="bitrix-callback",
    ),
]