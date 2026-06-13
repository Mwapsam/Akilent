import requests


from urllib.parse import urlencode
from django.conf import settings


def get_authorization_url():
    params = {
        "client_id": settings.BITRIX_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.BITRIX24_OAUTH_REDIRECT_URL,
    }

    return (
        "https://oauth.bitrix.info/oauth/authorize/?"
        + urlencode(params)
    )

def exchange_code(code):

    url = "https://oauth.bitrix.info/oauth/token/"

    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.BITRIX_CLIENT_ID,
        "client_secret": settings.BITRIX_CLIENT_SECRET,
        "redirect_uri": settings.BITRIX24_OAUTH_REDIRECT_URL,
        "code": code,
    }

    response = requests.post(
        url,
        data=payload,
        timeout=10,
    )

    response.raise_for_status()

    return response.json()