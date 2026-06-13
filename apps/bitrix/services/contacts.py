from apps.bitrix.client import BitrixClient


def get_contact(client: BitrixClient, contact_id: str) -> dict:
    return client.call("crm.contact.get", {"id": contact_id})


def find_contact_by_phone(client: BitrixClient, phone: str) -> dict | None:
    results = client.call("crm.contact.list", {
        "filter": {"PHONE": phone},
        "select": ["ID", "NAME", "LAST_NAME", "PHONE", "EMAIL"],
    })
    return results[0] if results else None


def create_contact(client: BitrixClient, fields: dict) -> str:
    """Create a CRM contact and return its ID."""
    result = client.call("crm.contact.add", {"fields": fields})
    return str(result)


def update_contact(client: BitrixClient, contact_id: str, fields: dict) -> None:
    client.call("crm.contact.update", {"id": contact_id, "fields": fields})
