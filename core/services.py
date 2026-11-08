"""Canonical money and line-item reads, plus the payment write paths.

One rule: only VERIFIED money counts as paid or refunded. Pending payments
count toward the recording cap so two people cannot record past the balance,
but they never reduce it. Rejected money counts nowhere.
"""
import warnings
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from .audit import audit
from .models import AuditLog, Expense, ExpenseCategory, Payment, PaymentMethod, Visit

_NPLUS1_HINT = (
    "Pass Visit.objects.with_financial_annotations() "
    "(or with_line_item_totals() for line data) to avoid this lookup."
)


def _annotated(visit, attr, fallback):
    if hasattr(visit, attr):
        return getattr(visit, attr)
    warnings.warn(
        f"Visit.{attr} accessed without with_financial_annotations(). {_NPLUS1_HINT}",
        RuntimeWarning,
        stacklevel=3,
    )
    return fallback(visit) or Decimal('0')


def _line_total(visit, category, cancelled):
    return visit.services.filter(
        is_cancelled=cancelled,
        service__category=category,
    ).aggregate(total=Sum(F('price_snapshot') * F('quantity')))['total'] or Decimal('0')


def total_cost(visit):
    """Billed total: non-cancelled SERVICE lines."""
    return _annotated(
        visit, 'annotated_service_total', lambda v: _line_total(v, 'SERVICE', False),
    )


def medicine_total(visit):
    """Non-cancelled MEDICINE lines."""
    return _annotated(
        visit, 'annotated_medicine_total', lambda v: _line_total(v, 'MEDICINE', False),
    )


def cancelled_services_total(visit):
    """Cancelled SERVICE lines; refund caps read this."""
    return _annotated(
        visit, 'annotated_cancelled_total', lambda v: _line_total(v, 'SERVICE', True),
    )


# ------------------------------------------------------------------ #
# Settled money reads                                                 #
# ------------------------------------------------------------------ #

def amount_paid(visit):
    """Settled payments minus settled refunds. Pending money is not paid."""
    return _annotated(
        visit,
        'annotated_amount_paid',
        lambda v: _paid_in(v) - _refunded(v),
    )


def already_refunded(visit):
    """Settled refunds."""
    return _annotated(
        visit,
        'annotated_refunded',
        lambda v: _refunded(v),
    )


def net_balance(visit):
    """Signed balance: positive means the patient owes, negative means the clinic owes.

    Negative only after a cancellation outruns the refund; recover it through
    actual_refundable, never by treating a negative balance as a payment.
    """
    if hasattr(visit, 'annotated_net_balance'):
        return visit.annotated_net_balance
    warnings.warn(
        f"Visit.annotated_net_balance accessed without "
        f"with_financial_annotations(). {_NPLUS1_HINT}",
        RuntimeWarning,
        stacklevel=3,
    )
    return total_cost(visit) - amount_paid(visit)


def amount_due(visit):
    """Collectable amount: the positive part of the signed balance."""
    return max(Decimal('0'), net_balance(visit))


def actual_refundable(visit):
    """Refund cap: capped by unrefunded cancelled lines and by net settled pay."""
    cancelled = cancelled_services_total(visit)
    refunded = already_refunded(visit)
    paid = amount_paid(visit)
    return max(Decimal('0'), min(cancelled - refunded, paid))


def can_process_refund(visit):
    return actual_refundable(visit) > 0


def recorded_net(visit):
    """Non-rejected payments minus non-rejected refunds.

    Not a money truth; the booking guard that stops two people from recording
    past the balance before verification happens.
    """
    totals = visit.payments.recorded().aggregate(
        paid=Sum('amount', filter=Q(type=Payment.PAYMENT)),
        refunded=Sum('amount', filter=Q(type=Payment.REFUND)),
    )
    return (totals['paid'] or Decimal('0')) - (totals['refunded'] or Decimal('0'))


def _paid_in(visit):
    return visit.payments.settled_payments().aggregate(total=Sum('amount'))['total'] or Decimal('0')


def _refunded(visit):
    return visit.payments.settled_refunds().aggregate(total=Sum('amount'))['total'] or Decimal('0')


# ------------------------------------------------------------------ #
# Visit transition helpers                                            #
# ------------------------------------------------------------------ #

def accept_visit_request(visit):
    """REQUESTED -> PLANNED. Shared by the accept view and the agent gateway."""
    if visit.status != Visit.REQUESTED:
        raise ValidationError(_("Only requested visits can be accepted."))
    visit.status = Visit.PLANNED
    visit.save(update_fields=['status', 'updated_at'])
    return visit


# ------------------------------------------------------------------ #
# Payment write paths                                                 #
# ------------------------------------------------------------------ #

def record_payment(visit, user, amount, method, notes=''):
    """Record a pending payment under the visit's booking cap.

    The visit row is locked so two requests cannot both record past the
    balance; the cap is recomputed inside the lock, not trusted from a form.
    """
    with transaction.atomic():
        locked = Visit.objects.select_for_update().get(pk=visit.pk)
        cap = _line_total(locked, 'SERVICE', False) - recorded_net(locked)
        if amount > cap:
            raise ValidationError(
                _("The amount exceeds the outstanding balance (%(cap)s).") % {'cap': cap}
            )
        payment = Payment.objects.create(
            visit=locked,
            amount=amount,
            method=method,
            type=Payment.PAYMENT,
            status=Payment.PENDING,
            created_by=user,
            notes=notes,
        )
        audit(
            action=AuditLog.CREATE,
            obj=payment,
            description='Payment recorded',
            metadata={'amount': str(amount), 'method': method.code},
        )
        return payment


