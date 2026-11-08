"""MVP command implementations.

Every command reuses the form or service the web UI uses. The gateway owns
authorization, approval, transactions, and agent audit; commands own the
business call and return only IDs and status labels.
"""

from datetime import datetime, time, timedelta, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from core import reporting
from core.access import can_change_visit, visible_patients, visible_visits
from core.constants import FLOOR_CHOICES
from core.forms import PatientForm, VisitForm, VisitServiceForm, assignable_doctors
from core.models import Payment, PaymentMethod, Service, Visit, VisitService
from core.search import patient_search_q
from core.services import accept_visit_request, record_payment

from .registry import (
    Command,
    CommandConflict,
    CommandDenied,
    CommandNotFound,
    CommandValidationError,
    RISK_DESTRUCTIVE,
    RISK_NORMAL,
    register,
)


STATUS_VALUES = {value for value, _label in Visit.STATUS_CHOICES}
FLOOR_VALUES = {value for value, _label in FLOOR_CHOICES}
AVAILABILITY_STATUSES = [
    Visit.REQUESTED, Visit.PLANNED, Visit.WAITING, Visit.IN_PROGRESS,
]


# --- argument helpers -------------------------------------------------------

def _require_object(args):
    if not isinstance(args, dict):
        raise CommandValidationError('Arguments must be a JSON object.')
    return args


def _check_keys(args, allowed):
    extra = sorted(set(args) - set(allowed))
    if extra:
        raise CommandValidationError(f"Unknown argument(s): {', '.join(extra)}.")


def _required_int(args, key):
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CommandValidationError(f'{key} must be a positive integer.')
    return value


def _optional_int(args, key, minimum=1):
    if key not in args or args[key] in (None, ''):
        return None
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CommandValidationError(f'{key} must be an integer of at least {minimum}.')
    return value


def _required_text(args, key, max_length=255):
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandValidationError(f'{key} is required.')
    value = value.strip()
    if len(value) > max_length:
        raise CommandValidationError(f'{key} is too long.')
    return value


def _optional_text(args, key, max_length=255):
    value = args.get(key)
    if value in (None, ''):
        return ''
    if not isinstance(value, str):
        raise CommandValidationError(f'{key} must be a string.')
    value = value.strip()
    if len(value) > max_length:
        raise CommandValidationError(f'{key} is too long.')
    return value


def _optional_choice(args, key, choices):
    value = args.get(key)
    if value in (None, ''):
        return ''
    if value not in choices:
        raise CommandValidationError(f'{key} is not a valid choice.')
    return value


def _required_datetime(args, key):
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandValidationError(f'{key} is required (ISO 8601).')
    parsed = parse_datetime(value.strip())
    if parsed is None:
        raise CommandValidationError(f'{key} must be an ISO 8601 datetime.')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _optional_date(args, key):
    value = args.get(key)
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise CommandValidationError(f'{key} must be a date string (YYYY-MM-DD).')
    parsed = parse_date(value)
    if parsed is None:
        raise CommandValidationError(f'{key} must be a date string (YYYY-MM-DD).')
    return parsed


def _required_date(args, key):
    value = _optional_date(args, key)
    if value is None:
        raise CommandValidationError(f'{key} is required (YYYY-MM-DD).')
    return value


def _decimal_amount(args, key='amount'):
    value = args.get(key)
    if value in (None, ''):
        raise CommandValidationError(f'{key} is required.')
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CommandValidationError(f'{key} must be a number.')
    if not amount.is_finite():
        raise CommandValidationError(f'{key} must be a finite number.')
    if amount <= 0:
        raise CommandValidationError(f'{key} must be greater than zero.')
    if amount.as_tuple().exponent < -2:
        raise CommandValidationError(f'{key} may have at most two decimal places.')
    if len(amount.as_tuple().digits) > 10:
        raise CommandValidationError(f'{key} is too large.')
    return amount


def _form_errors(form):
    parts = []
    for field, errors in form.errors.items():
        label = field if field != '__all__' else 'form'
        parts.append(f"{label}: {' '.join(str(error) for error in errors)}")
    return '; '.join(parts) or 'The form rejected the arguments.'


def version_of(obj):
    value = getattr(obj, 'updated_at', None)
    if not value:
        return ''
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value.astimezone(datetime_timezone.utc).isoformat().replace('+00:00', 'Z')


def _iso(value):
    if value is None:
        return ''
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value).isoformat()


