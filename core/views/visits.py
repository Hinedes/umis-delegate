from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..access import can_change_visit, can_start_visit, can_view_visit, visible_visits
from ..forms import VisitForm, VisitServiceFormSet, assignable_doctors
from ..audit import audit
from ..models import AuditLog, Patient, Service, Visit
from ..services import accept_visit_request
from ..search import patient_search_q
from ..sorting import apply_sorting



def _safe_next(request):
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return None


def _soft_cancel_deleted(request, deleted_lines):
    """Formset deletions become cancellations; the rows stay."""
    for line in deleted_lines:
        if not line.is_cancelled:
            line.is_cancelled = True
            line.cancelled_at = timezone.now()
            line.cancelled_by = request.user
            line.save()
            audit(action=AuditLog.CANCEL, obj=line, description='Visit line cancelled')



@login_required
def visit_list(request):
    visits = visible_visits(request.user).with_financial_annotations().select_related('patient', 'assigned_doctor')

    status = request.GET.get('status', '')
    if status in dict(Visit.STATUS_CHOICES):
        visits = visits.filter(status=status)

    doctor = request.GET.get('doctor', '')
    if doctor.isdigit():
        visits = visits.filter(assigned_doctor_id=doctor)

    date = request.GET.get('date', '')
    try:
        parsed_date = parse_date(date)
    except ValueError:  # well-formed but impossible, e.g. 2026-02-30
        parsed_date = None
    if parsed_date:
        visits = visits.filter(visit_datetime__date=parsed_date)

    search = request.GET.get('search', '').strip()
    if search:
        visits = visits.filter(patient__in=Patient.objects.filter(patient_search_q(search)))

    visits, sort_context = apply_sorting(request, visits, {
        'patient': 'patient__full_name', 'doctor': 'assigned_doctor__username',
        'date': 'visit_datetime',
    }, default_sort='-visit_datetime')
    page_obj = Paginator(visits, 25).get_page(request.GET.get('page'))

    return render(request, 'core/visit_list.html', {
        'page_obj': page_obj,
        'status_choices': Visit.STATUS_CHOICES,
        'doctors': assignable_doctors(),
        'current_status': status,
        'visits': page_obj.object_list,
        'active_registry_source': 'visits',
        'is_htmx': request.headers.get('HX-Request') == 'true',
        **sort_context,
    })


def home(request):
    """Start page: clinicians (and admins, who hold the same capability) land on their day."""
    if request.user.has_perm('core.practice_medicine'):
        return redirect('doctor_dashboard')
    return redirect('visit_list')


@login_required
@permission_required('core.practice_medicine', raise_exception=True)
def doctor_dashboard(request):
    visits = (
        Visit.objects.filter(assigned_doctor=request.user)
        .select_related('patient', 'assigned_doctor')
    )
    pending_visits = visits.filter(status=Visit.REQUESTED).order_by('visit_datetime', 'pk')
    active_visits = visits.filter(status=Visit.IN_PROGRESS).order_by('visit_datetime', 'pk')
    waiting_visits = visits.filter(status=Visit.WAITING).order_by('visit_datetime', 'pk')
    upcoming_visits = visits.filter(status=Visit.PLANNED).order_by('visit_datetime', 'pk')[:3]
    focus_visit = active_visits.first() or waiting_visits.first()
    if focus_visit is None and upcoming_visits:
        focus_visit = upcoming_visits[0]
    return render(request, 'core/doctor_dashboard.html', {
        'pending_visits': pending_visits,
        'active_visits': active_visits,
        'waiting_visits': waiting_visits,
        'upcoming_visits': upcoming_visits,
        'focus_visit': focus_visit,
        'duty_open_hour': settings.BOOKING_OPEN_HOUR,
        'duty_close_hour': settings.BOOKING_CLOSE_HOUR,
    })


def _render_visit_form(request, form, formset, visit=None):
    return render(request, 'core/visit_form.html', {
        'form': form, 'formset': formset,
        'visit_form': form, 'service_formset': formset,
        'visit': visit, 'is_edit': visit is not None,
        'services': Service.objects.filter(active=True, category=Service.SERVICE).order_by('name'),
        'medicines': Service.objects.filter(active=True, category=Service.MEDICINE).order_by('name'),
    })


@login_required
@permission_required('core.add_visit', raise_exception=True)
def visit_create(request):
    if request.method == 'POST':
        form = VisitForm(request.POST)
        formset = VisitServiceFormSet(request.POST, prefix='form')
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    visit = form.save(commit=False)
                    visit.creator = request.user
                    visit.save()
                    formset.instance = visit
                    for line in formset.save(commit=False):
                        is_new = line.pk is None
                        line.save()
                        audit(
                            action=AuditLog.CREATE if is_new else AuditLog.UPDATE,
                            obj=line,
                            description='Visit line saved',
                            metadata={'visit_id': visit.pk},
                        )
            except ValidationError as exc:
                messages.error(request, _("The visit could not be saved: %(error)s") % {'error': exc})
            else:
                audit(action=AuditLog.CREATE, obj=visit, description='Visit created')
                messages.success(request, _("Visit created."))
                return redirect('visit_detail', pk=visit.pk)
    else:
        form = VisitForm(initial={
            'patient': request.GET.get('patient'),
            'visit_datetime': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        })
        formset = VisitServiceFormSet(prefix='form')

    return _render_visit_form(request, form, formset)


