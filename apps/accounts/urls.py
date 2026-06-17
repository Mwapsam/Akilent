from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("signup/", views.signup, name="signup"),
    path("verify/<uidb64>/<token>/", views.verify_email, name="verify_email"),
    path("onboarding/", views.onboarding, name="onboarding"),
    path("dashboard/", views.dashboard, name="dashboard"),
]
