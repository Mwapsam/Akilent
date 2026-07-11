"""Standardized {"error": {"code", "message"}} envelope for apps.api.

Wraps DRF's default exception handler so every error response — validation,
auth, throttling, permission, and the billing PlanLimitExceeded exception
that isn't a DRF exception at all — comes back in one predictable shape.
"""
from __future__ import annotations

from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.api.services import UnverifiedDomainError
from apps.billing.limits import PlanLimitExceeded


def _flatten_detail(data) -> str:
    if isinstance(data, dict):
        if set(data.keys()) == {"detail"}:
            return str(data["detail"])
        parts = []
        for field, errors in data.items():
            if isinstance(errors, (list, tuple)):
                parts.append(f"{field}: {'; '.join(str(e) for e in errors)}")
            else:
                parts.append(f"{field}: {errors}")
        return " | ".join(parts)
    if isinstance(data, (list, tuple)):
        return "; ".join(str(e) for e in data)
    return str(data)


def custom_exception_handler(exc, context):
    if isinstance(exc, PlanLimitExceeded):
        return Response(
            {"error": {"code": exc.limit_type, "message": str(exc)}},
            status=status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, UnverifiedDomainError):
        return Response(
            {"error": {"code": "unverified_domain", "message": str(exc)}},
            status=status.HTTP_403_FORBIDDEN,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = getattr(exc, "default_code", "error")
    message = _flatten_detail(response.data)
    response.data = {"error": {"code": code, "message": message}}

    if isinstance(exc, drf_exceptions.Throttled):
        response["Retry-After"] = str(int(exc.wait)) if exc.wait else "60"

    return response
