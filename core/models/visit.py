from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..constants import FLOOR_CHOICES
from ..managers import VisitManager
from .patient import Patient
from .service import Service


class Visit(models.Model):
    """A scheduled or completed patient visit.

    Line items, payments, and financial totals belong to later systems and are
    deliberately absent here.
    """

    REQUESTED = 'REQUESTED'
    PLANNED = 'PLANNED'
    WAITING = 'WAITING'
    IN_PROGRESS = 'IN_PROGRESS'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    NO_SHOW = 'NO_SHOW'
    STATUS_CHOICES = [
        (REQUESTED, _("Requested")),
        (PLANNED, _("Scheduled")),
        (WAITING, _("Waiting")),
        (IN_PROGRESS, _("In progress")),
        (COMPLETED, _("Completed")),
        (CANCELLED, _("Cancelled")),
        (NO_SHOW, _("No show")),
    ]
    TERMINAL_STATUSES = (COMPLETED, CANCELLED)

    CONSULTATION = 'CONSULTATION'
    REPEAT = 'REPEAT'
    OPERATION = 'OPERATION'
    VISIT_TYPES = [
        (CONSULTATION, _("First consultation")),
        (REPEAT, _("Repeat consultation")),
        (OPERATION, _("Operation")),
    ]

    REFERRAL_CHOICES = [
        ('DOCTOR_NOTE', _("Doctor referral")),
        ('ADVERTISEMENT', _("Advertisement")),
        ('WORD_OF_MOUTH', _("Word of mouth")),
    ]

    patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name='visits',
        verbose_name=_("Patient"),
    )
    visit_datetime = models.DateTimeField(default=timezone.now, verbose_name=_("Visit time"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated"))
    floor = models.CharField(
        max_length=10,
        choices=FLOOR_CHOICES,
        default=FLOOR_CHOICES[0][0],
        verbose_name=_("Floor"),
    )
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_visits',
        verbose_name=_("Created by"),
    )
    assigned_doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_visits',
        verbose_name=_("Doctor"),
    )
    referral_source = models.CharField(
        max_length=50,
        choices=REFERRAL_CHOICES,
        blank=True,
        verbose_name=_("Referral source"),
    )
    comment = models.TextField(blank=True, verbose_name=_("Comment"))
    visit_type = models.CharField(
        max_length=20,
        choices=VISIT_TYPES,
        default=CONSULTATION,
        verbose_name=_("Visit type"),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PLANNED,
        verbose_name=_("Status"),
    )

    class Meta:
        verbose_name = _("Visit")
        verbose_name_plural = _("Visits")
        permissions = [
            ("start_visit", _("Can check in a visit")),
            ("practice_medicine", _("Can be assigned as a treating doctor")),
        ]

    objects = VisitManager()

    @property
    def total_cost(self):
        """Billed total: non-cancelled SERVICE lines."""
        from core.services import total_cost
        return total_cost(self)

    @property
    def medicine_total(self):
        """Non-cancelled MEDICINE lines."""
        from core.services import medicine_total
        return medicine_total(self)

    @property
    def cancelled_services_total(self):
        """Cancelled SERVICE lines; refund caps read this."""
        from core.services import cancelled_services_total
        return cancelled_services_total(self)

    @property
    def amount_paid(self):
        """Settled payments minus settled refunds."""
        from core.services import amount_paid
        return amount_paid(self)

    @property
    def already_refunded(self):
        """Settled refunds."""
        from core.services import already_refunded
        return already_refunded(self)

    @property
    def net_balance(self):
        """Signed: positive means the patient owes, negative means the clinic owes."""
        from core.services import net_balance
        return net_balance(self)

    @property
    def amount_due(self):
        """Collectable amount: max(net_balance, 0)."""
        from core.services import amount_due
        return amount_due(self)

    @property
    def actual_refundable(self):
        from core.services import actual_refundable
        return actual_refundable(self)

    @property
    def can_process_refund(self):
        from core.services import can_process_refund
        return can_process_refund(self)

    def _validate_terminal_transition(self, old_status):
        """Completed and cancelled are historical. They cannot be reopened.

        This runs from clean() (form and admin paths) and from save() (direct
        writes), so no ordinary path can rewrite a terminal visit's status.
        """
        if old_status in self.TERMINAL_STATUSES and self.status != old_status:
            raise ValidationError(
                _("A completed or cancelled visit cannot change status."),
                code='terminal_status',
            )

    def clean(self):
        super().clean()
        if self.pk:
            old_status = Visit.objects.filter(pk=self.pk).values_list('status', flat=True).first()
            if old_status:
                self._validate_terminal_transition(old_status)

        if self.assigned_doctor_id and self.visit_datetime:
            overlapping = Visit.objects.filter(
                assigned_doctor_id=self.assigned_doctor_id,
                visit_datetime=self.visit_datetime,
            ).exclude(status__in=[self.CANCELLED, self.NO_SHOW])
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError(
                    _("This doctor is already booked at this time."),
                    code='doctor_overlap',
                )

    def save(self, *args, **kwargs):
        old = None
        if self.pk:
            try:
                old = Visit.objects.get(pk=self.pk)
            except Visit.DoesNotExist:
                old = None

        if old:
            self._validate_terminal_transition(old.status)
            if (
                old.visit_datetime != self.visit_datetime
                and self.status == self.WAITING
                and self.visit_datetime > timezone.now()
            ):
                self.status = self.PLANNED
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.patient.full_name} · {self.visit_datetime:%Y-%m-%d %H:%M}"


