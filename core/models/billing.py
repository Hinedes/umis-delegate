from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..constants import FLOOR_CHOICES
from ..managers import ExpenseManager, PaymentManager

ZERO = Decimal('0')


class PaymentMethod(models.Model):
    """Configurable payment channel.

    Business labels live here; code groups by ``kind`` so cash stays cash
    regardless of which bank a deployment configures.
    """

    CASH = 'CASH'
    CARD = 'CARD'
    TRANSFER = 'TRANSFER'
    OTHER = 'OTHER'
    KIND_CHOICES = [
        (CASH, _("Cash")),
        (CARD, _("Card")),
        (TRANSFER, _("Bank transfer")),
        (OTHER, _("Other")),
    ]

    code = models.CharField(max_length=50, unique=True, verbose_name=_("Code"))
    name = models.CharField(max_length=100, verbose_name=_("Label"))
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=CASH, verbose_name=_("Kind"))
    active = models.BooleanField(default=True, verbose_name=_("Active"))
    order = models.PositiveIntegerField(default=0, verbose_name=_("Order"))

    class Meta:
        verbose_name = _("Payment method")
        verbose_name_plural = _("Payment methods")
        ordering = ['order', 'name']

    def clean(self):
        super().clean()
        if not self.pk:
            return

        old = PaymentMethod.objects.filter(pk=self.pk).values('code', 'kind').first()
        if old is None:
            return

        if Payment.objects.filter(method_id=self.pk).exists():
            if self.code != old['code']:
                raise ValidationError(
                    _("The code of a method used by payments cannot change. Create a new method instead."),
                    code='method_code_locked',
                )
            if self.kind != old['kind']:
                raise ValidationError(
                    _("The kind of a method used by payments cannot change. Create a new method instead."),
                    code='method_kind_locked',
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Payment(models.Model):
    """Money recorded against a visit, and refunds of that money.

    One state field decides everything:

    - PENDING: recorded, not confirmed. Counts toward the recording cap only.
    - VERIFIED: settled. The only status that counts as paid or refunded.
    - REJECTED: discarded. Counts nowhere.

    Refunds are recorded as VERIFIED in one authorized action. Amounts and
    methods never change after recording; corrections are rejections (while
    pending) or refunds (after settling).
    """

    PAYMENT = 'PAYMENT'
    REFUND = 'REFUND'
    TYPE_CHOICES = [
        (PAYMENT, _("Payment")),
        (REFUND, _("Refund")),
    ]

    PENDING = 'PENDING'
    VERIFIED = 'VERIFIED'
    REJECTED = 'REJECTED'
    STATUS_CHOICES = [
        (PENDING, _("Pending")),
        (VERIFIED, _("Verified")),
        (REJECTED, _("Rejected")),
    ]

    visit = models.ForeignKey(
        'Visit',
        on_delete=models.PROTECT,
        related_name='payments',
        verbose_name=_("Visit"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
        verbose_name=_("Amount"),
    )
    method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.PROTECT,
        verbose_name=_("Method"),
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=PAYMENT, verbose_name=_("Type"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING, verbose_name=_("Status"))
    payment_datetime = models.DateTimeField(default=timezone.now, verbose_name=_("Recorded at"))
    notes = models.TextField(blank=True, verbose_name=_("Notes"))

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_payments',
        verbose_name=_("Created by"),
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_payments',
        verbose_name=_("Verified by"),
    )
    verified_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Verified at"))

    class Meta:
        verbose_name = _("Payment")
        verbose_name_plural = _("Payments")
        ordering = ['-payment_datetime', '-pk']
        permissions = [
            ("view_payment_ledger", _("Can view the payment ledger")),
            ("verify_payment", _("Can verify or reject payments")),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name='check_payment_amount_non_negative',
            ),
        ]

    objects = PaymentManager()

    def clean(self):
        super().clean()

        if self.pk:
            old = Payment.objects.filter(pk=self.pk).only(
                'status', 'amount', 'visit_id', 'method_id', 'type',
            ).first()
            if old is not None:
                if (
                    self.amount != old.amount
                    or self.visit_id != old.visit_id
                    or self.method_id != old.method_id
                    or self.type != old.type
                ):
                    raise ValidationError(
                        _("A recorded payment cannot be changed."),
                        code='payment_immutable',
                    )
                if old.status != self.PENDING and self.status != old.status:
                    raise ValidationError(
                        _("Only a pending payment can change status."),
                        code='status_terminal',
                    )
            return

        if self.type == self.REFUND and self.status != self.VERIFIED:
            raise ValidationError(
                _("Refunds are recorded as settled."),
                code='refund_verified',
            )
        if self.method_id and not self.method.active:
            raise ValidationError(
                _("This payment method is inactive."),
                code='method_inactive',
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Recorded payments cannot be deleted. Reject or refund them instead."),
            code='payment_deletion_forbidden',
        )

    def mark_verified(self, user):
        if self.status != self.PENDING:
            raise ValidationError(_("Only a pending payment can be verified."))
        self.status = self.VERIFIED
        self.verified_by = user
        self.verified_at = timezone.now()
        self.save(update_fields=['status', 'verified_by', 'verified_at'])

    def mark_rejected(self, user):
        if self.status != self.PENDING:
            raise ValidationError(_("Only a pending payment can be rejected."))
        self.status = self.REJECTED
        self.verified_by = user
        self.verified_at = timezone.now()
        self.save(update_fields=['status', 'verified_by', 'verified_at'])

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} ({self.method.name})"


