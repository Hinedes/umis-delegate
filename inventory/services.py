from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import F
from django.utils.translation import gettext_lazy as _

from core.models import Visit

from .models import Consumable, Consumption, Location, Stock


class StockService:
    """Stock movements for medicine services and manual adjustments.

    Every movement is a signed delta recorded in Consumption. Deductions use a
    conditional UPDATE (`quantity >= n`) so two concurrent requests cannot
    oversell the same row; the loser gets a ValidationError and its enclosing
    transaction rolls back.
    """

    @staticmethod
    def _not_enough(consumable, available, requested):
        return ValidationError(
            _("Insufficient stock for %(item)s. Available: %(available)s, requested: %(requested)s.")
            % {'item': consumable.name, 'available': available, 'requested': requested}
        )

    @staticmethod
    def _resolve_context(visit_service):
        """Return (consumable, location, user), or None when the service is not stocked."""
        if visit_service.service.category != 'MEDICINE':
            return None

        consumable = Consumable.objects.filter(service=visit_service.service).first()
        if consumable is None:
            return None
        if not consumable.active:
            raise ValidationError(
                _("%(item)s is deactivated and cannot be added to visits.") % {'item': consumable.name}
            )

        location = Location.objects.filter(floor=visit_service.visit.floor).order_by('id').first()
        if location is None:
            raise ValidationError(
                _("No stock location is configured for floor %(floor)s.") % {'floor': visit_service.visit.floor}
            )

        user = getattr(visit_service, '_current_user', None) or visit_service.visit.creator
        return consumable, location, user

    @staticmethod
    def _movement(stock, delta, reason, user, visit_service=None, notes='', operation_consumable=None, medication_administration=None):
        """Apply a signed delta to one stock row and log it."""
        if delta < 0:
            updated = Stock.objects.filter(
                pk=stock.pk,
                quantity__gte=-delta,
            ).update(quantity=F('quantity') + delta)
            if updated == 0:
                raise StockService._not_enough(stock.consumable, stock.quantity, -delta)
            stock.refresh_from_db()
        else:
            updated = Stock.objects.filter(pk=stock.pk).update(quantity=F('quantity') + delta)
            if updated == 0:
                raise ValidationError(
                    _("No stock row for %(item)s at %(location)s.")
                    % {'item': stock.consumable.name, 'location': stock.location}
                )

        try:
            Consumption.objects.create(
                consumable=stock.consumable,
                location=stock.location,
                quantity=delta,
                reason=reason,
                visit_service=visit_service,
                operation_consumable=operation_consumable,
                medication_administration=medication_administration,
                notes=notes,
                created_by=user,
            )
        except IntegrityError:
            # The unique constraint on (visit_service, VISIT/CANCEL) means a
            # concurrent execution already recorded this transition. Raising
            # rolls the earlier deduction back with the rest of the block.
            raise ValidationError(_("This stock movement was already recorded."))

    @staticmethod
    def _visit_stock(consumable, location):
        stock = Stock.objects.filter(consumable=consumable, location=location).first()
        if stock is None:
            raise ValidationError(
                _("No stock row for %(item)s at %(location)s. Adjust stock first.")
                % {'item': consumable.name, 'location': location}
            )
        return stock

    @staticmethod
    def sync_stock(visit_service, old_instance=None):
        """Sync stock for a visit line, keyed on the line itself.

        Called from VisitService.save() inside its atomic block. The
        Consumption guard makes repeated processing a no-op.
        """
        context = StockService._resolve_context(visit_service)
        if context is None:
            return
        consumable, location, user = context

        if old_instance is None:
            if Consumption.objects.filter(visit_service=visit_service, reason=Consumption.VISIT).exists():
                return
            stock = StockService._visit_stock(consumable, location)
            StockService._movement(
                stock,
                delta=-visit_service.quantity,
                reason=Consumption.VISIT,
                user=user,
                visit_service=visit_service,
                notes=_("Visit #%(visit)s - %(service)s")
                % {'visit': visit_service.visit_id, 'service': visit_service.service.name},
            )
            return

        if not old_instance.is_cancelled and visit_service.is_cancelled:
            if visit_service.visit.status == Visit.COMPLETED:
                # The medicine left stock before the visit closed. Cancelling
                # the line is a settlement correction, not a physical return.
                return
            if Consumption.objects.filter(visit_service=visit_service, reason=Consumption.CANCEL).exists():
                return
            stock = StockService._visit_stock(consumable, location)
            StockService._movement(
                stock,
                delta=visit_service.quantity,
                reason=Consumption.CANCEL,
                user=user,
                visit_service=visit_service,
                notes=_("Cancelled - visit #%(visit)s - %(service)s")
                % {'visit': visit_service.visit_id, 'service': visit_service.service.name},
            )
            return

        if visit_service.is_cancelled:
            return

        diff = visit_service.quantity - old_instance.quantity
        if diff == 0:
            return
        stock = StockService._visit_stock(consumable, location)
        StockService._movement(
            stock,
            delta=-diff,
            reason=Consumption.ADJUSTMENT,
            user=user,
            visit_service=visit_service,
            notes=_("Quantity change: %(old)s -> %(new)s")
            % {'old': old_instance.quantity, 'new': visit_service.quantity},
        )

    @staticmethod
    def check_stock_available(service, quantity, floor, existing_quantity=0):
        """Pre-flight availability check for the user-facing message.

        The conditional UPDATE in _movement is the real guard; this check can
        go stale between here and the save.
        """
        if service.category != 'MEDICINE':
            return

        consumable = Consumable.objects.filter(service=service).first()
        if consumable is None:
            return

        location = Location.objects.filter(floor=floor).order_by('id').first()
        if location is None:
            raise ValidationError(
                _("No stock location is configured for floor %(floor)s.") % {'floor': floor}
            )

        stock = Stock.objects.filter(consumable=consumable, location=location).first()
        if stock is None:
            raise ValidationError(
                _("No stock row for %(item)s at %(location)s. Adjust stock first.")
                % {'item': consumable.name, 'location': location}
            )

        available = stock.quantity + existing_quantity
        if quantity > available:
            raise StockService._not_enough(consumable, available, quantity)

    @staticmethod
    def apply_adjustment(consumable, location, delta, reason, notes, user):
        if delta == 0:
            raise ValidationError(_("Adjustment quantity cannot be zero."))

        stock, _created = Stock.objects.get_or_create(
            consumable=consumable,
            location=location,
            defaults={'quantity': 0},
        )
        if delta < 0 and stock.quantity < -delta:
            raise StockService._not_enough(consumable, stock.quantity, -delta)

        StockService._movement(stock, delta=delta, reason=reason, user=user, notes=notes)
        stock.refresh_from_db()
        return stock

    @staticmethod
    def log_surgical_consumption(operation_consumable):
        """Deduct surgical usage from the operation's declared floor.

        The floor is the visit's floor when linked, otherwise the patient's
        declared floor. There is no guessed fallback. The partial unique
        constraint on (operation_consumable, SURGERY) makes a duplicate or
        concurrent log fail the transaction instead of double-deducting.
        """
        consumable = operation_consumable.consumable
        operation = operation_consumable.operation
        user = operation_consumable.used_by or operation.performed_by_user

        stock = (
            Stock.objects.filter(consumable=consumable, location__floor=operation.floor)
            .select_related('location')
            .order_by('location__id')
            .first()
        )
        if stock is None:
            raise ValidationError(
                _("No stock row for %(item)s on floor %(floor)s.")
                % {'item': consumable.name, 'floor': operation.floor}
            )

        if Consumption.objects.filter(
            operation_consumable=operation_consumable,
            reason=Consumption.SURGERY,
        ).exists():
            return

        StockService._movement(
            stock,
            delta=-operation_consumable.quantity,
            reason=Consumption.SURGERY,
            user=user,
            operation_consumable=operation_consumable,
            notes=_("Operation #%(operation)s - %(type)s - %(item)s")
            % {
                'operation': operation.pk,
                'type': operation.operation_type,
                'item': consumable.name,
            },
        )

    @staticmethod
    def log_medication_administration(administration):
        """Deduct the administered quantity at the visit's floor.

        Duplicate logging is blocked by the partial unique constraint on
        (medication_administration, MEDICATION).
        """
        order = administration.order
        consumable = order.consumable
        floor = order.visit.floor

        stock = (
            Stock.objects.filter(consumable=consumable, location__floor=floor)
            .select_related('location')
            .order_by('location__id')
            .first()
        )
        if stock is None:
            raise ValidationError(
                _("No stock row for %(item)s on floor %(floor)s.")
                % {'item': consumable.name, 'floor': floor}
            )

        if Consumption.objects.filter(
            medication_administration=administration,
            reason=Consumption.MEDICATION,
        ).exists():
            return

        StockService._movement(
            stock,
            delta=-administration.quantity_given,
            reason=Consumption.MEDICATION,
            user=administration.administered_by,
            medication_administration=administration,
            notes=_("Order #%(order)s - %(item)s x%(qty)s")
            % {
                'order': order.pk,
                'item': consumable.name,
                'qty': administration.quantity_given,
            },
        )

    @staticmethod
    def restore_medication_administration(administration, user, reason=''):
        """Compensate a voided administration at its original location.

        The original MEDICATION movement is the physical source of truth, so a
        later floor change cannot redirect the restore.
        """
        original = Consumption.objects.filter(
            medication_administration=administration,
            reason=Consumption.MEDICATION,
        ).select_related('location').first()
        if original is None:
            raise ValidationError(
                _("The original stock movement was not found; nothing to restore.")
            )

        if Consumption.objects.filter(
            medication_administration=administration,
            reason=Consumption.MEDICATION_VOID,
        ).exists():
            return

        stock = Stock.objects.filter(
            consumable=original.consumable,
            location=original.location,
        ).first()
        if stock is None:
            raise ValidationError(
                _("No stock row for %(item)s at %(location)s.")
                % {'item': original.consumable.name, 'location': original.location}
            )

        StockService._movement(
            stock,
            delta=administration.quantity_given,
            reason=Consumption.MEDICATION_VOID,
            user=user,
            medication_administration=administration,
            notes=reason or _("Voided administration #%(pk)s") % {'pk': administration.pk},
        )