class VisitService(models.Model):
    """A priced line on a visit.

    The catalog price is copied here at creation and never changes afterwards,
    so later catalog edits do not rewrite history. Cancellation is the only
    supported removal; the row stays for audit and for refund caps.
    Stock movements and automatic expenses reference this row.
    """

    visit = models.ForeignKey(
        Visit,
        on_delete=models.PROTECT,
        related_name='services',
        verbose_name=_("Visit"),
    )
    service = models.ForeignKey(Service, on_delete=models.PROTECT, verbose_name=_("Service"))
    price_snapshot = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price"))
    quantity = models.PositiveIntegerField(default=1, verbose_name=_("Quantity"))

    is_cancelled = models.BooleanField(default=False, verbose_name=_("Cancelled"))
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Cancelled at"))
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cancelled_visit_services',
        verbose_name=_("Cancelled by"),
    )

    class Meta:
        verbose_name = _("Visit service")
        verbose_name_plural = _("Visit services")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='check_visitservice_quantity_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(price_snapshot__gte=0),
                name='check_visitservice_price_non_negative',
            ),
        ]

    @property
    def line_total(self):
        return self.price_snapshot * self.quantity

    def clean(self):
        super().clean()
        old = getattr(self, '_old_instance', None)
        locked = bool(self.visit_id) and self.visit.status in (Visit.COMPLETED, Visit.CANCELLED)

        if old is None:
            if locked:
                raise ValidationError(
                    _("Line items cannot be added to a completed or cancelled visit."),
                    code='visit_locked',
                )
        else:
            if old.is_cancelled and not self.is_cancelled:
                raise ValidationError(
                    _("Cancellation is final; a cancelled line cannot be restored."),
                    code='cannot_uncancel',
                )
            if self.service_id != old.service_id:
                raise ValidationError(
                    _("The service cannot be changed. Cancel this line and add a new one."),
                    code='service_locked',
                )
            if self.price_snapshot != old.price_snapshot:
                raise ValidationError(
                    _("The price of an existing line cannot be changed."),
                    code='price_locked',
                )
            if locked and self.quantity != old.quantity:
                raise ValidationError(
                    _("The quantity cannot change after the visit is completed or cancelled."),
                    code='quantity_locked',
                )

        if not self.is_cancelled and self.service_id and self.visit_id:
            from inventory.services import StockService

            existing_qty = 0
            if old is not None and not old.is_cancelled:
                existing_qty = old.quantity
            StockService.check_stock_available(
                self.service,
                self.quantity,
                self.visit.floor,
                existing_quantity=existing_qty,
            )

    def save(self, *args, **kwargs):
        if self.pk is None:
            self.price_snapshot = self.service.price

        old_instance = None
        if self.pk:
            try:
                old_instance = VisitService.objects.get(pk=self.pk)
            except VisitService.DoesNotExist:
                pass
        self._old_instance = old_instance
        self.full_clean()

        from inventory.services import StockService
        from core.services import sync_expense

        # Stock movement and the derived accounting expense share the
        # transaction: a failure on either side rolls the line back.
        with transaction.atomic():
            super().save(*args, **kwargs)
            StockService.sync_stock(self, old_instance)
            sync_expense(self)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Physical deletion is forbidden. Cancel the line instead."),
            code='deletion_forbidden',
        )

    def __str__(self):
        status = _(" [cancelled]") if self.is_cancelled else ""
        return f"{self.service.name} x{self.quantity} @ {self.price_snapshot}{status}"