def _scope(*, obj=None, status='', affected=1, ids=None):
    return {
        'object': obj,
        'status': status,
        'version': version_of(obj) if obj is not None else '',
        'affected': affected,
        'ids': ids or {},
    }


def _nothing():
    return _scope(obj=None, status='', affected=0, ids={})


def _visit_summary(visit, include_payments=False):
    doctor = visit.assigned_doctor
    doctor_name = ''
    if doctor is not None:
        doctor_name = doctor.get_full_name() or doctor.get_username()
    data = {
        'visit_id': visit.pk,
        'visit_datetime': _iso(visit.visit_datetime),
        'status': visit.status,
        'visit_type': visit.visit_type,
        'assigned_doctor_id': visit.assigned_doctor_id,
        'assigned_doctor': doctor_name,
        'patient_id': visit.patient_id,
        'patient_name': visit.patient.full_name,
        'updated_at': version_of(visit),
    }
    if include_payments:
        data['billed_total'] = str(visit.total_cost)
        data['outstanding_balance'] = str(visit.amount_due)
        data['payments'] = [
            {
                'payment_id': payment.pk,
                'amount': str(payment.amount),
                'method': payment.method.code,
                'status': payment.status,
            }
            for payment in visit.payments.all()
        ]
    return data



# --- queries ----------------------------------------------------------------

def _validate_patient_search(args):
    args = _require_object(args)
    _check_keys(args, {'query'})
    return {'query': _required_text(args, 'query', 100)}


def _authorize_patient_search(user, args):
    return _nothing()


def _preview_patient_search(user, args):
    return {'summary': 'Search patients in your scope.'}


def _execute_patient_search(user, args, run, step):
    patients = list(
        visible_patients(user)
        .filter(patient_search_q(args['query']))
        .order_by('full_name', 'pk')[:21]
    )
    return {
        'count': len(patients[:20]),
        'truncated': len(patients) > 20,
        'patients': [
            {'id': patient.pk, 'floor': patient.floor}
            for patient in patients[:20]
        ],
    }


def _validate_visit_get(args):
    args = _require_object(args)
    _check_keys(args, {'visit_id', 'patient_id', 'filters'})
    forms = [
        key for key in ('visit_id', 'patient_id', 'filters')
        if args.get(key) not in (None, '')
    ]
    if len(forms) != 1:
        raise CommandValidationError(
            'Provide exactly one of visit_id, patient_id, or filters.'
        )
    if 'visit_id' in forms:
        return {'visit_id': _required_int(args, 'visit_id')}
    if 'patient_id' in forms:
        return {'patient_id': _required_int(args, 'patient_id')}

    filters = args.get('filters')
    if not isinstance(filters, dict):
        raise CommandValidationError('filters must be an object.')
    _check_keys(filters, {'date', 'status', 'patient_id'})
    normalized = {}
    if filters.get('date') not in (None, ''):
        normalized['date'] = _required_date(filters, 'date').isoformat()
    if filters.get('status') not in (None, ''):
        normalized['status'] = _optional_choice(filters, 'status', STATUS_VALUES)
    if filters.get('patient_id') not in (None, ''):
        normalized['patient_id'] = _required_int(filters, 'patient_id')
    if not normalized:
        raise CommandValidationError(
            'filters needs date, status, and/or patient_id.'
        )
    return {'filters': normalized}


def _authorize_visit_get(user, args):
    if 'visit_id' in args:
        visit = visible_visits(user).filter(pk=args['visit_id']).first()
        if visit is None:
            raise CommandNotFound('Visit not found in your scope.')
        return _scope(obj=visit, status=visit.status, affected=0,
                      ids={'visit_id': visit.pk})
    return _nothing()


def _preview_visit_get(user, args):
    return {'summary': 'Read visits in your scope.'}


def _execute_visit_get(user, args, run, step):
    include_payments = user.has_perm('core.view_payment')
    qs = visible_visits(user)
    if include_payments:
        qs = qs.with_financial_annotations().prefetch_related(
            Prefetch(
                'payments',
                queryset=Payment.objects.select_related('method').order_by('pk'),
            )
        )
    qs = qs.select_related('patient', 'assigned_doctor')
    filters = args.get('filters') or {}
    patient_id = args.get('patient_id', filters.get('patient_id'))
    if (
        patient_id is not None
        and not visible_patients(user).filter(pk=patient_id).exists()
    ):
        return {'visits': []}

    if 'visit_id' in args:
        qs = qs.filter(pk=args['visit_id'])
    else:
        if 'date' in filters:
            qs = qs.filter(visit_datetime__date=_required_date(filters, 'date'))
        if 'status' in filters:
            qs = qs.filter(status=filters['status'])
        if 'patient_id' in filters:
            qs = qs.filter(patient_id=filters['patient_id'])
        elif 'patient_id' in args:
            qs = qs.filter(patient_id=args['patient_id'])
    visits = list(qs.order_by('visit_datetime', 'pk')[:20])
    return {
        'visits': [
            _visit_summary(visit, include_payments=include_payments)
            for visit in visits
        ],
    }


