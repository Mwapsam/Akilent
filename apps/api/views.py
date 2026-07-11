from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.authentication import EmailApiKeyAuthentication
from apps.api.permissions import HasEmailApiFeature
from apps.api.serializers import MessageCreateSerializer
from apps.api.services import create_and_queue_message
from apps.api.throttling import ApiKeyRateThrottle


class MessageCreateView(APIView):
    """POST /api/v1/messages — send a transactional email.

    This is the endpoint marketed on the landing page's Developer Platform
    section. request.user is the authenticated Account and request.auth is
    the EmailApiKey (see EmailApiKeyAuthentication).
    """

    authentication_classes = [EmailApiKeyAuthentication]
    permission_classes = [HasEmailApiFeature]
    throttle_classes = [ApiKeyRateThrottle]

    def post(self, request, *args, **kwargs):
        serializer = MessageCreateSerializer(
            data=MessageCreateSerializer.from_request_data(request.data)
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        msg = create_and_queue_message(
            account=request.user,
            from_email=data["from_email"],
            to_email=data["to_email"],
            subject=data.get("subject", ""),
            text_body=data.get("text", ""),
            html_body=data.get("html", ""),
        )
        request.auth.touch()
        return Response(
            {"id": msg.id, "status": msg.status}, status=status.HTTP_202_ACCEPTED
        )
