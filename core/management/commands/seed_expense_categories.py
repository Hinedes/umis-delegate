from django.core.management.base import BaseCommand

from core.models import ExpenseCategory


class Command(BaseCommand):
    help = "Create generic expense categories. Idempotent."

    DEFAULT_CATEGORIES = [
        {'code': 'supplies', 'name': 'Supplies', 'order': 10},
        {'code': 'medicines', 'name': 'Medicines', 'order': 20},
        {'code': 'utilities', 'name': 'Utilities', 'order': 30},
        {'code': 'rent', 'name': 'Rent', 'order': 40},
        {'code': 'salaries', 'name': 'Salaries', 'order': 50},
        {'code': 'taxes', 'name': 'Taxes', 'order': 60},
        {'code': 'other', 'name': 'Other', 'order': 90},
    ]

    def handle(self, *args, **options):
        created = 0
        for spec in self.DEFAULT_CATEGORIES:
            _category, was_created = ExpenseCategory.objects.get_or_create(
                code=spec['code'],
                defaults=spec,
            )
            if was_created:
                created += 1

        self.stdout.write(self.style.SUCCESS(f"Expense categories ready ({created} created)."))