def process_refund(visit, user, notes=''):
    """Refund the full refundable amount as a settled refund.

    Locked and recomputed, so a concurrent refund cannot exceed the cap.
    """
    with transaction.atomic():
        locked = Visit.objects.select_for_update().get(pk=visit.pk)
        cap = actual_refundable(locked)
        if cap <= 0:
            raise ValidationError(_("There is no amount to refund."))

        last_payment = (
            locked.payments.settled_payments()
            .order_by('-payment_datetime', '-pk')
            .first()
        )
        if last_payment is not None:
            method = last_payment.method
        else:
            method = PaymentMethod.objects.filter(active=True).order_by('order', 'id').first()
        if method is None:
            raise ValidationError(_("No payment method is configured."))

        refund = Payment.objects.create(
            visit=locked,
            amount=cap,
            method=method,
            type=Payment.REFUND,
            status=Payment.VERIFIED,
            created_by=user,
            verified_by=user,
            verified_at=timezone.now(),
            notes=notes,
        )
        audit(
            action=AuditLog.REFUND,
            obj=refund,
            description='Refund recorded',
            metadata={'amount': str(cap), 'method': method.code},
            critical=True,
        )
        return refund


MEDICINES_CATEGORY = 'medicines'


def sync_expense(visit_service):
    """Mirror an active MEDICINE line into an accounting expense.

    The expense is derived, not independent history: quantity changes update
    it and cancellation withdraws it. The one-to-one key enforces at most one
    row per line; a concurrent create fails on the unique constraint and rolls
    its transaction back instead of duplicating.
    """
    if visit_service.service.category != 'MEDICINE':
        return

    if visit_service.is_cancelled:
        if visit_service.visit.status == Visit.COMPLETED:
            # The cost was incurred before the visit closed. The cancellation
            # is a settlement correction, so the expense stays.
            return
        Expense.objects.filter(visit_service=visit_service).delete()
        return

    category = ExpenseCategory.objects.filter(code=MEDICINES_CATEGORY).first()
    if category is None:
        raise ValidationError(
            _("Expense category '%(code)s' is missing. Run seed_expense_categories.")
            % {'code': MEDICINES_CATEGORY}
        )

    method = PaymentMethod.objects.filter(
        active=True, kind=PaymentMethod.CASH,
    ).order_by('order', 'id').first()

    defaults = {
        'category': category,
        'amount': visit_service.price_snapshot * visit_service.quantity,
        'recipient': visit_service.service.name,
        'explanation': _("Stock issue - visit #%(visit)s - %(service)s x%(qty)s")
        % {
            'visit': visit_service.visit_id,
            'service': visit_service.service.name,
            'qty': visit_service.quantity,
        },
        'method': method,
        'floor': visit_service.visit.floor,
        'created_by': visit_service.visit.assigned_doctor or visit_service.visit.creator,
    }

    updated = Expense.objects.filter(visit_service=visit_service).update(**defaults)
    if updated == 0:
        Expense.objects.create(visit_service=visit_service, **defaults)


def record_administration(order, user, quantity):
    """Create an administration under the order lock.

    The remaining quantity is recomputed inside the lock, so two nurses cannot
    jointly exceed the ordered amount. Stock failure rolls the row back, and a
    lost race leaves no stock movement.
    """
    from .models import MedicationAdministration, MedicationOrder

    with transaction.atomic():
        locked = MedicationOrder.objects.select_for_update().get(pk=order.pk)
        if not locked.is_active:
            raise ValidationError(_("This prescription is cancelled."))
        if locked.visit.status in Visit.TERMINAL_STATUSES:
            raise ValidationError(
                _("The visit is closed; new administrations are not allowed.")
            )
        if quantity < 1:
            raise ValidationError(_("The quantity must be a whole number of at least 1."))
        if locked.remaining_quantity < quantity:
            raise ValidationError(_("Cannot administer more than the remaining quantity."))

        administration = MedicationAdministration.objects.create(
            order=locked,
            administered_by=user,
            quantity_given=quantity,
        )
        audit(
            action=AuditLog.CREATE,
            obj=administration,
            description='Administration recorded',
            metadata={'order_id': order.pk, 'quantity': quantity},
        )
        return administration


def void_administration(administration, user, reason=''):
    """Void an administration and restore stock at its original location.

    Allowed after the order or visit becomes terminal: this is a historical
    correction, not new treatment. The void metadata and the compensating
    movement commit together.
    """
    from .models import MedicationAdministration
    from inventory.services import StockService

    with transaction.atomic():
        locked = MedicationAdministration.objects.select_for_update().get(
            pk=administration.pk,
        )
        if locked.is_voided:
            raise ValidationError(_("This administration is already voided."))

        locked.voided_at = timezone.now()
        locked.voided_by = user
        locked.void_reason = reason
        locked.save(update_fields=['voided_at', 'voided_by', 'void_reason'])

        StockService.restore_medication_administration(locked, user, reason)
        audit(
            action=AuditLog.VOID,
            obj=locked,
            description='Administration voided',
            metadata={'quantity': locked.quantity_given},
        )
        return locked
