from django.core.management.base import BaseCommand

from core.models import PaymentMethod


class Command(BaseCommand):
    help = "Create generic cash, card, and transfer payment methods. Idempotent."

    DEFAULT_METHODS = [
        {'code': 'cash', 'name': 'Cash', 'kind': PaymentMethod.CASH, 'order': 10},
        {'code': 'card', 'name': 'Card', 'kind': PaymentMethod.CARD, 'order': 20},
        {'code': 'transfer', 'name': 'Bank transfer', 'kind': PaymentMethod.TRANSFER, 'order': 30},
    ]

    def handle(self, *args, **options):
        created = 0
        for spec in self.DEFAULT_METHODS:
            _method, was_created = PaymentMethod.objects.get_or_create(
                code=spec['code'],
                defaults=spec,
            )
            if was_created:
                created += 1

        self.stdout.write(self.style.SUCCESS(f"Payment methods ready ({created} created)."))