class ExpenseCategory(models.Model):
    """Configurable cost category with a stable code.

    Accounting meaning lives in the code; the label is display only. Once a
    category is referenced, the code is frozen.
    """

    code = models.CharField(max_length=50, unique=True, verbose_name=_("Code"))
    name = models.CharField(max_length=100, verbose_name=_("Label"))
    active = models.BooleanField(default=True, verbose_name=_("Active"))
    order = models.PositiveIntegerField(default=0, verbose_name=_("Order"))

    class Meta:
        verbose_name = _("Expense category")
        verbose_name_plural = _("Expense categories")
        ordering = ['order', 'name']

    def clean(self):
        super().clean()
        if not self.pk:
            return
        old = ExpenseCategory.objects.filter(pk=self.pk).values('code').first()
        if old is not None and self.code != old['code']:
            if Expense.objects.filter(category_id=self.pk).exists():
                raise ValidationError(
                    _("The code of a category used by expenses cannot change. Create a new category instead."),
                    code='category_code_locked',
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Expense(models.Model):
    """A cost the clinic incurred.

    Costs have two origins and no other:

    - manual: recorded by staff through the expense form.
    - automatic: the accounting mirror of an active MEDICINE visit line.
      It is maintained by sync_expense while the line exists, and withdrawn
      when the line is cancelled.

    Expenses never represent patient settlement. Payments answer that.

    A recorded expense is append-only: the amount, category, and origin never
    change. The only correction paths are deletion of a manual expense by a
    user holding delete_expense, and the automatic mirror following its line.
    """

    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        verbose_name=_("Category"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
        verbose_name=_("Amount"),
    )
    recipient = models.CharField(max_length=255, verbose_name=_("Recipient"))
    explanation = models.TextField(verbose_name=_("Explanation"))
    method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_("Method"),
    )
    floor = models.CharField(
        max_length=10,
        choices=FLOOR_CHOICES,
        default=FLOOR_CHOICES[0][0],
        verbose_name=_("Floor"),
    )
    date = models.DateTimeField(default=timezone.now, verbose_name=_("Date"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Created by"),
    )
    visit_service = models.OneToOneField(
        'VisitService',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expense',
        verbose_name=_("Visit service"),
        help_text=_("Set for automatic medicine expenses."),
    )

    class Meta:
        verbose_name = _("Expense")
        verbose_name_plural = _("Expenses")
        ordering = ['-date', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name='check_expense_amount_non_negative',
            ),
        ]

    objects = ExpenseManager()

    @property
    def is_automatic(self):
        return self.visit_service_id is not None

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                _("A recorded expense cannot be edited."),
                code='expense_immutable',
            )
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.category.name} - {self.amount}"