def _validate_availability(args):
    args = _require_object(args)
    _check_keys(args, {'date', 'doctor_id'})
    return {
        'date': _required_date(args, 'date').isoformat(),
        'doctor_id': _optional_int(args, 'doctor_id'),
    }


def _authorize_availability(user, args):
    doctor_id = args.get('doctor_id')
    if not user.has_perm('core.view_visit'):
        if doctor_id is not None and doctor_id != user.pk:
            raise CommandDenied('You can only read your own availability.')
    return _nothing()


def _preview_availability(user, args):
    return {'summary': 'Read clinic-window availability.'}


def _execute_availability(user, args, run, step):
    day = _required_date(args, 'date')
    doctor_id = args.get('doctor_id')
    if not user.has_perm('core.view_visit') and doctor_id is None:
        doctor_id = user.pk

    tz = timezone.get_current_timezone()
    open_hour = int(getattr(settings, 'BOOKING_OPEN_HOUR', 9))
    close_hour = int(getattr(settings, 'BOOKING_CLOSE_HOUR', 18))
    slot_minutes = int(getattr(settings, 'BOOKING_SLOT_MINUTES', 30))

    window_start = timezone.make_aware(datetime.combine(day, time(open_hour)), tz)
    window_end = timezone.make_aware(datetime.combine(day, time(close_hour)), tz)
    slot_delta = timedelta(minutes=slot_minutes)

    day_start = timezone.make_aware(datetime.combine(day, time.min), tz)
    day_end = day_start + timedelta(days=1)

    scoped = visible_visits(user).filter(status__in=AVAILABILITY_STATUSES)
    if doctor_id is not None:
        scoped = scoped.filter(assigned_doctor_id=doctor_id)

    day_visits = scoped.filter(
        visit_datetime__gte=day_start, visit_datetime__lt=day_end,
    )
    counts = {status: 0 for status in AVAILABILITY_STATUSES}
    for row in day_visits.values('status').annotate(total=Count('id')):
        if row['status'] in counts:
            counts[row['status']] = row['total']

    overlapping = list(
        scoped.filter(
            visit_datetime__gte=window_start - slot_delta,
            visit_datetime__lt=window_end,
        ).order_by('visit_datetime')
    )

    slots = []
    cursor = window_start
    while cursor < window_end:
        slot_end = cursor + slot_delta
        busy = any(
            visit.visit_datetime < slot_end
            and visit.visit_datetime + slot_delta > cursor
            for visit in overlapping
        )
        slots.append({
            'start': _iso(cursor),
            'end': _iso(slot_end),
            'available': not busy,
        })
        cursor = slot_end

    return {
        'label': 'clinic-window availability',
        'date': day.isoformat(),
        'doctor_id': doctor_id,
        'open_hour': open_hour,
        'close_hour': close_hour,
        'slot_minutes': slot_minutes,
        'slots': slots,
        'counts': counts,
    }


def _validate_finance_period(args):
    args = _require_object(args)
    _check_keys(args, {'start_date', 'end_date'})
    start = _required_date(args, 'start_date')
    end = _required_date(args, 'end_date')
    if end < start:
        raise CommandValidationError('end_date must not precede start_date.')
    if (end - start).days > 366:
        raise CommandValidationError('The period is longer than one year.')
    return {'start_date': start.isoformat(), 'end_date': end.isoformat()}


def _authorize_finance_period(user, args):
    return _nothing()


def _preview_finance_period(user, args):
    return {'summary': 'Read settled money totals for a period.'}


def _execute_finance_period(user, args, run, step):
    start, end = reporting.period_bounds(
        _required_date(args, 'start_date'), _required_date(args, 'end_date'),
    )
    totals = reporting.payment_rows(start, end)
    return {
        'start_date': args['start_date'],
        'end_date': args['end_date'],
        'settled_payments': str(totals['settled_payments']),
        'refunds': str(totals['refunds']),
        'net_collected': str(totals['net_collected']),
    }


