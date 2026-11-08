from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.constants import FLOOR_CHOICES
from core.models import Service

from .forms import (
    REASON_TO_MOVEMENT,
    ConsumableCreateForm,
    ConsumableEditForm,
    StockAdjustmentForm,
)
from core.audit import audit
from core.models import AuditLog

from .models import Consumable, Consumption, Stock
from .services import StockService

MANAGE = 'inventory.manage_inventory'


@login_required
@permission_required(MANAGE, raise_exception=True)
def stock_list(request):
    consumables = Consumable.objects.prefetch_related('stock_levels__location')

    floor = request.GET.get('floor', '')
    if floor in dict(FLOOR_CHOICES):
        consumables = consumables.filter(stock_levels__location__floor=floor).distinct()

    page_obj = Paginator(consumables, 20).get_page(request.GET.get('page'))

    return render(request, 'inventory/stock_list.html', {
        'page_obj': page_obj,
        'floor_choices': FLOOR_CHOICES,
        'current_floor': floor,
    })


@login_required
@permission_required(MANAGE, raise_exception=True)
def consumable_create(request):
    if request.method == 'POST':
        form = ConsumableCreateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                consumable = form.save()
                service = Service.objects.create(
                    name=consumable.name,
                    category=Service.MEDICINE,
                    price=form.cleaned_data['price'],
                    active=True,
                )
                consumable.service = service
                consumable.save(update_fields=['service'])

                quantity = form.cleaned_data['quantity']
                if quantity > 0:
                    StockService.apply_adjustment(
                        consumable,
                        form.cleaned_data['location'],
                        quantity,
                        Consumption.RESTOCK,
                        _("Initial stock"),
                        request.user,
                    )
            audit(action=AuditLog.CREATE, obj=consumable, description='Consumable created')
            messages.success(request, _("Consumable created."))
            return redirect('inventory:stock_list')
    else:
        form = ConsumableCreateForm()

    return render(request, 'inventory/consumable_form.html', {'form': form})


@login_required
@permission_required(MANAGE, raise_exception=True)
def consumable_edit(request, pk):
    consumable = get_object_or_404(Consumable, pk=pk)

    if request.method == 'POST':
        form = ConsumableEditForm(request.POST, instance=consumable)
        if form.is_valid():
            form.save()
            audit(action=AuditLog.UPDATE, obj=consumable, description='Consumable updated')
            messages.success(request, _("Consumable updated."))
            return redirect('inventory:stock_list')
    else:
        form = ConsumableEditForm(instance=consumable)

    return render(request, 'inventory/consumable_form.html', {
        'form': form,
        'consumable': consumable,
    })


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_POST
def consumable_toggle_active(request, pk):
    consumable = get_object_or_404(Consumable, pk=pk)
    consumable.active = not consumable.active
    consumable.save(update_fields=['active'])

    if consumable.active:
        audit(action=AuditLog.UPDATE, obj=consumable, description='Consumable activated', metadata={'active': True})
        messages.success(request, _("Consumable activated."))
    else:
        audit(action=AuditLog.UPDATE, obj=consumable, description='Consumable deactivated', metadata={'active': False})
        messages.success(request, _("Consumable deactivated."))
    return redirect('inventory:stock_list')


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_POST
def consumable_delete(request, pk):
    consumable = get_object_or_404(Consumable, pk=pk)
    try:
        consumable.delete()
        audit(action=AuditLog.DELETE, object_type='inventory.Consumable', object_id=str(pk), description='Consumable deleted')
        messages.success(request, _("Consumable deleted."))
    except ProtectedError:
        messages.error(
            request,
            _("This item has stock history and cannot be deleted. Deactivate it instead."),
        )
    return redirect('inventory:stock_list')


@login_required
@permission_required(MANAGE, raise_exception=True)
def stock_adjustment(request, pk):
    consumable = get_object_or_404(Consumable, pk=pk)

    if request.method == 'POST':
        form = StockAdjustmentForm(request.POST)
        if form.is_valid():
            reason_code = form.cleaned_data['reason_code']
            reason_label = dict(form.fields['reason_code'].choices)[reason_code]
            notes = form.cleaned_data['notes']
            composed = f"{reason_label}: {notes}" if notes else str(reason_label)

            try:
                StockService.apply_adjustment(
                    consumable,
                    form.cleaned_data['location'],
                    form.cleaned_data['quantity_change'],
                    REASON_TO_MOVEMENT[reason_code],
                    composed,
                    request.user,
                )
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if exc.messages else str(exc))
            else:
                messages.success(request, _("Stock adjusted."))
                return redirect('inventory:stock_list')
    else:
        form = StockAdjustmentForm()

    return render(request, 'inventory/stock_adjustment.html', {
        'consumable': consumable,
        'form': form,
        'stock_rows': Stock.objects.filter(consumable=consumable).select_related('location'),
    })
