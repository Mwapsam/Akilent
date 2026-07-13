from abc import ABC, abstractmethod


class PaymentGateway(ABC):
    """A pluggable way to pay for a plan.

    Gateways only *initiate* checkout — activation always goes through
    ``apps.billing.services.activate_subscription`` so every payment method
    grants a subscription the same way.
    """

    code: str
    label: str

    @abstractmethod
    def start_checkout(self, request, account, plan):
        """Return the HttpResponse that begins payment for ``plan``.

        For a hosted gateway this is a redirect to the provider's checkout
        page. For an offline gateway this is a rendered instructions page.
        """
        raise NotImplementedError
