from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.constants import FLOOR_CHOICES


class Consumable(models.Model):
    """Stock authority for a physical item.

    The optional link to a Service is the billing side; stock does not depend
    on it. Retire items by deactivating them. Deletion is blocked once any
    movement exists, because Consumption is history.
    """

    name = models.CharField(max_length=255, verbose_name=_("Name"))
    sku = models.CharField(max_length=50, unique=True, blank=True, verbose_name=_("SKU"))
    unit = models.CharField(max_length=20, default='pc', verbose_name=_("Unit"))
    category = models.CharField(max_length=50, blank=True, verbose_name=_("Category"))
    service = models.OneToOneField(
        'core.Service',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='consumable',
        verbose_name=_("Billed as"),
        help_text=_("Optional medicine service that bills this item."),
    )
    active = models.BooleanField(default=True, verbose_name=_("Active"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated"))

    class Meta:
        verbose_name = _("Consumable")
        verbose_name_plural = _("Consumables")
        ordering = ['name']
        permissions = [
            ("manage_inventory", _("Can manage inventory")),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.sku:
            super().save(*args, **kwargs)
            self.sku = f"SKU-{self.pk:05d}"
            super().save(update_fields=['sku'])
            return
        super().save(*args, **kwargs)


class Location(models.Model):
    """A physical stock location. One per configured floor by default."""

    name = models.CharField(max_length=100, verbose_name=_("Name"))
    floor = models.CharField(max_length=10, choices=FLOOR_CHOICES, verbose_name=_("Floor"))

    class Meta:
        verbose_name = _("Location")
        verbose_name_plural = _("Locations")
        ordering = ['floor', 'name']

    def __str__(self):
        return f"{self.name}"


class Stock(models.Model):
    """Current quantity of a consumable at a location.

    Updated only through StockService movements, never by an absolute form
    value.
    """

    consumable = models.ForeignKey(
        Consumable,
        on_delete=models.CASCADE,
        related_name='stock_levels',
        verbose_name=_("Consumable"),
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='stock_items',
        verbose_name=_("Location"),
    )
    quantity = models.IntegerField(default=0, verbose_name=_("Quantity"))

    class Meta:
        verbose_name = _("Stock")
        verbose_name_plural = _("Stock")
        unique_together = ['consumable', 'location']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0),
                name='stock_quantity_non_negative',
            ),
        ]

    def __str__(self):
        return f"{self.consumable.name} @ {self.location.name}: {self.quantity}"


class ConsumptionQuerySet(models.QuerySet):
    """Append-only access: movements can be inserted and read, never changed."""

    def delete(self):
        raise ValidationError(_("Stock movements are append-only and cannot be deleted."))

    def update(self, **kwargs):
        raise ValidationError(_("Stock movements are append-only and cannot be edited."))

    def net(self, consumable, location=None):
        """Signed movement total. Stock is the cached value of this sum.

        Usage reporting aggregates this ledger, never OperationConsumable rows.
        """
        rows = self.filter(consumable=consumable)
        if location is not None:
            rows = rows.filter(location=location)
        return rows.aggregate(total=models.Sum('quantity'))['total'] or 0


class Consumption(models.Model):
    """Append-only stock movement log. Records are never updated or deleted.

    A visit line can produce at most one VISIT movement and one CANCEL
    movement; corrections use a new ADJUSTMENT movement.
    """

    VISIT = 'VISIT'
    CANCEL = 'CANCEL'
    ADJUSTMENT = 'ADJUSTMENT'
    RESTOCK = 'RESTOCK'
    SURGERY = 'SURGERY'
    MEDICATION = 'MEDICATION'
    MEDICATION_VOID = 'MEDICATION_VOID'
    REASON_CHOICES = [
        (VISIT, _("Visit")),
        (CANCEL, _("Visit line cancelled")),
        (ADJUSTMENT, _("Adjustment")),
        (RESTOCK, _("Restock")),
        (SURGERY, _("Operation")),
        (MEDICATION, _("Medication administered")),
        (MEDICATION_VOID, _("Medication administration voided")),
    ]

    consumable = models.ForeignKey(
        Consumable,
        on_delete=models.PROTECT,
        related_name='consumption_records',
        verbose_name=_("Consumable"),
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='consumption_records',
        verbose_name=_("Location"),
    )
    quantity = models.IntegerField(verbose_name=_("Quantity"))
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, verbose_name=_("Reason"))
    visit_service = models.ForeignKey(
        'core.VisitService',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consumption_records',
        verbose_name=_("Visit service"),
    )
    operation_consumable = models.ForeignKey(
        'core.OperationConsumable',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consumption_records',
        verbose_name=_("Operation consumable"),
    )
    medication_administration = models.ForeignKey(
        'core.MedicationAdministration',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consumption_records',
        verbose_name=_("Medication administration"),
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Created by"),
    )

    class Meta:
        verbose_name = _("Stock movement")
        verbose_name_plural = _("Stock movements")
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['visit_service', 'reason'],
                condition=models.Q(visit_service__isnull=False, reason__in=['VISIT', 'CANCEL']),
                name='uniq_consumption_visit_movement',
            ),
            models.UniqueConstraint(
                fields=['operation_consumable', 'reason'],
                condition=models.Q(operation_consumable__isnull=False, reason='SURGERY'),
                name='uniq_consumption_surgery_movement',
            ),
            models.UniqueConstraint(
                fields=['medication_administration', 'reason'],
                condition=models.Q(
                    medication_administration__isnull=False,
                    reason__in=['MEDICATION', 'MEDICATION_VOID'],
                ),
                name='uniq_consumption_medication_movement',
            ),
        ]

    objects = ConsumptionQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ValidationError(_("Stock movements are append-only and cannot be edited."))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Stock movements are append-only and cannot be deleted."))

    def __str__(self):
        return f"{self.consumable.name}: {self.quantity:+d} ({self.get_reason_display()})"