@login_required
@permission_required('core.change_visit', raise_exception=True)
def visit_edit(request, pk):
    visit = get_object_or_404(Visit, pk=pk)

    if request.method == 'POST':
        form = VisitForm(request.POST, instance=visit)
        formset = VisitServiceFormSet(request.POST, instance=visit, prefix='form')
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    for line in formset.save(commit=False):
                        is_new = line.pk is None
                        line.save()
                        audit(
                            action=AuditLog.CREATE if is_new else AuditLog.UPDATE,
                            obj=line,
                            description='Visit line saved',
                            metadata={'visit_id': visit.pk},
                        )
                    _soft_cancel_deleted(request, formset.deleted_objects)
            except ValidationError as exc:
                messages.error(request, _("The visit could not be saved: %(error)s") % {'error': exc})
            else:
                audit(action=AuditLog.UPDATE, obj=visit, description='Visit updated')
                messages.success(request, _("Visit updated."))
                return redirect('visit_detail', pk=visit.pk)
    else:
        form = VisitForm(instance=visit)
        formset = VisitServiceFormSet(
            instance=visit,
            queryset=visit.services.filter(is_cancelled=False),
            prefix='form',
        )

    return _render_visit_form(request, form, formset, visit)


@login_required
@permission_required('core.delete_visit', raise_exception=True)
@require_POST
def visit_delete(request, pk):
    visit = get_object_or_404(Visit, pk=pk)
    try:
        visit_pk = visit.pk
        visit.delete()
        audit(action=AuditLog.DELETE, object_type='core.Visit', object_id=str(visit_pk), description='Visit deleted')
        messages.success(request, _("Visit deleted."))
    except ProtectedError:
        messages.error(
            request,
            _("This visit has line items and cannot be deleted. Cancel the lines instead."),
        )
        return redirect('visit_detail', pk=visit.pk)
    return redirect('visit_list')


@login_required
@require_POST
def visit_accept_request(request, pk):
    with transaction.atomic():
        visit = get_object_or_404(Visit.objects.select_for_update(), pk=pk)
        if not can_change_visit(request.user, visit):
            raise PermissionDenied
        if visit.status != Visit.REQUESTED:
            messages.warning(request, _("Only requested visits can be accepted."))
            return redirect('visit_detail', pk=visit.pk)

        accept_visit_request(visit)
        audit(action=AuditLog.UPDATE, obj=visit, description='Visit accepted', metadata={'status': ['REQUESTED', 'PLANNED']})
    messages.success(request, _("Visit accepted."))
    return redirect('visit_detail', pk=visit.pk)


@login_required
def visit_detail(request, pk):
    visit = get_object_or_404(
        Visit.objects.with_financial_annotations().select_related('patient', 'assigned_doctor'),
        pk=pk,
    )
    if not can_view_visit(request.user, visit):
        raise PermissionDenied

    return render(request, 'core/visit_detail.html', {
        'visit': visit,
        'lines': visit.services.select_related('service').order_by('pk'),
        'payments': visit.payments.select_related('method').order_by('-payment_datetime', '-pk'),
        'can_start': can_start_visit(request.user, visit),
        'can_change': can_change_visit(request.user, visit),
    })


@login_required
@require_POST
def visit_status_update(request):
    visit_id = request.POST.get('visit_id', '')
    action = request.POST.get('action')

    if not visit_id.isdigit() or action not in ('start', 'finish', 'cancel'):
        messages.error(request, _("Invalid request."))
        return redirect('visit_list')

    with transaction.atomic():
        visit = get_object_or_404(Visit.objects.select_for_update(), pk=visit_id)
        old_status = visit.status

        if action == 'start':
            allowed = can_start_visit(request.user, visit)
        else:
            allowed = can_change_visit(request.user, visit)
        if not allowed:
            raise PermissionDenied

        if visit.status in Visit.TERMINAL_STATUSES:
            messages.error(request, _("A completed or cancelled visit cannot change status."))
            return redirect(_safe_next(request) or reverse('visit_detail', args=[visit.pk]))

        if action == 'start':
            visit.status = Visit.IN_PROGRESS
        elif action == 'finish':
            visit.status = Visit.COMPLETED
        else:
            visit.status = Visit.CANCELLED
        visit.save(update_fields=['status', 'updated_at'])
        audit(
            action=AuditLog.UPDATE,
            obj=visit,
            description='Visit status changed',
            metadata={'status': [old_status, visit.status]},
        )

    if action == 'start':
        messages.success(request, _("Visit started."))
    elif action == 'finish':
        messages.success(request, _("Visit completed."))
    else:
        messages.success(request, _("Visit cancelled."))

    return redirect(_safe_next(request) or reverse('visit_detail', args=[visit.pk]))