# --- mutations --------------------------------------------------------------

def _validate_patient_create(args):
    args = _require_object(args)
    _check_keys(args, {'full_name', 'phone', 'national_id', 'birth_date', 'floor', 'notes'})
    data = {
        'full_name': _required_text(args, 'full_name', 255),
        'phone': _required_text(args, 'phone', 32),
        'national_id': _optional_text(args, 'national_id', 20),
        'floor': _optional_choice(args, 'floor', FLOOR_VALUES),
        'notes': _optional_text(args, 'notes', 5000),
    }
    birth_date = _optional_date(args, 'birth_date')
    data['birth_date'] = birth_date.isoformat() if birth_date else ''
    if not data['floor']:
        data['floor'] = FLOOR_CHOICES[0][0]
    return data


def _authorize_patient_create(user, args):
    return _scope(obj=None, affected=1)


def _preview_patient_create(user, args):
    return {
        'summary': 'Create a patient card.',
        'changes': {'create': 'patient'},
        'floor': args['floor'],
    }


def _execute_patient_create(user, args, run, step):
    form = PatientForm(data=args)
    if not form.is_valid():
        raise CommandValidationError(_form_errors(form))
    patient = form.save()
    return {
        'patient_id': patient.pk,
        'duplicate_warning': bool(form.duplicate_warning),
    }


def _validate_visit_request(args):
    args = _require_object(args)
    _check_keys(args, {
        'patient_id', 'visit_datetime', 'assigned_doctor_id', 'visit_type',
        'floor', 'referral_source', 'comment',
    })
    visit_datetime = _required_datetime(args, 'visit_datetime')
    data = {
        'patient_id': _required_int(args, 'patient_id'),
        'assigned_doctor_id': _required_int(args, 'assigned_doctor_id'),
        'visit_datetime': visit_datetime.isoformat(),
        'visit_type': _optional_choice(args, 'visit_type', {value for value, _ in Visit.VISIT_TYPES}),
        'floor': _optional_choice(args, 'floor', FLOOR_VALUES),
        'referral_source': _optional_choice(
            args, 'referral_source', {value for value, _ in Visit.REFERRAL_CHOICES},
        ),
        'comment': _optional_text(args, 'comment', 2000),
    }
    if not data['visit_type']:
        data['visit_type'] = Visit.CONSULTATION
    if not data['floor']:
        data['floor'] = FLOOR_CHOICES[0][0]
    return data


def _patient_in_scope(user, patient_id):
    patient = visible_patients(user).filter(pk=patient_id).first()
    if patient is None:
        raise CommandDenied('Patient is not in your scope.')
    return patient


def _doctor_for_assignment(doctor_id):
    doctor = assignable_doctors().filter(pk=doctor_id).first()
    if doctor is None:
        raise CommandValidationError('assigned_doctor_id is not an assignable doctor.')
    return doctor


def _authorize_visit_request(user, args):
    patient = _patient_in_scope(user, args['patient_id'])
    _doctor_for_assignment(args['assigned_doctor_id'])
    return _scope(
        obj=patient,
        affected=1,
        ids={
            'patient_id': patient.pk,
            'assigned_doctor_id': args['assigned_doctor_id'],
        },
    )


def _preview_visit_request(user, args):
    return {
        'summary': 'Request a visit for an assignable doctor.',
        'changes': {'create': 'visit', 'status': Visit.REQUESTED},
        'patient_id': args['patient_id'],
        'assigned_doctor_id': args['assigned_doctor_id'],
        'visit_datetime': args['visit_datetime'],
    }


def _execute_visit_request(user, args, run, step):
    patient = _patient_in_scope(user, args['patient_id'])
    _doctor_for_assignment(args['assigned_doctor_id'])
    visit_datetime = _required_datetime(args, 'visit_datetime')
    form = VisitForm(data={
        'patient': patient.pk,
        'visit_datetime': timezone.localtime(visit_datetime).strftime('%Y-%m-%dT%H:%M'),
        'visit_type': args['visit_type'],
        'floor': args['floor'],
        'assigned_doctor': args['assigned_doctor_id'],
        'referral_source': args['referral_source'],
        'comment': args['comment'],
    })
    form.fields['patient'].queryset = visible_patients(user)
    if not form.is_valid():
        raise CommandValidationError(_form_errors(form))
    visit = form.save(commit=False)
    visit.creator = user
    visit.status = Visit.REQUESTED
    visit.save()
    return {
        'visit_id': visit.pk,
        'status': visit.status,
        'patient_id': visit.patient_id,
        'assigned_doctor_id': visit.assigned_doctor_id,
        'expected_version': version_of(visit),
    }


