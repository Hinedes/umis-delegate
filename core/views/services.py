from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..forms import ServiceForm
from ..audit import audit
from ..models import AuditLog, Service
from ..sorting import apply_sorting


@login_required
@permission_required('core.view_service', raise_exception=True)
def service_list(request):
    services = Service.objects.order_by('name')

    category = request.GET.get('category', '')
    if category in dict(Service.CATEGORY_CHOICES):
        services = services.filter(category=category)

    services, sort_context = apply_sorting(request, services, {
        'name': 'name', 'price': 'price', 'active': 'active',
    }, default_sort='name')
    return render(request, 'core/service_list.html', {
        'services': services,
        'current_category': category,
        'category_choices': Service.CATEGORY_CHOICES,
        **sort_context,
    })


@login_required
@permission_required('core.add_service', raise_exception=True)
def service_create(request):
    if request.method == 'POST':
        form = ServiceForm(request.POST)
        if form.is_valid():
            service = form.save()
            audit(action=AuditLog.CREATE, obj=service, description='Service created')
            messages.success(request, _("Service created."))
            return redirect('service_list')
    else:
        form = ServiceForm()

    return render(request, 'core/service_form.html', {'form': form})


@login_required
@permission_required('core.change_service', raise_exception=True)
def service_edit(request, pk):
    service = get_object_or_404(Service, pk=pk)

    if request.method == 'POST':
        form = ServiceForm(request.POST, instance=service)
        if form.is_valid():
            form.save()
            audit(action=AuditLog.UPDATE, obj=service, description='Service updated')
            messages.success(request, _("Service updated."))
            return redirect('service_list')
    else:
        form = ServiceForm(instance=service)

    return render(request, 'core/service_form.html', {'form': form, 'service': service})


@login_required
@permission_required('core.change_service', raise_exception=True)
@require_POST
def service_toggle_active(request, pk):
    service = get_object_or_404(Service, pk=pk)
    service.active = not service.active
    service.save(update_fields=['active'])

    if service.active:
        messages.success(request, _("Service activated."))
    else:
        messages.success(request, _("Service deactivated."))
    audit(action=AuditLog.UPDATE, obj=service, description='Service toggled', metadata={'active': service.active})
    return redirect('service_list')


@login_required
@permission_required('core.delete_service', raise_exception=True)
@require_POST
def service_delete(request, pk):
    service = get_object_or_404(Service, pk=pk)
    try:
        service_pk = service.pk
        service.delete()
        audit(action=AuditLog.DELETE, object_type='core.Service', object_id=str(service_pk), description='Service deleted')
        messages.success(request, _("Service deleted."))
    except ProtectedError:
        messages.error(
            request,
            _("This service is used by visits. Deactivate it instead of deleting."),
        )
    return redirect('service_list')
