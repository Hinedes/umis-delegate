from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from ..constants import FLOOR_CHOICES
from ..forms import ShiftCloseForm, ShiftOpenForm
from ..audit import audit
from ..models import AuditLog, Shift

MANAGE = 'core.manage_shift'


@login_required
@permission_required(MANAGE, raise_exception=True)
def shift_list(request):
    shifts = Shift.objects.select_related('opened_by', 'closed_by')[:50]
    active = {
        code: Shift.objects.filter(floor=code, status=Shift.OPEN).first()
        for code, _label in FLOOR_CHOICES
    }
    return render(request, 'core/shift_list.html', {
        'shifts': shifts,
        'active_shifts': active,
        'floor_choices': FLOOR_CHOICES,
    })


@login_required
@permission_required(MANAGE, raise_exception=True)
def shift_open(request):
    if request.method == 'POST':
        form = ShiftOpenForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    shift = Shift.objects.create(
                        floor=form.cleaned_data['floor'],
                        opening_cash=form.cleaned_data['opening_cash'],
                        opened_by=request.user,
                    )
            except IntegrityError:
                messages.error(request, _("A shift for this floor is already open."))
                return redirect('shift_list')
            else:
                audit(action=AuditLog.OPEN, obj=shift, description='Shift opened', metadata={'floor': shift.floor})
                messages.success(request, _("Shift opened."))
                return redirect('shift_list')
    else:
        form = ShiftOpenForm()

    return render(request, 'core/shift_open.html', {'form': form})


@login_required
@permission_required(MANAGE, raise_exception=True)
def shift_close(request, pk):
    shift = get_object_or_404(Shift, pk=pk, status=Shift.OPEN)

    if request.method == 'POST':
        form = ShiftCloseForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    shift.close(
                        request.user,
                        form.cleaned_data['closing_cash'],
                        form.cleaned_data['notes'],
                    )
                    audit(
                        action=AuditLog.CLOSE,
                        obj=shift,
                        description='Shift closed',
                        metadata={'floor': shift.floor, 'difference': str(shift.cash_difference)},
                        critical=True,
                    )
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if exc.messages else str(exc))
            else:
                messages.success(request, _("Shift closed."))
                return redirect('shift_report', pk=shift.pk)
    else:
        form = ShiftCloseForm()

    totals = shift.period_totals()
    return render(request, 'core/shift_close.html', {
        'shift': shift,
        'form': form,
        'totals': totals,
        'expected_cash': shift.expected_cash_total(totals),
    })


@login_required
@permission_required(MANAGE, raise_exception=True)
def shift_report(request, pk):
    shift = get_object_or_404(Shift, pk=pk)

    if shift.status == Shift.CLOSED:
        totals = {
            'cash_in': shift.snapshot_cash_in,
            'cash_refund': shift.snapshot_cash_refund,
            'cash_expenses': shift.snapshot_cash_expenses,
            'total_payments': shift.snapshot_total_payments,
            'total_refunds': shift.snapshot_total_refunds,
            'manual_expenses': shift.snapshot_manual_expenses,
            'visit_count': shift.snapshot_visit_count,
            'patient_count': shift.snapshot_patient_count,
        }
    else:
        totals = shift.period_totals()

    return render(request, 'core/shift_report.html', {
        'shift': shift,
        'totals': totals,
    })
