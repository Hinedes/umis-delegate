from datetime import date

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render
from django.utils.timezone import localdate

from .. import reporting


def _requested_period(request):
    today = localdate()
    month_start = today.replace(day=1)

    def parse(name, fallback):
        raw = request.GET.get(name, '')
        try:
            return date.fromisoformat(raw)
        except (TypeError, ValueError):
            return fallback

    start = parse('start', month_start)
    end = parse('end', today)
    if end < start:
        start, end = month_start, today
    return start, end


@login_required
@permission_required('core.view_visit', raise_exception=True)
def analytics_dashboard(request):
    start_date, end_date = _requested_period(request)
    start, end = reporting.period_bounds(start_date, end_date)

    return render(request, 'core/analytics_dashboard.html', {
        'start_date': start_date,
        'end_date': end_date,
        'summary': reporting.visit_summary(start, end),
        'by_status': reporting.group_visits(start, end, 'status'),
        'by_visit_type': reporting.group_visits(start, end, 'visit_type'),
        'by_floor': reporting.group_visits(start, end, 'floor'),
        'by_referral': reporting.group_visits(start, end, 'referral_source'),
        'by_doctor': reporting.group_visits(start, end, 'assigned_doctor'),
        'by_weekday': reporting.weekday_rows(start, end),
        'age': reporting.age_rows(start, end, end_date),
    })


@login_required
@permission_required('core.view_payment_ledger', raise_exception=True)
def accountant_dashboard(request):
    start_date, end_date = _requested_period(request)
    start, end = reporting.period_bounds(start_date, end_date)

    return render(request, 'core/accountant_dashboard.html', {
        'start_date': start_date,
        'end_date': end_date,
        'money': reporting.payment_rows(start, end),
        'outstanding': reporting.outstanding_total(),
        'expenses': reporting.expense_summary(start, end),
        'shifts': reporting.shift_rows(start, end),
        'by_doctor': reporting.doctor_collected_rows(start, end),
    })
