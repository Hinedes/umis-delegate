from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..constants import FLOOR_CHOICES
from .billing import Expense, Payment, PaymentMethod

ZERO = Decimal('0')


class Shift(models.Model):
    """A cash drawer session for one floor.

    Reconciliation rule, in one place:

        expected_cash = opening_cash + verified_cash_in
                        - verified_cash_refunds - manual_cash_expenses

    Only VERIFIED payments count. Pending and rejected count nowhere. Only
    methods whose kind is CASH touch the drawer. Automatic medicine expenses
    are accounting costs, not cash movements, and are excluded.

    A closed shift is history. Its snapshot fields are what every report
    reads; source records that appear later cannot rewrite it.

    Snapshot scope: the drawer figures and manual costs. Automatic medicine
    expenses are accounting costs and are never snapshotted into a shift;
    general cost analytics aggregate them directly from expenses.
    """

    OPEN = 'OPEN'
    CLOSED = 'CLOSED'
    STATUS_CHOICES = [
        (OPEN, _("Open")),
        (CLOSED, _("Closed")),
    ]

    floor = models.CharField(max_length=10, choices=FLOOR_CHOICES, verbose_name=_("Floor"))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN, verbose_name=_("Status"))
    opened_at = models.DateTimeField(default=timezone.now, verbose_name=_("Opened at"))
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Closed at"))
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='shifts_opened',
        verbose_name=_("Opened by"),
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='shifts_closed',
        verbose_name=_("Closed by"),
    )

    opening_cash = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        verbose_name=_("Opening cash"),
    )
    closing_cash = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name=_("Closing cash"),
    )
    expected_cash = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name=_("Expected cash"),
    )
    cash_difference = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name=_("Difference"),
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))

    # Frozen at closure. Reports read these instead of re-querying history.
    snapshot_patient_count = models.PositiveIntegerField(null=True, blank=True)
    snapshot_visit_count = models.PositiveIntegerField(null=True, blank=True)
    snapshot_total_payments = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    snapshot_total_refunds = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    snapshot_manual_expenses = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    snapshot_cash_in = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    snapshot_cash_refund = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    snapshot_cash_expenses = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = _("Shift")
        verbose_name_plural = _("Shifts")
        ordering = ['-opened_at', '-pk']
        permissions = [
            ("manage_shift", _("Can manage shifts")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['floor'],
                condition=models.Q(status='OPEN'),
                name='uniq_open_shift_per_floor',
            ),
        ]

    def __str__(self):
        return f"Shift {self.floor} - {self.opened_at:%Y-%m-%d %H:%M}"

    def period_totals(self):
        """Live totals for the shift window, inclusive of both boundaries."""
        from .visit import Visit

        end = self.closed_at or timezone.now()
        verified = Payment.objects.verified().filter(
            visit__floor=self.floor,
            payment_datetime__range=(self.opened_at, end),
        )
        cash = verified.filter(method__kind=PaymentMethod.CASH)

        payments = verified.payments()
        refunds = verified.refunds()
        expenses = Expense.objects.manual().filter(
            floor=self.floor,
            date__range=(self.opened_at, end),
        )

        visits = Visit.objects.filter(
            floor=self.floor,
            visit_datetime__range=(self.opened_at, end),
        ).aggregate(total=models.Count('id'), patients=models.Count('patient', distinct=True))

        return {
            'cash_in': cash.payments().aggregate(total=models.Sum('amount'))['total'] or ZERO,
            'cash_refund': cash.refunds().aggregate(total=models.Sum('amount'))['total'] or ZERO,
            'cash_expenses': expenses.filter(
                method__kind=PaymentMethod.CASH,
            ).aggregate(total=models.Sum('amount'))['total'] or ZERO,
            'total_payments': payments.aggregate(total=models.Sum('amount'))['total'] or ZERO,
            'total_refunds': refunds.aggregate(total=models.Sum('amount'))['total'] or ZERO,
            'manual_expenses': expenses.aggregate(total=models.Sum('amount'))['total'] or ZERO,
            'visit_count': visits['total'] or 0,
            'patient_count': visits['patients'] or 0,
        }

    def expected_cash_total(self, totals=None):
        totals = totals or self.period_totals()
        return (
            self.opening_cash
            + totals['cash_in']
            - totals['cash_refund']
            - totals['cash_expenses']
        )

    def close(self, user, closing_cash, notes=''):
        if self.status != self.OPEN:
            raise ValidationError(_("This shift is already closed."))

        totals = self.period_totals()
        self.status = self.CLOSED
        self.closed_at = timezone.now()
        self.closed_by = user
        self.closing_cash = closing_cash
        self.expected_cash = self.expected_cash_total(totals)
        self.cash_difference = closing_cash - self.expected_cash
        self.notes = notes
        self.snapshot_patient_count = totals['patient_count']
        self.snapshot_visit_count = totals['visit_count']
        self.snapshot_total_payments = totals['total_payments']
        self.snapshot_total_refunds = totals['total_refunds']
        self.snapshot_manual_expenses = totals['manual_expenses']
        self.snapshot_cash_in = totals['cash_in']
        self.snapshot_cash_refund = totals['cash_refund']
        self.snapshot_cash_expenses = totals['cash_expenses']
        self.save()

    def save(self, *args, **kwargs):
        if self.pk:
            old = Shift.objects.filter(pk=self.pk).values('status', 'floor').first()
            if old is not None:
                if old['status'] == self.CLOSED:
                    raise ValidationError(
                        _("A closed shift cannot change."),
                        code='shift_closed',
                    )
                if old['floor'] != self.floor:
                    raise ValidationError(
                        _("The floor of an existing shift cannot change."),
                        code='shift_floor_locked',
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Shifts are reconciliation history and cannot be deleted."),
            code='shift_deletion_forbidden',
        )
