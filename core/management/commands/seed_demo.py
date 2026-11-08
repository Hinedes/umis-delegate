"""Synthetic demo data for a demo deployment.

Idempotent: rerunning keeps existing rows and only fills what is missing.
``--reset`` clears demo visits, lines, payments, and expenses, keeps Maria
Ivanova, and recreates her canonical requested visit.
Never creates real patient data. New demo accounts share one password: the
``DEMO_PASSWORD`` setting, or a random one printed once when it is unset.
"""

import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.constants import FLOOR_CHOICES
from core.models import (
    Expense,
    MedicalRecord,
    MedicationAdministration,
    MedicationOrder,
    Operation,
    OperationConsumable,
    Patient,
    Payment,
    PaymentMethod,
    Service,
    Visit,
    VisitService,
)

User = get_user_model()

MARIA_NAME = 'Maria Ivanova'
MARIA_PHONE = '+79161234568'
MARIA_COMMENT = 'Synthetic demo request for Dr. Smirnov.'

DEMO_VISIT_TIMES = (
    time(hour=9),
    time(hour=11),
    time(hour=13),
    time(hour=16, minute=30),
    time(hour=17, minute=30),
)

DEMO_PATIENTS = [
    {'full_name': MARIA_NAME, 'phone': MARIA_PHONE, 'floor': FLOOR_CHOICES[0][0]},
    {'full_name': 'Alexey Morozov', 'phone': '+79161234501', 'floor': '2'},
    {'full_name': 'Anna Zorina', 'phone': '+79161234502', 'floor': '3'},
    {'full_name': 'Dmitry Sokolov', 'phone': '+79161234503', 'floor': '2'},
    {'full_name': 'Elena Volkova', 'phone': '+79161234504', 'floor': '3'},
    {'full_name': 'Farid Karimov', 'phone': '+79161234505', 'floor': '2'},
    {'full_name': 'Galina Lebedeva', 'phone': '+79161234506', 'floor': '3'},
    {'full_name': 'Igor Novikov', 'phone': '+79161234507', 'floor': '2'},
    {'full_name': 'Julia Orlova', 'phone': '+79161234508', 'floor': '3'},
    {'full_name': 'Kamil Akhmedov', 'phone': '+79161234509', 'floor': '2'},
    {'full_name': 'Larisa Kuznetsova', 'phone': '+79161234510', 'floor': '3'},
    {'full_name': 'Mikhail Popov', 'phone': '+79161234511', 'floor': '2'},
    {'full_name': 'Natalia Romanova', 'phone': '+79161234512', 'floor': '3'},
    {'full_name': 'Oleg Vasiliev', 'phone': '+79161234513', 'floor': '2'},
    {'full_name': 'Polina Fedorova', 'phone': '+79161234514', 'floor': '3'},
    {'full_name': 'Ruslan Isakov', 'phone': '+79161234515', 'floor': '2'},
    {'full_name': 'Svetlana Belova', 'phone': '+79161234516', 'floor': '3'},
    {'full_name': 'Timur Aliev', 'phone': '+79161234517', 'floor': '2'},
    {'full_name': 'Ulyana Krylova', 'phone': '+79161234518', 'floor': '3'},
    {'full_name': 'Viktor Zaitsev', 'phone': '+79161234519', 'floor': '2'},
]

DEMO_USERS = [
    {
        'username': 'admin',
        'first_name': 'Demo',
        'last_name': 'Admin',
        'superuser': True,
        'group': 'Admin',
    },
    {
        'username': 'dr_smirnov',
        'first_name': 'Dr.',
        'last_name': 'Smirnov',
        'superuser': False,
        'group': 'Doctor',
    },
    {
        'username': 'dr_kim',
        'first_name': 'Dr.',
        'last_name': 'Kim',
        'superuser': False,
        'group': 'Doctor',
    },
    {
        'username': 'nurse1',
        'first_name': 'Demo',
        'last_name': 'Nurse 1',
        'superuser': False,
        'group': 'Nurse',
    },
    {
        'username': 'reception1',
        'first_name': 'Demo',
        'last_name': 'Reception 1',
        'superuser': False,
        'group': 'Reception',
    },
]


