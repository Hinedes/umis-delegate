from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.postgres.search import TrigramSimilarity
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..access import can_view_patient, visible_patients
from ..constants import FLOOR_CHOICES
from ..forms import PatientForm
from ..audit import audit
from ..models import AuditLog, Patient
from ..search import patient_search_q
from ..sorting import apply_sorting


@login_required
@permission_required('core.add_patient', raise_exception=True)
def patient_create(request):
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            patient = form.save()
            audit(action=AuditLog.CREATE, obj=patient, description='Patient created')
            if form.duplicate_warning:
                messages.warning(request, form.duplicate_warning)
            messages.success(request, _("Patient created."))
            return redirect('patient_list')
    else:
        form = PatientForm()

    return render(request, 'core/patient_form.html', {'form': form})


@login_required
@permission_required('core.change_patient', raise_exception=True)
def patient_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)

    if request.method == 'POST':
        form = PatientForm(request.POST, instance=patient)
        if form.is_valid():
            form.save()
            if form.duplicate_warning:
                messages.warning(request, form.duplicate_warning)
            audit(action=AuditLog.UPDATE, obj=patient, description='Patient updated')
            messages.success(request, _("Patient updated."))
            return redirect('patient_detail', pk=patient.pk)
    else:
        form = PatientForm(instance=patient)

    return render(request, 'core/patient_form.html', {'form': form, 'patient': patient})


@login_required
@permission_required('core.delete_patient', raise_exception=True)
@require_POST
def patient_delete(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    try:
        patient.delete()
        audit(action=AuditLog.DELETE, object_type='core.Patient', object_id=str(pk), description='Patient deleted')
        messages.success(request, _("Patient deleted."))
    except ProtectedError:
        messages.error(
            request,
            _("This patient has linked records and cannot be deleted."),
        )
        return redirect('patient_detail', pk=patient.pk)
    return redirect('patient_list')


@login_required
def patient_list(request):
    patients = visible_patients(request.user)

    floor = request.GET.get('floor', '')
    if floor in dict(FLOOR_CHOICES):
        patients = patients.filter(floor=floor)

    search = request.GET.get('search', '').strip()
    if search:
        patients = patients.filter(patient_search_q(search)).annotate(
            search_rank=TrigramSimilarity('full_name', search),
        )

    patients, sort_context = apply_sorting(request, patients, {
        'name': 'full_name', 'national_id': 'national_id',
        'phone': 'phone', 'birth_date': 'birth_date',
    }, default_sort='-search_rank' if search else 'full_name')
    paginator = Paginator(patients, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'core/patient_list.html', {
        'page_obj': page_obj,
        'current_floor': floor,
        'floor_choices': FLOOR_CHOICES,
        'patients': page_obj.object_list,
        'active_registry_source': 'patients',
        'is_htmx': request.headers.get('HX-Request') == 'true',
        'search_query': search,
        **sort_context,
    })


@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    if not can_view_patient(request.user, patient):
        raise PermissionDenied

    records = patient.medical_records.select_related('doctor').order_by('-record_datetime')
    return render(request, 'core/patient_detail.html', {
        'patient': patient,
        'records': records,
    })