def _validate_visit_add_service(args):
    args = _require_object(args)
    _check_keys(args, {'visit_id', 'service_id', 'quantity'})
    return {
        'visit_id': _required_int(args, 'visit_id'),
        'service_id': _required_int(args, 'service_id'),
        'quantity': _optional_int(args, 'quantity', 1) or 1,
    }


def _scoped_visit(user, visit_id):
    visit = visible_visits(user).filter(pk=visit_id).first()
    if visit is None:
        raise CommandNotFound('Visit not found in your scope.')
    return visit


def _authorize_visit_add_service(user, args):
    visit = _scoped_visit(user, args['visit_id'])
    if visit.status not in (Visit.REQUESTED, Visit.PLANNED):
        raise CommandConflict('Services can only be added to a requested or planned visit.')
    service = Service.objects.filter(pk=args['service_id']).first()
    if service is None:
        raise CommandValidationError('service_id does not exist.')
    return _scope(
        obj=visit, status=visit.status, affected=1,
        ids={'visit_id': visit.pk, 'service_id': service.pk},
    )


def _preview_visit_add_service(user, args):
    return {
        'summary': 'Add a service line to a visit.',
        'changes': {'create': 'visit_service'},
        'visit_id': args['visit_id'],
        'service_id': args['service_id'],
        'quantity': args['quantity'],
    }


def _execute_visit_add_service(user, args, run, step):
    with transaction.atomic():
        visit = (
            visible_visits(user).select_for_update()
            .filter(pk=args['visit_id']).first()
        )
        if visit is None:
            raise CommandNotFound('Visit not found in your scope.')
        if visit.status not in (Visit.REQUESTED, Visit.PLANNED):
            raise CommandConflict(
                'Services can only be added to a requested or planned visit.'
            )
        form = VisitServiceForm(
            data={'service': args['service_id'], 'quantity': args['quantity']},
            instance=VisitService(visit=visit),
        )
        if not form.is_valid():
            raise CommandValidationError(_form_errors(form))
        try:
            line = form.save()
        except ValidationError as exc:
            raise CommandValidationError('; '.join(exc.messages))
        return {
            'visit_id': visit.pk,
            'visit_service_id': line.pk,
            'service_id': line.service_id,
            'quantity': line.quantity,
            'status': visit.status,
        }


def _validate_visit_confirm(args):
    args = _require_object(args)
    _check_keys(args, {'visit_id', 'expected_version'})
    return {'visit_id': _required_int(args, 'visit_id')}


def _authorize_visit_confirm(user, args):
    visit = Visit.objects.filter(pk=args['visit_id']).first()
    if visit is None:
        raise CommandNotFound('Visit not found.')
    if not can_change_visit(user, visit):
        raise CommandDenied('Only the assigned doctor or an authorized admin can accept this visit.')
    if visit.status != Visit.REQUESTED:
        raise CommandConflict('Only a requested visit can be accepted.')
    return _scope(obj=visit, status=visit.status, affected=1, ids={'visit_id': visit.pk})


def _preview_visit_confirm(user, args):
    return {
        'summary': 'Accept a requested visit.',
        'changes': {'status': [Visit.REQUESTED, Visit.PLANNED]},
    }


def _execute_visit_confirm(user, args, run, step):
    visit = Visit.objects.select_for_update().get(pk=args['visit_id'])
    if not can_change_visit(user, visit):
        raise CommandDenied('Only the assigned doctor or an authorized admin can accept this visit.')
    try:
        accept_visit_request(visit)
    except ValidationError as exc:
        raise CommandConflict('; '.join(exc.messages))
    return {
        'visit_id': visit.pk,
        'status': visit.status,
        'expected_version': version_of(visit),
    }


def _validate_visit_cancel(args):
    args = _require_object(args)
    _check_keys(args, {'visit_id', 'expected_version', 'reason'})
    return {
        'visit_id': _required_int(args, 'visit_id'),
        'reason': _optional_text(args, 'reason', 255),
    }


