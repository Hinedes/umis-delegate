from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Visit


class Command(BaseCommand):
    help = "Move overdue scheduled visits (PLANNED) to WAITING."

    def handle(self, *args, **options):
        overdue = Visit.objects.filter(
            status=Visit.PLANNED,
            visit_datetime__lte=timezone.now(),
        )
        count = overdue.count()
        overdue.update(status=Visit.WAITING)

        if count:
            self.stdout.write(
                self.style.SUCCESS(f"Updated {count} visit(s) from PLANNED to WAITING.")
            )
        else:
            self.stdout.write("No visits needed updating.")
