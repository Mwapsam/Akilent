
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.conf import settings

from .models import BitrixAccount
from .auth import exchange_code

from django.shortcuts import redirect

from .auth import get_authorization_url


def connect(request):
    return redirect(get_authorization_url())


def callback(request):

    code = request.GET.get("code")

    if not code:
        return JsonResponse(
            {"error": "Missing authorization code"},
            status=400,
        )


    data = exchange_code(code)

    expires_at = timezone.now() + timedelta(seconds=data["expires_in"])

    # Bitrix returns the portal domain directly in the token response
    domain = data.get("domain") or data.get("client_endpoint", "").split("/")[2]

    BitrixAccount.objects.update_or_create(
        domain=domain,
        defaults={
            "company_name": domain,
            "client_id": settings.BITRIX_CLIENT_ID,
            "client_secret": settings.BITRIX_CLIENT_SECRET,
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": expires_at,
        },
    )

    return JsonResponse({"success": True})