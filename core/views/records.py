import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..access import can_view_patient
from ..forms import MedicalRecordForm
from ..audit import audit
from ..models import AuditLog, MedicalRecord, Patient

logger = logging.getLogger(__name__)


def _can_manage_record(user, record):
    """The author of a record and any user with clinic-wide patient access can
    manage it. Assigned-doctor scope for other doctors arrives with the visit
    system."""
    if user.has_perm('core.view_patient'):
        return True
    return record.doctor_id == user.id


@login_required
@permission_required('core.add_medicalrecord', raise_exception=True)
def medical_record_create(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk)
    if not can_view_patient(request.user, patient):
        raise PermissionDenied

    if request.method == 'POST':
        form = MedicalRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.patient = patient
            record.doctor = request.user
            record.save()
            audit(action=AuditLog.CREATE, obj=record, description='Medical record created')
            messages.success(request, _("Medical record created."))
            return redirect('patient_detail', pk=patient.pk)
    else:
        form = MedicalRecordForm()

    return render(request, 'core/medical_record_form.html', {
        'form': form,
        'patient': patient,
    })


@login_required
@permission_required('core.change_medicalrecord', raise_exception=True)
def medical_record_edit(request, pk):
    record = get_object_or_404(MedicalRecord.objects.select_related('patient'), pk=pk)
    if not _can_manage_record(request.user, record):
        raise PermissionDenied

    if request.method == 'POST':
        form = MedicalRecordForm(request.POST, instance=record)
        if form.is_valid():
            form.save()
            audit(action=AuditLog.UPDATE, obj=record, description='Medical record updated')
            messages.success(request, _("Medical record updated."))
            return redirect('patient_detail', pk=record.patient_id)
    else:
        form = MedicalRecordForm(instance=record)

    return render(request, 'core/medical_record_form.html', {
        'form': form,
        'patient': record.patient,
        'record': record,
    })


@login_required
@permission_required('core.delete_medicalrecord', raise_exception=True)
@require_POST
def medical_record_delete(request, pk):
    record = get_object_or_404(MedicalRecord.objects.select_related('patient'), pk=pk)
    if not _can_manage_record(request.user, record):
        raise PermissionDenied

    patient_pk = record.patient_id
    record_pk = record.pk
    record.delete()
    audit(action=AuditLog.DELETE, object_type='core.MedicalRecord', object_id=str(record_pk), description='Medical record deleted')
    messages.success(request, _("Medical record deleted."))
    return redirect('patient_detail', pk=patient_pk)
