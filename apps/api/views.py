from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.authentication import EmailApiKeyAuthentication
from apps.api.permissions import (
    HasBulkEmailFeature,
    HasEmailApiFeature,
    HasEmailTemplatesFeature,
    HasScope,
)
from apps.api.serializers import (
    CampaignCreateSerializer,
    MessageCreateSerializer,
    TemplateSerializer,
)
from apps.api.services import (
    create_and_queue_campaign,
    create_and_queue_message,
    create_template,
    update_template,
)
from apps.api.throttling import ApiKeyRateThrottle
from apps.email.models import BulkEmailCampaign, EmailTemplate


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
            template_id=data.get("template_id"),
            template_variables=data.get("template_variables"),
        )
        request.auth.touch()
        return Response(
            {"id": msg.id, "status": msg.status}, status=status.HTTP_202_ACCEPTED
        )


class TemplateListCreateView(APIView):
    """GET /api/v1/templates — list; POST /api/v1/templates — create."""

    authentication_classes = [EmailApiKeyAuthentication]
    permission_classes = [HasEmailApiFeature, HasEmailTemplatesFeature, HasScope]
    throttle_classes = [ApiKeyRateThrottle]
    required_scope = "templates:manage"

    def get(self, request, *args, **kwargs):
        templates = EmailTemplate.objects.filter(account=request.user, is_active=True)
        return Response(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "slug": t.slug,
                    "subject": t.subject,
                    "updated_at": t.updated_at,
                }
                for t in templates
            ]
        )

    def post(self, request, *args, **kwargs):
        serializer = TemplateSerializer(
            data=TemplateSerializer.from_request_data(request.data)
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        template = create_template(
            account=request.user,
            name=data["name"],
            slug=data.get("slug", ""),
            subject=data.get("subject", ""),
            text_body=data.get("text", ""),
            html_body=data.get("html", ""),
            sample_variables=data.get("sample_variables"),
        )
        request.auth.touch()
        return Response(
            {"id": template.id, "name": template.name, "slug": template.slug},
            status=status.HTTP_201_CREATED,
        )


class TemplateDetailView(APIView):
    """GET/PATCH/DELETE /api/v1/templates/<slug>."""

    authentication_classes = [EmailApiKeyAuthentication]
    permission_classes = [HasEmailApiFeature, HasEmailTemplatesFeature, HasScope]
    throttle_classes = [ApiKeyRateThrottle]
    required_scope = "templates:manage"

    def _get_template(self, request, slug):
        return EmailTemplate.objects.get(account=request.user, slug=slug)

    def get(self, request, slug, *args, **kwargs):
        t = self._get_template(request, slug)
        return Response(
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "subject": t.subject,
                "text": t.text_body,
                "html": t.html_body,
                "sample_variables": t.sample_variables,
            }
        )

    def patch(self, request, slug, *args, **kwargs):
        t = self._get_template(request, slug)
        data = TemplateSerializer.from_request_data(request.data)
        update_template(
            template=t,
            name=request.data.get("name"),
            subject=data.get("subject") if "subject" in request.data else None,
            text=data.get("text") if "text" in request.data else None,
            html=data.get("html") if "html" in request.data else None,
            sample_variables=request.data.get("sample_variables"),
        )
        return Response({"id": t.id, "name": t.name, "slug": t.slug})

    def delete(self, request, slug, *args, **kwargs):
        t = self._get_template(request, slug)
        t.is_active = False
        t.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class CampaignCreateView(APIView):
    """POST /api/v1/campaigns — launch a bulk send."""

    authentication_classes = [EmailApiKeyAuthentication]
    permission_classes = [HasEmailApiFeature, HasBulkEmailFeature, HasScope]
    throttle_classes = [ApiKeyRateThrottle]
    required_scope = "messages:send:bulk"

    def post(self, request, *args, **kwargs):
        serializer = CampaignCreateSerializer(
            data=CampaignCreateSerializer.from_request_data(request.data)
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        campaign = create_and_queue_campaign(
            account=request.user,
            from_email=data["from_email"],
            template_id=data.get("template_id"),
            subject=data.get("subject", ""),
            text_body=data.get("text", ""),
            html_body=data.get("html", ""),
            recipients=data["recipients"],
        )
        request.auth.touch()
        return Response(
            {
                "id": campaign.id,
                "status": campaign.status,
                "recipient_count": campaign.recipient_count,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class CampaignDetailView(APIView):
    """GET /api/v1/campaigns/<id> — status/progress poll."""

    authentication_classes = [EmailApiKeyAuthentication]
    permission_classes = [HasEmailApiFeature, HasBulkEmailFeature, HasScope]
    throttle_classes = [ApiKeyRateThrottle]
    required_scope = "messages:send:bulk"

    def get(self, request, pk, *args, **kwargs):
        campaign = BulkEmailCampaign.objects.get(pk=pk, account=request.user)
        return Response(
            {
                "id": campaign.id,
                "status": campaign.status,
                "recipient_count": campaign.recipient_count,
                "queued_count": campaign.queued_count,
                "sent_count": campaign.sent_count,
                "failed_count": campaign.failed_count,
            }
        )
