from django.urls import path

from apps.api import views

urlpatterns = [
    path("<str:version>/messages", views.MessageCreateView.as_view(), name="api-v1-messages"),
    path("<str:version>/templates", views.TemplateListCreateView.as_view(), name="api-v1-templates"),
    path("<str:version>/templates/<slug:slug>", views.TemplateDetailView.as_view(), name="api-v1-template-detail"),
    path("<str:version>/campaigns", views.CampaignCreateView.as_view(), name="api-v1-campaigns"),
    path("<str:version>/campaigns/<int:pk>", views.CampaignDetailView.as_view(), name="api-v1-campaign-detail"),
]
