from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from ..constants import FLOOR_CHOICES


class Patient(models.Model):
    full_name = models.CharField(max_length=255, verbose_name=_("Full name"))
    phone = PhoneNumberField(verbose_name=_("Phone"))
    national_id = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("National ID or passport"),
    )
    birth_date = models.DateField(null=True, blank=True, verbose_name=_("Date of birth"))
    floor = models.CharField(
        max_length=10,
        choices=FLOOR_CHOICES,
        default=FLOOR_CHOICES[0][0],
        verbose_name=_("Floor"),
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created"))

    class Meta:
        verbose_name = _("Patient")
        verbose_name_plural = _("Patients")
        indexes = [
            GinIndex(fields=['full_name'], name='core_patient_name_trgm', opclasses=['gin_trgm_ops']),
            GinIndex(fields=['national_id'], name='core_patient_nid_trgm', opclasses=['gin_trgm_ops']),
            GinIndex(fields=['phone'], name='core_patient_phone_trgm', opclasses=['gin_trgm_ops']),
            models.Index(fields=['phone'], name='core_patient_phone_idx'),
        ]

    def __str__(self):
        return self.full_name


class MedicalRecord(models.Model):
    NOTE = 'NOTE'
    PRESCRIPTION = 'RX'
    RECORD_TYPES = [
        (NOTE, _("Note")),
        (PRESCRIPTION, _("Prescription")),
    ]

    patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name='medical_records',
        verbose_name=_("Patient"),
    )
    record_type = models.CharField(max_length=10, choices=RECORD_TYPES, verbose_name=_("Record type"))
    content = models.TextField(verbose_name=_("Content"))
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        verbose_name=_("Doctor"),
    )
    record_datetime = models.DateTimeField(default=timezone.now, verbose_name=_("Recorded at"))

    class Meta:
        verbose_name = _("Medical record")
        verbose_name_plural = _("Medical records")

    def __str__(self):
        return f"{self.get_record_type_display()} - {self.patient.full_name}"
