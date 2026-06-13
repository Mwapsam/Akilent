from apps.bitrix.client import BitrixClient


def get_deal(client: BitrixClient, deal_id: str) -> dict:
    return client.call("crm.deal.get", {"id": deal_id})


def create_deal(client: BitrixClient, fields: dict) -> str:
    """Create a CRM deal and return its ID."""
    result = client.call("crm.deal.add", {"fields": fields})
    return str(result)


def update_deal(client: BitrixClient, deal_id: str, fields: dict) -> None:
    client.call("crm.deal.update", {"id": deal_id, "fields": fields})


def move_deal_stage(client: BitrixClient, deal_id: str, stage_id: str) -> None:
    update_deal(client, deal_id, {"STAGE_ID": stage_id})


def list_deals_for_contact(client: BitrixClient, contact_id: str) -> list:
    return client.call("crm.deal.list", {
        "filter": {"CONTACT_ID": contact_id},
        "select": ["ID", "TITLE", "STAGE_ID", "CURRENCY_ID", "OPPORTUNITY"],
    })
