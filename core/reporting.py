"""Reporting reads over canonical domain facts.

Reports consume the annotations and queryset helpers the domain systems
already define; they never restate a formula. Vocabulary:

    billed_service_total   active SERVICE line value, visit_datetime clock
    settled_payments       verified PAYMENT inflow, payment_datetime clock
    refunds                verified REFUND outflow, payment_datetime clock
    net_collected          settled_payments - refunds
    outstanding_balance    positive patient balances only, current snapshot
    clinic_expenses_*      manual and automatic costs by expense date
    shift figures          frozen snapshots for closed shifts

Every period is a half-open aware window [start, end), so a timestamp on a
boundary belongs to exactly one period.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce, ExtractWeekDay, Greatest
from django.utils import timezone

from .models import Expense, Payment, PaymentMethod, Shift, Visit

ZERO = Decimal('0')

AGE_BUCKETS = [(0, 17, '0-17'), (18, 34, '18-34'), (35, 59, '35-59'), (60, None, '60+')]

WEEKDAY_LABELS = {
    1: 'Sunday', 2: 'Monday', 3: 'Tuesday', 4: 'Wednesday',
    5: 'Thursday', 6: 'Friday', 7: 'Saturday',
}


def period_bounds(start_date, end_date):
    """A local-date window as aware half-open datetimes [start, end)."""
    if end_date < start_date:
        raise ValueError('end_date must not precede start_date')
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
    return start, end


def visits_in(start, end):
    return Visit.objects.filter(visit_datetime__gte=start, visit_datetime__lt=end)


def _billed_total():
    return Coalesce(Sum('annotated_service_total'), Value(ZERO), output_field=DecimalField())


def visit_summary(start, end):
    return visits_in(start, end).with_financial_annotations().aggregate(
        visits=Count('id'),
        billed_service_total=_billed_total(),
    )


def group_visits(start, end, field):
    """Visit counts and billed value grouped by one visit field.

    One query, no service join: a visit with several lines counts once and
    its billed value comes from the per-visit subquery annotation.
    """
    qs = visits_in(start, end).with_financial_annotations()
    if field == 'assigned_doctor':
        rows = qs.values(
            'assigned_doctor',
            'assigned_doctor__first_name',
            'assigned_doctor__last_name',
            'assigned_doctor__username',
        ).annotate(visits=Count('id'), billed_service_total=_billed_total()).order_by('-visits')
        for row in rows:
            first = row['assigned_doctor__first_name']
            last = row['assigned_doctor__last_name']
            name = ' '.join(part for part in (first, last) if part)
            row['label'] = name or row['assigned_doctor__username'] or '—'
        return list(rows)

    labels = dict(Visit._meta.get_field(field).choices)
    rows = qs.values(field).annotate(
        visits=Count('id'), billed_service_total=_billed_total(),
    ).order_by('-visits')
    for row in rows:
        row['label'] = labels.get(row[field], row[field] or '—')
    return list(rows)


def weekday_rows(start, end):
    rows = visits_in(start, end).with_financial_annotations().annotate(
        weekday=ExtractWeekDay('visit_datetime'),
    ).values('weekday').annotate(
        visits=Count('id'), billed_service_total=_billed_total(),
    ).order_by('weekday')
    for row in rows:
        row['label'] = WEEKDAY_LABELS.get(row['weekday'], str(row['weekday']))
    return list(rows)


def age_rows(start, end, report_date):
    """Exact ages of distinct patients seen in the period, at ``report_date``."""
    patients = list(
        visits_in(start, end)
        .exclude(patient__birth_date__isnull=True)
        .values_list('patient_id', 'patient__birth_date')
        .distinct()
    )
    buckets = [{'label': label, 'count': 0} for _, _, label in AGE_BUCKETS]
    ages = []
    for _, birthday in patients:
        age = report_date.year - birthday.year - (
            (report_date.month, report_date.day) < (birthday.month, birthday.day)
        )
        ages.append(age)
        for low, high, label in AGE_BUCKETS:
            if age >= low and (high is None or age <= high):
                buckets[[b['label'] for b in buckets].index(label)]['count'] += 1
                break
    without_birth_date = (
        visits_in(start, end)
        .filter(patient__birth_date__isnull=True)
        .values('patient').distinct().count()
    )
    return {
        'buckets': buckets,
        'average': round(sum(ages) / len(ages), 1) if ages else None,
        'without_birth_date': without_birth_date,
    }


def payment_rows(start, end):
    """Settled payments and refunds per configured method, zeros included."""
    settled = Payment.objects.settled_payments().filter(
        payment_datetime__gte=start, payment_datetime__lt=end,
    )
    refunds = Payment.objects.settled_refunds().filter(
        payment_datetime__gte=start, payment_datetime__lt=end,
    )

    def by_method(qs):
        return {
            row['method_id']: row
            for row in qs.values('method_id').annotate(total=Sum('amount'), count=Count('id'))
        }

    paid, refunded = by_method(settled), by_method(refunds)
    rows = []
    for method in PaymentMethod.objects.order_by('order', 'name'):
        paid_row = paid.get(method.pk, {})
        refunded_row = refunded.get(method.pk, {})
        rows.append({
            'code': method.code,
            'label': method.name,
            'kind': method.get_kind_display(),
            'payments': paid_row.get('total') or ZERO,
            'payment_count': paid_row.get('count') or 0,
            'refunds': refunded_row.get('total') or ZERO,
            'refund_count': refunded_row.get('count') or 0,
            'net': (paid_row.get('total') or ZERO) - (refunded_row.get('total') or ZERO),
        })

    def total(qs):
        return qs.aggregate(t=Coalesce(Sum('amount'), Value(ZERO), output_field=DecimalField()))['t']

    settled_total = total(settled)
    refund_total = total(refunds)
    return {
        'rows': rows,
        'settled_payments': settled_total,
        'refunds': refund_total,
        'net_collected': settled_total - refund_total,
    }


def outstanding_total():
    """Positive patient balances only; credit balances stay out of the sum."""
    return Visit.objects.with_financial_annotations().aggregate(
        t=Coalesce(
            Sum(Greatest(F('annotated_net_balance'), Value(ZERO), output_field=DecimalField())),
            Value(ZERO),
            output_field=DecimalField(),
        ),
    )['t']


def expense_summary(start, end):
    qs = Expense.objects.filter(date__gte=start, date__lt=end)

    def total(part):
        return part.aggregate(t=Coalesce(Sum('amount'), Value(ZERO), output_field=DecimalField()))['t']

    manual = qs.manual()
    return {
        'manual': total(manual),
        'automatic': total(qs.automatic()),
        'all': total(qs),
        'manual_by_category': list(
            manual.values('category__code', 'category__name')
            .annotate(total=Sum('amount')).order_by('-total')
        ),
    }


def doctor_collected_rows(start, end):
    """Verified money on visits assigned to each doctor, refunds netted."""
    def grouped(qs):
        rows = qs.filter(
            payment_datetime__gte=start, payment_datetime__lt=end,
        ).values(
            'visit__assigned_doctor',
            'visit__assigned_doctor__first_name',
            'visit__assigned_doctor__last_name',
            'visit__assigned_doctor__username',
        ).annotate(total=Sum('amount'))
        return {row['visit__assigned_doctor']: row for row in rows}

    paid = grouped(Payment.objects.settled_payments())
    refunded = grouped(Payment.objects.settled_refunds())
    result = []
    for doctor_id in set(paid) | set(refunded):
        source = paid.get(doctor_id) or refunded[doctor_id]
        first, last = source['visit__assigned_doctor__first_name'], source['visit__assigned_doctor__last_name']
        name = ' '.join(part for part in (first, last) if part)
        result.append({
            'label': name or source['visit__assigned_doctor__username'] or '—',
            'collected': (paid.get(doctor_id, {}).get('total') or ZERO)
            - (refunded.get(doctor_id, {}).get('total') or ZERO),
        })
    result.sort(key=lambda row: row['collected'], reverse=True)
    return result


def shift_rows(start, end):
    """Closed shifts read frozen snapshots; open shifts read live totals."""
    closed = []
    for shift in Shift.objects.filter(
        status=Shift.CLOSED, opened_at__gte=start, opened_at__lt=end,
    ).order_by('opened_at'):
        closed.append({
            'shift': shift,
            'historical': True,
            'totals': {
                'visits': shift.snapshot_visit_count,
                'patients': shift.snapshot_patient_count,
                'payments': shift.snapshot_total_payments,
                'refunds': shift.snapshot_total_refunds,
                'manual_expenses': shift.snapshot_manual_expenses,
                'cash_in': shift.snapshot_cash_in,
                'cash_refund': shift.snapshot_cash_refund,
                'cash_expenses': shift.snapshot_cash_expenses,
                'expected_cash': shift.expected_cash,
                'difference': shift.cash_difference,
            },
        })

    open_rows = []
    for shift in Shift.objects.filter(status=Shift.OPEN).order_by('floor'):
        totals = shift.period_totals()
        open_rows.append({
            'shift': shift,
            'historical': False,
            'totals': {
                'visits': totals['visit_count'],
                'patients': totals['patient_count'],
                'payments': totals['total_payments'],
                'refunds': totals['total_refunds'],
                'manual_expenses': totals['manual_expenses'],
                'cash_in': totals['cash_in'],
                'cash_refund': totals['cash_refund'],
                'cash_expenses': totals['cash_expenses'],
                'expected_cash': shift.expected_cash_total(totals),
                'difference': None,
            },
        })
    return {'closed': closed, 'open': open_rows}