class Operation(models.Model):
    """A surgical procedure. Clinical history; no delete path.

    The stock floor is declared data: the visit's floor when a visit is
    linked, otherwise the patient's floor. There is no guessed fallback.
    """

    patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name='operations',
        verbose_name=_("Patient"),
    )
    visit = models.ForeignKey(
        Visit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='operations',
        verbose_name=_("Visit"),
    )
    passport_data = models.TextField(
        blank=True,
        verbose_name=_("Passport data"),
        help_text=_("Optional; the patient card already carries identity data."),
    )
    operation_type = models.CharField(max_length=255, verbose_name=_("Operation type"))
    performed_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='performed_operations',
        verbose_name=_("Performed by (user)"),
        help_text=_(
            "Linked staff account when known; otherwise record the name in "
            "'Performed by (text)'. Both may be empty when unknown."
        ),
    )
    performed_by_text = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Performed by (text)"),
    )
    operation_datetime = models.DateTimeField(default=timezone.now, verbose_name=_("Operation time"))
    comment = models.TextField(blank=True, verbose_name=_("Comment"))

    class Meta:
        verbose_name = _("Operation")
        verbose_name_plural = _("Operations")
        ordering = ['-operation_datetime', '-pk']

    def clean(self):
        super().clean()
        if self.visit_id and self.patient_id and self.visit.patient_id != self.patient_id:
            raise ValidationError(
                _("The visit belongs to a different patient."),
                code='visit_patient_mismatch',
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def floor(self):
        if self.visit_id:
            return self.visit.floor
        return self.patient.floor

    def __str__(self):
        return f"{self.operation_type} - {self.patient.full_name}"


class OperationConsumable(models.Model):
    """Append-only surgical stock usage.

    A mistaken entry is corrected with a compensating ADJUSTMENT movement, not
    by editing or deleting this row. Stock usage reporting aggregates the
    Consumption ledger, never sums these rows.
    """

    operation = models.ForeignKey(
        Operation,
        on_delete=models.PROTECT,
        related_name='consumables',
        verbose_name=_("Operation"),
    )
    consumable = models.ForeignKey(
        'inventory.Consumable',
        on_delete=models.PROTECT,
        verbose_name=_("Consumable"),
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name=_("Quantity"))
    used_at = models.DateTimeField(default=timezone.now, verbose_name=_("Used at"))
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='used_consumables',
        verbose_name=_("Used by"),
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))

    class Meta:
        verbose_name = _("Operation consumable")
        verbose_name_plural = _("Operation consumables")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='check_operationconsumable_quantity_positive',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                _("A logged consumable cannot be edited. Correct it with a stock adjustment."),
                code='operation_consumable_immutable',
            )
        self.full_clean()

        from inventory.services import StockService

        with transaction.atomic():
            super().save(*args, **kwargs)
            StockService.log_surgical_consumption(self)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("A logged consumable cannot be deleted. Correct it with a stock adjustment."),
            code='operation_consumable_deletion_forbidden',
        )

    def __str__(self):
        return f"{self.consumable.name} x{self.quantity} for {self.operation}"


