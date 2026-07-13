from ..models import PaymentMethod
from .flutterwave_gateway import FlutterwaveGateway
from .manual_gateway import ManualGateway

_GATEWAYS = {
    FlutterwaveGateway.code: FlutterwaveGateway(),
    ManualGateway.code: ManualGateway(),
}


def get_gateway(code: str):
    return _GATEWAYS.get(code)


def enabled_payment_methods():
    """PaymentMethod rows that are enabled and have a registered gateway."""
    return [
        m for m in PaymentMethod.objects.filter(is_enabled=True).order_by("sort_order", "name")
        if m.code in _GATEWAYS
    ]
