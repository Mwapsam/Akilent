from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.billing.models import Plan

PLANS = [
    {
        "slug": Plan.TRIAL,
        "name": "Trial",
        "price_monthly": Decimal("0.00"),
        "max_conversations_per_month": 100,
        "max_emails_per_month": 500,
        "max_mailboxes": 1,
        "max_automation_rules": 2,
        "max_whatsapp_numbers": 1,
        "trial_days": 14,
        "has_priority_support": False,
        "flutterwave_plan_id": None,
        "is_active": True,
    },
    {
        "slug": Plan.STARTER,
        "name": "Starter",
        "price_monthly": Decimal("19.00"),
        "max_conversations_per_month": 1000,
        "max_emails_per_month": 10000,
        "max_mailboxes": 5,
        "max_automation_rules": 10,
        "max_whatsapp_numbers": 1,
        "trial_days": 0,
        "has_priority_support": False,
        "flutterwave_plan_id": None,
        "is_active": True,
    },
    {
        "slug": Plan.PROFESSIONAL,
        "name": "Professional",
        "price_monthly": Decimal("49.00"),
        "max_conversations_per_month": 5000,
        "max_emails_per_month": 50000,
        "max_mailboxes": 25,
        "max_automation_rules": 50,
        "max_whatsapp_numbers": 3,
        "trial_days": 0,
        "has_priority_support": True,
        "flutterwave_plan_id": None,
        "is_active": True,
    },
    {
        "slug": Plan.BUSINESS,
        "name": "Business",
        "price_monthly": Decimal("99.00"),
        "max_conversations_per_month": -1,
        "max_emails_per_month": -1,
        "max_mailboxes": -1,
        "max_automation_rules": -1,
        "max_whatsapp_numbers": 10,
        "trial_days": 0,
        "has_priority_support": True,
        "flutterwave_plan_id": None,
        "is_active": True,
    },
]


class Command(BaseCommand):
    help = "Seed the four subscription plans (Trial/Starter/Professional/Business)"

    def handle(self, *args, **options):
        for data in PLANS:
            slug = data.pop("slug")
            plan, created = Plan.objects.update_or_create(slug=slug, defaults=data)
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action}: {plan.name} (${plan.price_monthly}/mo)")
        self.stdout.write(self.style.SUCCESS("Plans seeded successfully."))