def next_friday_at_ten():
    """Next Friday at 10:00 in the app timezone (strictly in the future)."""
    now = timezone.localtime()
    days_ahead = (4 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    target_date = (now + timedelta(days=days_ahead)).date()
    naive = datetime.combine(target_date, time(hour=10))
    return timezone.make_aware(naive, timezone.get_current_timezone())


class Command(BaseCommand):
    help = (
        'Create idempotent synthetic demo data (users, groups, catalog, patient, '
        'visit). With --reset, clear demo transactions and restore Maria\'s '
        'canonical requested visit.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help=(
                'Delete demo visits, lines, payments, and expenses; keep Maria '
                'Ivanova and recreate her requested Friday visit.'
            ),
        )

    def handle(self, *args, **options):
        reset = bool(options.get('reset'))
        if reset and not getattr(settings, 'DEMO_ALLOW_RESET', False):
            raise CommandError(
                'Refusing --reset: DEMO_ALLOW_RESET is not enabled. '
                'Set DEMO_ALLOW_RESET=True only on a disposable demo database.'
            )
        with transaction.atomic():
            call_command('seed_groups', stdout=self.stdout)

            users = self._seed_users()
            self._seed_catalog()
            if reset:
                self._reset_demo(
                    users['reception1'], users['dr_smirnov'], users['dr_kim'],
                )
            else:
                self._seed_patients(
                    users['reception1'], users['dr_smirnov'], users['dr_kim'],
                )

        self.stdout.write(
            self.style.SUCCESS('Demo data reset.' if reset else 'Demo data ready.')
        )

    def _seed_users(self):
        users = {}
        password = settings.DEMO_PASSWORD or secrets.token_urlsafe(12)
        created = False
        for spec in DEMO_USERS:
            user = User.objects.filter(username=spec['username']).first()
            if user is None:
                user = User.objects.create_user(
                    username=spec['username'],
                    password=password,
                    first_name=spec['first_name'],
                    last_name=spec['last_name'],
                    is_staff=spec['superuser'],
                    is_superuser=spec['superuser'],
                )
                state = f"Created user '{user.username}'"
                created = True
            else:
                state = f"User '{user.username}' exists"

            user.first_name = spec['first_name']
            user.last_name = spec['last_name']
            user.is_staff = spec['superuser']
            user.is_superuser = spec['superuser']
            user.is_active = True
            user.save()

            group = Group.objects.get(name=spec['group'])
            user.groups.set([group])

            self.stdout.write(state)
            users[spec['username']] = user

        if created and not settings.DEMO_PASSWORD:
            self.stdout.write(self.style.WARNING(f'Password for new demo accounts: {password}'))
        return users

    def _seed_catalog(self):
        for name, price in (
            ('Consultation', Decimal('1500.00')),
            ('Ultrasound', Decimal('2500.00')),
        ):
            service, created = Service.objects.get_or_create(
                name=name,
                defaults={'category': Service.SERVICE, 'price': price},
            )
            state = 'Created' if created else 'Exists'
            self.stdout.write(f"{state} service '{service.name}'")

        method, created = PaymentMethod.objects.get_or_create(
            code='card',
            defaults={
                'name': 'Card',
                'kind': PaymentMethod.CARD,
                'order': 20,
            },
        )
        if not method.active:
            method.active = True
            method.save(update_fields=['active'])
            self.stdout.write("Activated payment method 'Card'")
        else:
            state = 'Created' if created else 'Exists'
            self.stdout.write(f"{state} payment method '{method.name}'")

    def _seed_patients(self, creator, smirnov, kim):
        patients = {}
        for spec in DEMO_PATIENTS:
            patient, created = Patient.objects.get_or_create(
                phone=spec['phone'],
                defaults={
                    'full_name': spec['full_name'],
                    'floor': spec['floor'],
                    'notes': 'Synthetic demo data.',
                },
            )
            patients[spec['full_name']] = patient
            state = 'Created' if created else 'Exists'
            self.stdout.write(f"{state} patient '{patient.full_name}'")

        maria = patients[MARIA_NAME]
        self._seed_maria_visit(maria, creator, smirnov)

        for index, spec in enumerate(DEMO_PATIENTS[1:]):
            self._seed_demo_visit(
                patients[spec['full_name']], index, creator, smirnov, kim,
            )

    def _seed_maria_visit(self, maria, creator, doctor):
        visit_datetime = next_friday_at_ten()
        visit, created = Visit.objects.get_or_create(
            patient=maria,
            comment=MARIA_COMMENT,
            defaults={
                'visit_datetime': visit_datetime,
                'creator': creator,
                'assigned_doctor': doctor,
                'floor': FLOOR_CHOICES[0][0],
                'visit_type': Visit.CONSULTATION,
                'status': Visit.REQUESTED,
            },
        )
        if not created and visit.visit_datetime != visit_datetime:
            visit.visit_datetime = visit_datetime
            visit.save(update_fields=['visit_datetime', 'updated_at'])
            self.stdout.write(
                f"Updated requested visit #{visit.pk} for '{maria.full_name}' "
                f"to {visit_datetime:%Y-%m-%d %H:%M}"
            )
        elif created:
            self.stdout.write(
                f"Created requested visit #{visit.pk} for '{maria.full_name}' "
                f"on {visit_datetime:%Y-%m-%d %H:%M}"
            )
        else:
            current = timezone.localtime(visit.visit_datetime)
            self.stdout.write(
                f"Requested visit #{visit.pk} for '{maria.full_name}' "
                f"exists on {current:%Y-%m-%d %H:%M}"
            )

    def _seed_demo_visit(self, patient, index, creator, smirnov, kim):
        day = timezone.localdate() + timedelta(days=index % 7)
        visit_time = DEMO_VISIT_TIMES[index % len(DEMO_VISIT_TIMES)]
        visit_datetime = timezone.make_aware(
            datetime.combine(day, visit_time), timezone.get_current_timezone(),
        )
        doctor = smirnov if index % 2 == 0 else kim
        if index == 0:
            status = Visit.WAITING
        elif index == 7:
            status = Visit.IN_PROGRESS
        else:
            status = Visit.PLANNED
        marker = f'Synthetic demo visit {index + 1:02d}.'
        visit, created = Visit.objects.get_or_create(
            patient=patient,
            comment=marker,
            defaults={
                'visit_datetime': visit_datetime,
                'creator': creator,
                'assigned_doctor': doctor,
                'floor': patient.floor,
                'visit_type': Visit.CONSULTATION,
                'status': status,
            },
        )
        state = 'Created' if created else 'Exists'
        self.stdout.write(f"{state} visit for '{patient.full_name}'")

    def _reset_demo(self, creator, smirnov, kim):
        """Delete demo transaction data, then restore the full demo cohort.

        Deletion order follows the PROTECT edges: money and clinical children
        before visit lines, visit lines before visits, and patient references
        before patients.
        """
        maria = (
            Patient.objects.filter(full_name=MARIA_NAME).first()
            or Patient.objects.filter(phone=MARIA_PHONE).first()
        )
        maria_id = maria.pk if maria is not None else None

        Expense.objects.all().delete()
        Payment.objects.all().delete()
        MedicationAdministration.objects.all().delete()
        MedicationOrder.objects.all().delete()
        OperationConsumable.objects.all().delete()
        Operation.objects.all().delete()

        records = MedicalRecord.objects.all()
        if maria_id is not None:
            records = records.exclude(patient_id=maria_id)
        records.delete()

        VisitService.objects.all().delete()
        Visit.objects.all().delete()

        if maria_id is not None:
            Patient.objects.exclude(pk=maria_id).delete()
            maria.full_name = MARIA_NAME
            maria.phone = MARIA_PHONE
            maria.floor = FLOOR_CHOICES[0][0]
            maria.notes = 'Synthetic demo data.'
            maria.save()
        else:
            Patient.objects.all().delete()

        self._seed_patients(creator, smirnov, kim)
        self.stdout.write('Demo visits, lines, payments, and expenses reset.')
