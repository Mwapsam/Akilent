from django.urls import path

from apps.api import views

urlpatterns = [
    path("<str:version>/messages", views.MessageCreateView.as_view(), name="api-v1-messages"),
]
