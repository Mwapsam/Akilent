from rest_framework import serializers


class MessageCreateSerializer(serializers.Serializer):
    """Validates the public POST /api/v1/messages payload.

    Field-level validation only (presence, email format) — the verified-
    sending-domain check and plan/quota gates live in apps.api.services,
    shared with the legacy /email/send/ shim, so both keep identical 403
    semantics rather than the generic 400 a serializer ValidationError
    would produce.
    """

    from_email = serializers.EmailField()
    to_email = serializers.EmailField()
    subject = serializers.CharField(required=False, allow_blank=True, default="")
    text = serializers.CharField(required=False, allow_blank=True, default="")
    html = serializers.CharField(required=False, allow_blank=True, default="")

    @staticmethod
    def from_request_data(data: dict) -> dict:
        """Map the public JSON shape (`from`/`to`) onto this serializer's fields."""
        return {
            "from_email": data.get("from"),
            "to_email": data.get("to"),
            "subject": data.get("subject", ""),
            "text": data.get("text", ""),
            "html": data.get("html", ""),
        }
