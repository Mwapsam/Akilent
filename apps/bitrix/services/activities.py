from apps.bitrix.client import BitrixClient


def log_message_activity(
    client: BitrixClient,
    entity_type: str,
    entity_id: str,
    subject: str,
    description: str,
    direction: int = 2,  # 1=inbound, 2=outbound
) -> str:
    """Log a WhatsApp message as a Bitrix24 CRM activity and return its ID."""
    entity_type_id = {"lead": 1, "contact": 3, "deal": 2}.get(entity_type, 3)
    result = client.call("crm.activity.add", {
        "fields": {
            "OWNER_TYPE_ID": entity_type_id,
            "OWNER_ID": entity_id,
            "TYPE_ID": 4,  # 4 = call/message
            "SUBJECT": subject,
            "DESCRIPTION": description,
            "DIRECTION": direction,
            "COMPLETED": "Y",
            "COMMUNICATIONS": [],
        }
    })
    return str(result)


def get_activity(client: BitrixClient, activity_id: str) -> dict:
    return client.call("crm.activity.get", {"id": activity_id})


def list_activities_for_entity(
    client: BitrixClient, entity_type: str, entity_id: str
) -> list:
    entity_type_id = {"lead": 1, "contact": 3, "deal": 2}.get(entity_type, 3)
    return client.call("crm.activity.list", {
        "filter": {"OWNER_TYPE_ID": entity_type_id, "OWNER_ID": entity_id},
        "select": ["ID", "SUBJECT", "DESCRIPTION", "CREATED", "DIRECTION"],
    })