def _authorize_visit_cancel(user, args):
    visit = Visit.objects.filter(pk=args['visit_id']).first()
    if visit is None:
        raise CommandNotFound('Visit not found.')
    if not can_change_visit(user, visit):
        raise CommandDenied('Only the assigned doctor or an authorized admin can cancel this visit.')
    if visit.status not in (Visit.REQUESTED, Visit.PLANNED):
        raise CommandConflict('Only a requested or planned visit can be cancelled.')
    return _scope(obj=visit, status=visit.status, affected=1, ids={'visit_id': visit.pk})


def _preview_visit_cancel(user, args):
    return {
        'summary': 'Cancel a visit. This is a destructive transition.',
        'changes': {'status': 'REQUESTED|PLANNED -> CANCELLED'},
        'reason': args.get('reason', ''),
    }


def _execute_visit_cancel(user, args, run, step):
    visit = Visit.objects.select_for_update().get(pk=args['visit_id'])
    if not can_change_visit(user, visit):
        raise CommandDenied('Only the assigned doctor or an authorized admin can cancel this visit.')
    if visit.status not in (Visit.REQUESTED, Visit.PLANNED):
        raise CommandConflict('Only a requested or planned visit can be cancelled.')
    visit.status = Visit.CANCELLED
    visit.save(update_fields=['status', 'updated_at'])
    return {
        'visit_id': visit.pk,
        'status': visit.status,
        'expected_version': version_of(visit),
    }


def _resolve_method(value):
    method = PaymentMethod.objects.filter(active=True).filter(
        Q(code__iexact=value) | Q(name__iexact=value)
    ).order_by('order', 'pk').first()
    if method is None:
        raise CommandValidationError('Payment method is not configured or inactive.')
    return method


def _validate_payment_record(args):
    args = _require_object(args)
    _check_keys(args, {'visit_id', 'amount', 'method', 'notes', 'expected_version'})
    return {
        'visit_id': _required_int(args, 'visit_id'),
        'amount': str(_decimal_amount(args)),
        'method': _required_text(args, 'method', 100),
        'notes': _optional_text(args, 'notes', 2000),
    }


def _authorize_payment_record(user, args):
    visit = visible_visits(user).filter(pk=args['visit_id']).first()
    if visit is None:
        raise CommandNotFound('Visit not found in your scope.')
    if visit.status in Visit.TERMINAL_STATUSES:
        raise CommandConflict('A closed visit cannot accept a payment.')
    if not visit.services.filter(
        is_cancelled=False, service__category=Service.SERVICE,
    ).exists():
        raise CommandConflict('Add service lines before recording a payment.')
    method = _resolve_method(args['method'])
    return _scope(
        obj=visit, status=visit.status, affected=1,
        ids={'visit_id': visit.pk, 'method_id': method.pk},
    )


def _preview_payment_record(user, args):
    return {
        'summary': 'Record a pending payment.',
        'changes': {'create': 'payment', 'status': Payment.PENDING},
        'visit_id': args['visit_id'],
        'amount': str(args['amount']),
        'method': args['method'],
    }


def _execute_payment_record(user, args, run, step):
    with transaction.atomic():
        visit = (
            visible_visits(user).select_for_update()
            .filter(pk=args['visit_id']).first()
        )
        if visit is None:
            raise CommandNotFound('Visit not found in your scope.')
        if visit.status in Visit.TERMINAL_STATUSES:
            raise CommandConflict('A closed visit cannot accept a payment.')
        if not visit.services.filter(
            is_cancelled=False, service__category=Service.SERVICE,
        ).exists():
            raise CommandConflict('Add service lines before recording a payment.')
        method = _resolve_method(args['method'])
        try:
            payment = record_payment(
                visit, user, Decimal(str(args['amount'])), method, args['notes'],
            )
        except ValidationError as exc:
            raise CommandValidationError('; '.join(exc.messages))
        return {
            'payment_id': payment.pk,
            'visit_id': visit.pk,
            'status': payment.status,
            'amount': str(payment.amount),
            'method': method.code,
        }


def _validate_payment_verify(args):
    args = _require_object(args)
    _check_keys(args, {'payment_id'})
    return {'payment_id': _required_int(args, 'payment_id')}


def _authorize_payment_verify(user, args):
    payment = Payment.objects.select_related('visit').filter(pk=args['payment_id']).first()
    if payment is None:
        raise CommandNotFound('Payment not found.')
    if not visible_visits(user).filter(pk=payment.visit_id).exists():
        raise CommandDenied('Payment is outside your visit scope.')
    if payment.status != Payment.PENDING:
        raise CommandConflict('Only a pending payment can be verified.')
    return _scope(
        obj=payment, status=payment.status, affected=1,
        ids={'payment_id': payment.pk, 'visit_id': payment.visit_id},
    )


