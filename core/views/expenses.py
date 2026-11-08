from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..forms import ExpenseForm
from ..audit import audit
from ..models import AuditLog, Expense, Shift


@login_required
@permission_required('core.add_expense', raise_exception=True)
def expense_create(request):
    if request.method == 'POST':
        form = ExpenseForm(request.POST)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.created_by = request.user
            expense.save()
            audit(action=AuditLog.CREATE, obj=expense, description='Expense recorded')
            messages.success(request, _("Expense recorded."))
            return redirect('expense_create')
    else:
        form = ExpenseForm()

    return render(request, 'core/expense_form.html', {'form': form})


@login_required
@permission_required('core.delete_expense', raise_exception=True)
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)

    if expense.is_automatic:
        messages.error(
            request,
            _("This expense follows a medicine line. Cancel the line instead."),
        )
        return redirect('expense_create')

    if Shift.objects.filter(
        floor=expense.floor,
        status=Shift.CLOSED,
        opened_at__lte=expense.date,
        closed_at__gte=expense.date,
    ).exists():
        messages.error(
            request,
            _("This expense was counted in a closed shift and cannot be deleted."),
        )
        return redirect('expense_create')

    expense.delete()
    audit(action=AuditLog.DELETE, object_type='core.Expense', object_id=str(pk), description='Expense deleted')
    messages.success(request, _("Expense deleted."))
    return redirect('expense_create')

