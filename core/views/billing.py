from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from django.db import transaction

from ..audit import audit
from ..forms import PaymentForm
from ..models import AuditLog, Payment, Visit
from ..services import process_refund, record_payment


@login_required
@permission_required('core.add_payment', raise_exception=True)
def visit_record_payment(request, pk):
    visit = get_object_or_404(Visit.objects.with_financial_annotations(), pk=pk)

    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            try:
                record_payment(
                    visit,
                    request.user,
                    form.cleaned_data['amount'],
                    form.cleaned_data['method'],
                    form.cleaned_data['notes'],
                )
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if exc.messages else str(exc))
            else:
                messages.success(request, _("Payment recorded and sent for verification."))
                return redirect('visit_detail', pk=visit.pk)
    else:
        form = PaymentForm()

    return render(request, 'core/payment_form.html', {'visit': visit, 'form': form})


@login_required
@permission_required('core.verify_payment', raise_exception=True)
def payment_pending_list(request):
    payments = Payment.objects.pending().with_related().order_by('payment_datetime')
    page_obj = Paginator(payments, 25).get_page(request.GET.get('page'))

    return render(request, 'core/payment_pending_list.html', {'page_obj': page_obj})


@login_required
@permission_required('core.verify_payment', raise_exception=True)
def payment_verify(request, pk):
    payment = get_object_or_404(Payment.objects.with_related(), pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')
        with transaction.atomic():
            # Lock the row so a concurrent verify/reject cannot pass too.
            payment = Payment.objects.select_for_update().get(pk=payment.pk)
            if payment.status != Payment.PENDING:
                messages.error(request, _("Only a pending payment can be verified or rejected."))
            elif action == 'verify':
                payment.mark_verified(request.user)
                audit(
                    action=AuditLog.VERIFY,
                    obj=payment,
                    description='Payment verified',
                    metadata={'amount': str(payment.amount)},
                    critical=True,
                )
                messages.success(request, _("Payment verified."))
            elif action == 'reject':
                payment.mark_rejected(request.user)
                audit(
                    action=AuditLog.REJECT,
                    obj=payment,
                    description='Payment rejected',
                    metadata={'amount': str(payment.amount)},
                    critical=True,
                )
                messages.warning(request, _("Payment rejected."))
            else:
                messages.error(request, _("Unknown action."))
        return redirect('payment_pending_list')

    return render(request, 'core/payment_verify.html', {'payment': payment})


@login_required
@permission_required('core.change_payment', raise_exception=True)
@require_POST
def visit_process_refund(request, pk):
    visit = get_object_or_404(Visit, pk=pk)
    try:
        refund = process_refund(visit, request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else str(exc))
    else:
        messages.success(request, _("Refund of %(amount)s recorded.") % {'amount': refund.amount})
    return redirect('visit_detail', pk=pk)


@login_required
@permission_required('core.view_payment_ledger', raise_exception=True)
def payment_ledger(request):
    payments = Payment.objects.with_related().order_by('-payment_datetime')

    status = request.GET.get('status', '')
    if status in dict(Payment.STATUS_CHOICES):
        payments = payments.filter(status=status)

    page_obj = Paginator(payments, 25).get_page(request.GET.get('page'))

    return render(request, 'core/payment_list.html', {
        'page_obj': page_obj,
        'status_choices': Payment.STATUS_CHOICES,
        'current_status': status,
    })