def _preview_payment_verify(user, args):
    return {
        'summary': 'Verify a pending payment. This moves money.',
        'changes': {'payment_status': [Payment.PENDING, Payment.VERIFIED]},
    }


def _execute_payment_verify(user, args, run, step):
    payment = Payment.objects.select_for_update().get(pk=args['payment_id'])
    if not visible_visits(user).filter(pk=payment.visit_id).exists():
        raise CommandDenied('Payment is outside your visit scope.')
    try:
        payment.mark_verified(user)
    except ValidationError as exc:
        raise CommandConflict('; '.join(exc.messages))
    return {
        'payment_id': payment.pk,
        'visit_id': payment.visit_id,
        'status': payment.status,
    }


# --- registrations ----------------------------------------------------------

register(Command(
    name='query.patient.search',
    description='Search patients visible to you. Returns IDs and floors only; the goal already has the name.',
    parameters={
        'type': 'object',
        'properties': {
            'query': {'type': 'string', 'description': 'Name, phone fragment, or national ID.'},
        },
        'required': ['query'],
    },
    permission='core.view_patient',
    risk=RISK_NORMAL,
    validator=_validate_patient_search,
    authorize=_authorize_patient_search,
    preview=_preview_patient_search,
    execute=_execute_patient_search,
))

register(Command(
    name='query.visit.get',
    description=(
        'Read visits by visit_id, patient_id, or filters {date, status, '
        'patient_id}. Scope decides which visits are visible. Returns at most '
        '20 visits.'
    ),
    parameters={
        'type': 'object',
        'properties': {
            'visit_id': {'type': 'integer'},
            'patient_id': {'type': 'integer'},
            'filters': {
                'type': 'object',
                'properties': {
                    'date': {'type': 'string', 'description': 'YYYY-MM-DD'},
                    'status': {'type': 'string', 'enum': sorted(STATUS_VALUES)},
                    'patient_id': {'type': 'integer'},
                },
                'additionalProperties': False,
            },
        },
        'oneOf': [
            {'required': ['visit_id']},
            {'required': ['patient_id']},
            {'required': ['filters']},
        ],
        'additionalProperties': False,
    },
    permission=None,
    risk=RISK_NORMAL,
    validator=_validate_visit_get,
    authorize=_authorize_visit_get,
    preview=_preview_visit_get,
    execute=_execute_visit_get,
))

register(Command(
    name='query.visit.availability',
    description='Read clinic-window appointment availability and status counts for a date.',
    parameters={
        'type': 'object',
        'properties': {
            'date': {'type': 'string', 'description': 'YYYY-MM-DD'},
            'doctor_id': {'type': 'integer'},
        },
        'required': ['date'],
    },
    permission='core.view_visit',
    object_permission=True,
    risk=RISK_NORMAL,
    validator=_validate_availability,
    authorize=_authorize_availability,
    preview=_preview_availability,
    execute=_execute_availability,
))

register(Command(
    name='query.finance.period',
    description='Read settled payments, refunds, and net collected for a date range. Aggregate totals only.',
    parameters={
        'type': 'object',
        'properties': {
            'start_date': {'type': 'string', 'description': 'YYYY-MM-DD'},
            'end_date': {'type': 'string', 'description': 'YYYY-MM-DD'},
        },
        'required': ['start_date', 'end_date'],
    },
    permission='core.view_payment_ledger',
    risk=RISK_NORMAL,
    validator=_validate_finance_period,
    authorize=_authorize_finance_period,
    preview=_preview_finance_period,
    execute=_execute_finance_period,
))

register(Command(
    name='mutate.patient.create',
    description='Create a patient card through the existing patient form.',
    parameters={
        'type': 'object',
        'properties': {
            'full_name': {'type': 'string'},
            'phone': {'type': 'string'},
            'national_id': {'type': 'string'},
            'birth_date': {'type': 'string', 'description': 'YYYY-MM-DD'},
            'floor': {'type': 'string', 'enum': sorted(FLOOR_VALUES)},
            'notes': {'type': 'string'},
        },
        'required': ['full_name', 'phone'],
    },
    permission='core.add_patient',
    risk=RISK_NORMAL,
    validator=_validate_patient_create,
    authorize=_authorize_patient_create,
    preview=_preview_patient_create,
    execute=_execute_patient_create,
))

