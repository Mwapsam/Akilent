"""Sandboxed rendering of EmailTemplate content.

Uses a plain django.template.Engine instance (not the app-configured one) so
tenant-authored template content only ever sees the variables dict passed at
render time — never settings, the request, or app models. Never render with
a RequestContext here.
"""
from __future__ import annotations

from django.template import Context, Engine

_ENGINE = Engine(
    debug=False,
    libraries={},
    builtins=["django.template.defaulttags", "django.template.defaultfilters"],
)


def render_string(source: str, variables: dict | None = None) -> str:
    """Render a single Django-template-syntax string with `variables`."""
    if not source:
        return source
    return _ENGINE.from_string(source).render(Context(variables or {}, autoescape=True))


def render_template(template, variables: dict | None = None) -> tuple[str, str, str]:
    """Render an EmailTemplate's subject/text/html with `variables`.

    Returns (subject, text_body, html_body).
    """
    variables = variables or {}
    subject = render_string(template.subject, variables)
    text_body = render_string(template.text_body, variables)
    html_body = render_string(template.html_body, variables)
    return subject, text_body, html_body
