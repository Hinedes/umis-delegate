from django.core.management.base import BaseCommand

from core.constants import FLOOR_CHOICES
from inventory.models import Location


class Command(BaseCommand):
    help = "Create one stock location per configured floor. Idempotent."

    def handle(self, *args, **options):
        created = 0
        for code, _label in FLOOR_CHOICES:
            if not Location.objects.filter(floor=code).exists():
                Location.objects.create(floor=code, name=f"Floor {code}")
                created += 1

        self.stdout.write(self.style.SUCCESS(f"Locations ready ({created} created)."))
