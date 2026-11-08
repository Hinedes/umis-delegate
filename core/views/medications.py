from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..access import visible_visits
from ..forms import MedicationOrderForm
from ..audit import audit
from ..models import AuditLog, MedicationAdministration, MedicationOrder, Visit
from ..services import record_administration, void_administration


@login_required
@permission_required('core.view_medicationorder', raise_exception=True)
def medication_list(request):
    orders = (
        MedicationOrder.objects.filter(cancelled_at__isnull=True)
        .select_related('visit__patient', 'consumable', 'prescribed_by')
        .prefetch_related('administrations__administered_by')
        .order_by('-prescribed_at', '-pk')[:50]
    )
    return render(request, 'core/nurse/medication_list.html', {'orders': orders})


@login_required
@permission_required('core.add_medicationorder', raise_exception=True)
def medication_order_create(request):
    visits = (
        visible_visits(request.user)
        .exclude(status__in=Visit.TERMINAL_STATUSES)
        .select_related('patient')
    )

    if request.method == 'POST':
        form = MedicationOrderForm(request.POST, visits=visits)
        if form.is_valid():
            order = MedicationOrder.objects.create(
                visit=form.cleaned_data['visit'],
                consumable=form.cleaned_data['consumable'],
                total_quantity=form.cleaned_data['total_quantity'],
                prescribed_by=request.user,
            )
            audit(action=AuditLog.CREATE, obj=order, description='Prescription created')
            messages.success(request, _("Prescription created."))
            return redirect('medication_list')
    else:
        form = MedicationOrderForm(visits=visits)

    return render(request, 'core/medication_order_form.html', {'form': form})


@login_required
@permission_required('core.add_medicationadministration', raise_exception=True)
@require_POST
def medication_administer(request, pk):
    order = get_object_or_404(MedicationOrder, pk=pk)

    try:
        quantity = int(request.POST.get('quantity'))
    except (TypeError, ValueError):
        messages.error(request, _("Enter a whole quantity."))
        return redirect('medication_list')

    try:
        record_administration(order, request.user, quantity)
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else str(exc))
    else:
        messages.success(request, _("Administration recorded."))
    return redirect('medication_list')


@login_required
@permission_required('core.change_medicationadministration', raise_exception=True)
@require_POST
def medication_void(request, pk):
    administration = get_object_or_404(MedicationAdministration, pk=pk)

    try:
        void_administration(administration, request.user, request.POST.get('reason', ''))
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else str(exc))
    else:
        messages.success(request, _("Administration voided and stock restored."))
    return redirect('medication_list')


@login_required
@permission_required('core.change_medicationorder', raise_exception=True)
@require_POST
def medication_order_cancel(request, pk):
    order = get_object_or_404(MedicationOrder, pk=pk)

    try:
        order.cancel(request.user, request.POST.get('reason', ''))
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else str(exc))
    else:
        audit(action=AuditLog.CANCEL, obj=order, description='Prescription cancelled')
        messages.success(request, _("Prescription cancelled."))
    return redirect('medication_list')