class MedicationOrder(models.Model):
    """A prescription for a visit. Historical from creation.

    Visit, consumable, quantity, and prescriber are frozen. The only later
    mutation is one-way cancellation, which stops new administrations while
    keeping the row and everything already given.
    """

    visit = models.ForeignKey(
        Visit,
        on_delete=models.PROTECT,
        related_name='medication_orders',
        verbose_name=_("Visit"),
    )
    consumable = models.ForeignKey(
        'inventory.Consumable',
        on_delete=models.PROTECT,
        related_name='medication_orders',
        verbose_name=_("Medication"),
    )
    total_quantity = models.PositiveIntegerField(verbose_name=_("Total quantity"))
    prescribed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='prescribed_medication_orders',
        verbose_name=_("Prescribed by"),
    )
    prescribed_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Prescribed at"))

    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Cancelled at"))
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cancelled_medication_orders',
        verbose_name=_("Cancelled by"),
    )
    cancel_reason = models.CharField(max_length=255, blank=True, verbose_name=_("Cancel reason"))

    class Meta:
        verbose_name = _("Medication order")
        verbose_name_plural = _("Medication orders")
        ordering = ['-prescribed_at', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_quantity__gte=1),
                name='check_medicationorder_quantity_positive',
            ),
        ]

    @property
    def is_active(self):
        return self.cancelled_at is None

    @property
    def administered_quantity(self):
        total = self.administrations.filter(voided_at__isnull=True).aggregate(
            total=models.Sum('quantity_given'),
        )['total']
        return total or 0

    @property
    def remaining_quantity(self):
        return max(0, self.total_quantity - self.administered_quantity)

    def clean(self):
        super().clean()
        old = getattr(self, '_old_instance', None)

        if old is None:
            if self.visit_id and self.visit.status in Visit.TERMINAL_STATUSES:
                raise ValidationError(
                    _("The visit is closed; new prescriptions are not allowed."),
                    code='visit_closed',
                )
            return

        if (
            self.visit_id != old.visit_id
            or self.consumable_id != old.consumable_id
            or self.total_quantity != old.total_quantity
            or self.prescribed_by_id != old.prescribed_by_id
        ):
            raise ValidationError(
                _("A prescription cannot be changed. Cancel it and prescribe again."),
                code='order_immutable',
            )
        if old.cancelled_at and not self.cancelled_at:
            raise ValidationError(
                _("Cancellation is final."),
                code='cannot_uncancel_order',
            )

    def save(self, *args, **kwargs):
        old_instance = None
        if self.pk:
            try:
                old_instance = MedicationOrder.objects.get(pk=self.pk)
            except MedicationOrder.DoesNotExist:
                pass
        self._old_instance = old_instance
        self.full_clean()
        super().save(*args, **kwargs)

    def cancel(self, user, reason=''):
        if not self.is_active:
            raise ValidationError(_("This prescription is already cancelled."))
        self.cancelled_at = timezone.now()
        self.cancelled_by = user
        self.cancel_reason = reason
        self.save(update_fields=['cancelled_at', 'cancelled_by', 'cancel_reason'])

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Prescriptions are clinical history and cannot be deleted."),
            code='order_deletion_forbidden',
        )

    def __str__(self):
        return f"{self.consumable.name} x{self.total_quantity} for visit #{self.visit_id}"


class MedicationAdministration(models.Model):
    """An administration event. Append-only; voiding is a one-way record.

    Voided rows stay visible. Quantity totals count effective rows only, and
    voiding restores stock at the location the original deduction used.
    """

    order = models.ForeignKey(
        MedicationOrder,
        on_delete=models.PROTECT,
        related_name='administrations',
        verbose_name=_("Order"),
    )
    administered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='medication_administrations',
        verbose_name=_("Administered by"),
    )
    administered_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Administered at"))
    quantity_given = models.PositiveIntegerField(verbose_name=_("Quantity given"))

    voided_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Voided at"))
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='voided_medication_administrations',
        verbose_name=_("Voided by"),
    )
    void_reason = models.CharField(max_length=255, blank=True, verbose_name=_("Void reason"))

    class Meta:
        verbose_name = _("Medication administration")
        verbose_name_plural = _("Medication administrations")
        ordering = ['-administered_at', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_given__gte=1),
                name='check_medicationadministration_quantity_positive',
            ),
        ]

    @property
    def is_voided(self):
        return self.voided_at is not None

    def clean(self):
        super().clean()
        old = getattr(self, '_old_instance', None)

        if old is None:
            if self.order.visit.status in Visit.TERMINAL_STATUSES:
                raise ValidationError(
                    _("The visit is closed; new administrations are not allowed."),
                    code='visit_closed',
                )
            if not self.order.is_active:
                raise ValidationError(
                    _("This prescription is cancelled."),
                    code='order_cancelled',
                )
            if self.order.remaining_quantity < self.quantity_given:
                raise ValidationError(
                    _("Cannot administer more than the remaining quantity."),
                    code='over_remaining',
                )
            return

        if (
            self.order_id != old.order_id
            or self.quantity_given != old.quantity_given
            or self.administered_by_id != old.administered_by_id
        ):
            raise ValidationError(
                _("A recorded administration cannot be changed. Void it instead."),
                code='administration_immutable',
            )
        if old.voided_at and not self.voided_at:
            raise ValidationError(
                _("A void is final."),
                code='cannot_unvoid',
            )

    def save(self, *args, **kwargs):
        from inventory.services import StockService

        old_instance = None
        if self.pk:
            try:
                old_instance = MedicationAdministration.objects.get(pk=self.pk)
            except MedicationAdministration.DoesNotExist:
                pass
        self._old_instance = old_instance
        self.full_clean()

        if old_instance is None:
            with transaction.atomic():
                super().save(*args, **kwargs)
                StockService.log_medication_administration(self)
            return

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Administrations are clinical history and cannot be deleted."),
            code='administration_deletion_forbidden',
        )

    def __str__(self):
        return f"Administration #{self.pk or 'new'} for order #{self.order_id}"
