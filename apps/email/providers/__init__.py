import importlib

from django.conf import settings

from .base import DkimResult, MailProvider, MailProviderError, ProvisionResult

# Built-in short-name aliases so .env stays readable.
_ALIASES: dict[str, str] = {
    "stalwart": "apps.email.providers.stalwart.StalwartProvider",
    "null":     "apps.email.providers.null.NullProvider",
}


def get_mail_provider() -> MailProvider:
    """Return the configured mail infrastructure provider.

    MAIL_PROVIDER_BACKEND accepts either a short alias ("stalwart", "null") or a
    full dotted import path ("myapp.providers.postfix.PostfixProvider"), so any
    class that implements MailProvider can be plugged in without touching this
    file or any call site.
    """
    backend: str = getattr(settings, "MAIL_PROVIDER_BACKEND", "stalwart")
    dotted = _ALIASES.get(backend, backend)
    if "." not in dotted:
        raise ValueError(
            f"MAIL_PROVIDER_BACKEND {backend!r} is not a known alias and is not "
            "a dotted import path (e.g. 'myapp.mail.MyProvider')."
        )
    module_path, class_name = dotted.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
        cls: type[MailProvider] = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(
            f"Cannot load mail provider {dotted!r}: {exc}"
        ) from exc
    return cls()


__all__ = [
    "get_mail_provider",
    "MailProvider",
    "MailProviderError",
    "ProvisionResult",
    "DkimResult",
]
