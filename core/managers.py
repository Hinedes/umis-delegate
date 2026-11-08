from decimal import Decimal

from django.db import models
from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce


class VisitQuerySet(models.QuerySet):
    def with_line_item_totals(self):
        """Annotate line-item totals without join fan-in.

        Subqueries keep service rows from multiplying when more related tables
        are joined.
        """
        from .models import VisitService

        def line_sum(category, cancelled):
            return (
                VisitService.objects.filter(
                    visit=OuterRef('pk'),
                    service__category=category,
                    is_cancelled=cancelled,
                )
                .values('visit')
                .annotate(total=Sum(F('price_snapshot') * F('quantity')))
                .values('total')
            )

        def total(category, cancelled):
            return Coalesce(
                Subquery(line_sum(category, cancelled), output_field=DecimalField()),
                Value(Decimal('0')),
                output_field=DecimalField(),
            )

        return self.annotate(
            annotated_service_total=total('SERVICE', False),
            annotated_cancelled_total=total('SERVICE', True),
            annotated_medicine_total=total('MEDICINE', False),
        )

    def with_financial_annotations(self):
        """Line-item totals plus settled payment figures.

        These annotations are the canonical money reads; callers consume them
        instead of redefining what is paid.
        """
        from .models import Payment

        def payment_sum(payment_type):
            return (
                Payment.objects.filter(
                    visit=OuterRef('pk'),
                    type=payment_type,
                    status=Payment.VERIFIED,
                )
                .values('visit')
                .annotate(total=Sum('amount'))
                .values('total')
            )

        def subquery(payment_type):
            return Coalesce(
                Subquery(payment_sum(payment_type), output_field=DecimalField()),
                Value(Decimal('0')),
                output_field=DecimalField(),
            )

        return self.with_line_item_totals().annotate(
            annotated_paid_in=subquery(Payment.PAYMENT),
            annotated_refunded=subquery(Payment.REFUND),
        ).annotate(
            annotated_amount_paid=F('annotated_paid_in') - F('annotated_refunded'),
            annotated_net_balance=F('annotated_service_total') - F('annotated_amount_paid'),
        )


class PaymentQuerySet(models.QuerySet):
    def payments(self):
        return self.filter(type='PAYMENT')

    def refunds(self):
        return self.filter(type='REFUND')

    def pending(self):
        return self.filter(status='PENDING')

    def verified(self):
        return self.filter(status='VERIFIED')

    def rejected(self):
        return self.filter(status='REJECTED')

    def recorded(self):
        """Everything still on the books: pending plus verified."""
        return self.exclude(status='REJECTED')

    def settled_payments(self):
        return self.payments().verified()

    def settled_refunds(self):
        return self.refunds().verified()

    def cash(self):
        return self.filter(method__kind='CASH')

    def with_related(self):
        return self.select_related('visit__patient', 'method', 'created_by', 'verified_by')


VisitManager = models.Manager.from_queryset(VisitQuerySet)
PaymentManager = models.Manager.from_queryset(PaymentQuerySet)


class ExpenseQuerySet(models.QuerySet):
    def manual(self):
        """Costs recorded by staff."""
        return self.filter(visit_service__isnull=True)

    def automatic(self):
        """Accounting mirrors of active medicine lines."""
        return self.filter(visit_service__isnull=False)

    def for_period(self, start, end):
        return self.filter(date__range=(start, end))

    def with_related(self):
        return self.select_related(
            'category', 'method', 'created_by', 'visit_service__visit',
        )


ExpenseManager = models.Manager.from_queryset(ExpenseQuerySet)
