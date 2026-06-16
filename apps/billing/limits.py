import logging

logger = logging.getLogger(__name__)


class PlanLimitExceeded(Exception):
    def __init__(self, message: str, limit_type: str):
        self.limit_type = limit_type
        super().__init__(message)


class LimitChecker:
    def __init__(self, account):
        self.account = account
        try:
            self.subscription = account.subscription
        except Exception:
            self.subscription = None

    def _require_active_plan(self):
        if not self.subscription or not self.subscription.is_active:
            raise PlanLimitExceeded(
                "No active subscription. Please subscribe to a plan.",
                "subscription",
            )
        return self.subscription.plan

    def check_conversation(self):
        plan = self._require_active_plan()
        if plan.max_conversations_per_month == -1:
            return
        from apps.billing.models import UsageSummary
        used = UsageSummary.get_current_usage(self.account)
        if used >= plan.max_conversations_per_month:
            raise PlanLimitExceeded(
                f"Monthly conversation limit of {plan.max_conversations_per_month} reached. "
                "Please upgrade your plan.",
                "conversations",
            )

    def check_whatsapp_number(self):
        plan = self._require_active_plan()
        if plan.max_whatsapp_numbers == -1:
            return
        from apps.whatsapp.models.tenant import WhatsAppBusinessNumber
        count = WhatsAppBusinessNumber.objects.filter(account=self.account).count()
        if count >= plan.max_whatsapp_numbers:
            raise PlanLimitExceeded(
                f"WhatsApp number limit of {plan.max_whatsapp_numbers} reached. "
                "Upgrade to a higher plan to add more numbers.",
                "whatsapp_numbers",
            )

    def check_automation_rule(self):
        plan = self._require_active_plan()
        if plan.max_automation_rules == -1:
            return
        from apps.whatsapp.models import AutomationRule
        count = AutomationRule.objects.filter(
            account=self.account, is_active=True
        ).count()
        if count >= plan.max_automation_rules:
            raise PlanLimitExceeded(
                f"Automation rule limit of {plan.max_automation_rules} reached. "
                "Upgrade your plan to add more rules.",
                "automation_rules",
            )

    def check_mailbox(self):
        plan = self._require_active_plan()
        if plan.max_mailboxes == -1:
            return
        from apps.email.models import Mailbox
        count = Mailbox.objects.filter(account=self.account).count()
        if count >= plan.max_mailboxes:
            raise PlanLimitExceeded(
                f"Mailbox limit of {plan.max_mailboxes} reached. "
                "Upgrade your plan to add more mailboxes.",
                "mailboxes",
            )

    def check_email(self):
        plan = self._require_active_plan()
        if plan.max_emails_per_month == -1:
            return
        from apps.billing.models import UsageSummary
        used = UsageSummary.get_current_email_usage(self.account)
        if used >= plan.max_emails_per_month:
            raise PlanLimitExceeded(
                f"Monthly email limit of {plan.max_emails_per_month} reached. "
                "Please upgrade your plan.",
                "emails",
            )
