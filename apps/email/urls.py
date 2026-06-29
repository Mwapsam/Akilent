from django.urls import path

from apps.email import views

urlpatterns = [
    path("domains/", views.domains_list, name="email-domains"),
    path("domains/create/", views.domain_create, name="email-domain-create"),
    path("domains/<int:pk>/verify/", views.domain_verify, name="email-domain-verify"),
    path("domains/<int:pk>/toggle/", views.domain_toggle, name="email-domain-toggle"),
    path("domains/<int:pk>/delete/", views.domain_delete, name="email-domain-delete"),
    path("keys/create/", views.key_create, name="email-key-create"),
    path("insights/", views.insights, name="email-insights"),
    path("mailboxes/", views.mailbox_list, name="email-mailboxes"),
    path("mailboxes/create/", views.mailbox_create, name="email-mailbox-create"),
    path("mailboxes/<int:pk>/delete/", views.mailbox_delete, name="email-mailbox-delete"),
    path("mailboxes/<int:pk>/password/", views.mailbox_password, name="email-mailbox-password"),
    path("mailboxes/<int:pk>/quota/", views.mailbox_quota, name="email-mailbox-quota"),
    path("aliases/create/", views.alias_create, name="email-alias-create"),
    path("send/", views.api_send, name="email-send"),
    path("t/open/<str:token>/", views.tracking_open, name="email-tracking-open"),
    path("t/click/<str:token>/", views.tracking_click, name="email-tracking-click"),
]