register(Command(
    name='mutate.visit.request',
    description='Request a visit for a patient and an assignable doctor. Creates REQUESTED, not PLANNED.',
    parameters={
        'type': 'object',
        'properties': {
            'patient_id': {'type': 'integer'},
            'visit_datetime': {'type': 'string', 'description': 'ISO 8601 datetime.'},
            'assigned_doctor_id': {'type': 'integer'},
            'visit_type': {
                'type': 'string',
                'enum': sorted(value for value, _label in Visit.VISIT_TYPES),
            },
            'floor': {'type': 'string', 'enum': sorted(FLOOR_VALUES)},
            'referral_source': {
                'type': 'string',
                'enum': sorted(value for value, _label in Visit.REFERRAL_CHOICES),
            },
            'comment': {'type': 'string'},
        },
        'required': ['patient_id', 'visit_datetime', 'assigned_doctor_id'],
    },
    permission='core.add_visit',
    risk=RISK_NORMAL,
    validator=_validate_visit_request,
    authorize=_authorize_visit_request,
    preview=_preview_visit_request,
    execute=_execute_visit_request,
))

register(Command(
    name='mutate.visit.add_service',
    description='Add a priced service line to a requested or planned visit.',
    parameters={
        'type': 'object',
        'properties': {
            'visit_id': {'type': 'integer'},
            'service_id': {'type': 'integer'},
            'quantity': {'type': 'integer', 'minimum': 1},
        },
        'required': ['visit_id', 'service_id'],
    },
    permission='core.add_visitservice',
    risk=RISK_NORMAL,
    source_states=(Visit.REQUESTED, Visit.PLANNED),
    validator=_validate_visit_add_service,
    authorize=_authorize_visit_add_service,
    preview=_preview_visit_add_service,
    execute=_execute_visit_add_service,
))

register(Command(
    name='mutate.visit.confirm',
    description='Accept a requested visit (REQUESTED -> PLANNED). Assigned doctor or authorized admin only.',
    parameters={
        'type': 'object',
        'properties': {
            'visit_id': {'type': 'integer'},
            'expected_version': {'type': 'string'},
        },
        'required': ['visit_id'],
    },
    permission=None,
    risk=RISK_NORMAL,
    source_states=(Visit.REQUESTED,),
    validator=_validate_visit_confirm,
    authorize=_authorize_visit_confirm,
    preview=_preview_visit_confirm,
    execute=_execute_visit_confirm,
))

register(Command(
    name='mutate.visit.cancel',
    description='Cancel a requested or planned visit. Destructive: pauses for explicit approval.',
    parameters={
        'type': 'object',
        'properties': {
            'visit_id': {'type': 'integer'},
            'expected_version': {'type': 'string'},
            'reason': {'type': 'string'},
        },
        'required': ['visit_id'],
    },
    permission=None,
    risk=RISK_DESTRUCTIVE,
    source_states=(Visit.REQUESTED, Visit.PLANNED),
    validator=_validate_visit_cancel,
    authorize=_authorize_visit_cancel,
    preview=_preview_visit_cancel,
    execute=_execute_visit_cancel,
))

register(Command(
    name='mutate.payment.record',
    description='Record a pending payment. Does not verify it.',
    parameters={
        'type': 'object',
        'properties': {
            'visit_id': {'type': 'integer'},
            'amount': {'type': ['number', 'string']},
            'method': {'type': 'string', 'description': 'Payment method code or label, e.g. card.'},
            'notes': {'type': 'string'},
            'expected_version': {'type': 'string'},
        },
        'required': ['visit_id', 'amount', 'method'],
    },
    permission='core.add_payment',
    risk=RISK_NORMAL,
    validator=_validate_payment_record,
    authorize=_authorize_payment_record,
    preview=_preview_payment_record,
    execute=_execute_payment_record,
))

register(Command(
    name='mutate.payment.verify',
    description='Verify a pending payment. Destructive money movement: pauses for explicit approval.',
    parameters={
        'type': 'object',
        'properties': {
            'payment_id': {'type': 'integer'},
        },
        'required': ['payment_id'],
    },
    permission='core.verify_payment',
    risk=RISK_DESTRUCTIVE,
    source_states=(Payment.PENDING,),
    validator=_validate_payment_verify,
    authorize=_authorize_payment_verify,
    preview=_preview_payment_verify,
    execute=_execute_payment_verify,
))
