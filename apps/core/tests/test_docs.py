import pytest
from django.utils.html import escape

from apps.core import docs as docs_kb


@pytest.mark.django_db
def test_docs_index_renders_for_anonymous(client):
    resp = client.get("/docs/")
    assert resp.status_code == 200
    assert b"Developer Platform" in resp.content


@pytest.mark.django_db
@pytest.mark.parametrize("page", docs_kb.PAGES, ids=lambda p: p.slug)
def test_every_docs_page_renders(client, page):
    url = "/docs/" if page.slug == "index" else f"/docs/{page.slug}/"
    resp = client.get(url)
    assert resp.status_code == 200
    assert escape(page.title).encode() in resp.content


@pytest.mark.django_db
def test_unknown_docs_page_is_404(client):
    assert client.get("/docs/does-not-exist/").status_code == 404


@pytest.mark.django_db
def test_docs_nav_lists_every_page(client):
    resp = client.get("/docs/")
    for page in docs_kb.PAGES:
        assert escape(page.title).encode() in resp.content


@pytest.mark.django_db
def test_smtp_page_shows_relay_host_and_port(client, settings):
    settings.SMTP_RELAY_HOST = "mail.example.com"
    settings.SMTP_RELAY_PORT = 587
    resp = client.get("/docs/smtp/")
    assert resp.status_code == 200
    assert b"mail.example.com" in resp.content
    assert b"587" in resp.content
