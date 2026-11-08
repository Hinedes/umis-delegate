from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from core.role_defaults import GROUPS_PERMISSIONS


class Command(BaseCommand):
    help = "Seed user groups and their permissions. Use --reset to overwrite existing groups."

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Clear each seeded group before assigning permissions',
        )

    def handle(self, *args, **options):
        reset = options['reset']
        missing = []

        for group_name, perm_specs in GROUPS_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(name=group_name)
            if created:
                self.stdout.write(f"Created group '{group_name}'")
                missing += self._assign(group, perm_specs)
            elif reset:
                self.stdout.write(f"Reset group '{group_name}'")
                group.permissions.clear()
                missing += self._assign(group, perm_specs)
            else:
                self.stdout.write(
                    f"Group '{group_name}' exists; skipped (use --reset to overwrite)"
                )

        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(missing)} permission(s) not found; they belong to systems that do not exist yet"
                )
            )

    def _assign(self, group, perm_specs):
        missing = []
        for spec in perm_specs:
            app_label, codename = spec.split('.')
            try:
                permission = Permission.objects.get(
                    content_type__app_label=app_label,
                    codename=codename,
                )
            except Permission.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"Permission {spec} not found"))
                missing.append(spec)
                continue
            group.permissions.add(permission)
        return missing
