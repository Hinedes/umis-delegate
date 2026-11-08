from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..audit import audit
from ..models import AuditLog, Operation, OperationConsumable
from inventory.models import Consumable


@login_required
@permission_required('core.view_operation', raise_exception=True)
def operation_list(request):
    operations = (
        Operation.objects.select_related('patient', 'visit', 'performed_by_user')
        .prefetch_related('consumables__consumable', 'consumables__used_by')
        .order_by('-operation_datetime', '-pk')[:50]
    )
    consumables = Consumable.objects.filter(active=True).order_by('name')
    return render(request, 'core/nurse/operation_list.html', {
        'operations': operations,
        'consumables': consumables,
    })


@login_required
@permission_required('core.add_operationconsumable', raise_exception=True)
@require_POST
def operation_add_consumable(request, pk):
    operation = get_object_or_404(
        Operation.objects.select_related('patient', 'visit'),
        pk=pk,
    )

    try:
        consumable = Consumable.objects.get(pk=request.POST.get('consumable'))
        quantity = int(request.POST.get('quantity'))
    except (Consumable.DoesNotExist, TypeError, ValueError):
        messages.error(request, _("Choose a consumable and a whole quantity."))
        return redirect('operation_list')

    try:
        usage = OperationConsumable.objects.create(
            operation=operation,
            consumable=consumable,
            quantity=quantity,
            used_by=request.user,
            notes=request.POST.get('notes', ''),
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else str(exc))
    else:
        audit(action=AuditLog.CREATE, obj=usage, description='Surgical consumable logged')
        messages.success(request, _("Consumable logged."))
    return redirect('operation_list')
