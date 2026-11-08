"""Mediated command gateway.

The agent runtime can only reach this module through ``RunGatewayClient``.
The gateway validates, authorizes, scopes, gates destructive commands, executes
inside a transaction, and writes the agent audit trail.
"""

import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.audit import (
    SOURCE_AGENT,
    get_current_ip,
    get_current_source,
    get_current_user,
    set_current,
)
from core.audit import audit as write_audit
from core.forms import assignable_doctors
from core.models import AuditLog, Service

from . import commands as _commands  # noqa: F401 - registers the command set
from .models import AgentApproval, AgentPolicy, AgentRun, AgentStep
from .registry import (
    CommandConflict,
    CommandDenied,
    CommandError,
    CommandNotFound,
    CommandValidationError,
    all_commands,
    command_definitions,
    get_command,
)

logger = logging.getLogger(__name__)


ID_KEYS = (
    'patient_id', 'visit_id', 'payment_id', 'visit_service_id',
    'assigned_doctor_id', 'service_id', 'method_id',
)

ACTION_FOR_COMMAND = {
    'mutate.patient.create': AuditLog.CREATE,
    'mutate.visit.request': AuditLog.CREATE,
    'mutate.visit.add_service': AuditLog.CREATE,
    'mutate.visit.confirm': AuditLog.UPDATE,
    'mutate.visit.cancel': AuditLog.CANCEL,
    'mutate.payment.record': AuditLog.CREATE,
    'mutate.payment.verify': AuditLog.VERIFY,
}


def args_hash(command_name, args):
    payload = json.dumps(
        {'command': command_name, 'args': args},
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


@contextmanager
def _agent_audit_context(user):
    previous = (get_current_user(), get_current_ip(), get_current_source())
    set_current(user=user, ip=previous[1], source=SOURCE_AGENT)
    try:
        yield
    finally:
        set_current(user=previous[0], ip=previous[1], source=previous[2])


def _metadata(run, step, *, phase, result_status, approval=None):
    metadata = {
        'command': step.command,
        'args_hash': step.args_hash,
        'run_id': str(run.pk),
        'step_id': str(step.pk),
        'model': run.model,
        'phase': phase,
        'result_status': result_status,
    }
    if approval is not None:
        metadata['approval_id'] = str(approval.pk)
    for key in ID_KEYS:
        source = step.args if isinstance(step.args, dict) else {}
        value = source.get(key)
        if value in (None, ''):
            value = (step.result or {}).get(key) if isinstance(step.result, dict) else None
        if value not in (None, ''):
            metadata[key] = value
    return metadata


def _action_for_phase(command_name, phase):
    if phase == 'effect':
        return ACTION_FOR_COMMAND.get(command_name, AuditLog.UPDATE)
    if phase in ('denied', 'unknown_command', 'rejected', 'invalidated', 'expired'):
        return AuditLog.REJECT
    return AuditLog.UPDATE


def _audit(run, step, *, phase, result_status, approval=None, critical=True):
    action = _action_for_phase(step.command, phase)
    description = f"Agent command {phase}"[:255]
    return write_audit(
        action=action,
        object_type='control.AgentStep',
        object_id=str(step.pk),
        description=description,
        metadata=_metadata(
            run, step, phase=phase, result_status=result_status, approval=approval,
        ),
        actor=run.user,
        source=SOURCE_AGENT,
        critical=critical,
    )


def _step_response(step):
    response = {
        'status': step.status,
        'step_id': step.pk,
        'command': step.command,
        'result': step.result or {},
        'error': step.error,
        'replayed': True,
    }
    if step.status == AgentStep.AWAITING_APPROVAL:
        approval = step.approvals.filter(status=AgentApproval.PENDING).order_by('-pk').first()
        if approval is not None:
            response['approval_id'] = approval.pk
            response['preview'] = approval.preview
            response['expires_at'] = approval.expires_at.isoformat()
    return response


def _create_step(run, index, command_name, args, hash_value, expected_version,
                 status, result=None, error=''):
    return AgentStep.objects.create(
        run=run,
        index=index,
        command=command_name[:100],
        args=args,
        args_hash=hash_value,
        status=status,
        result=_json_safe(result or {}),
        error=error[:2000],
        expected_version=(expected_version or '')[:64],
    )


def _next_index(run):
    last = run.steps.order_by('-index').values_list('index', flat=True).first()
    return 0 if last is None else last + 1


def _permission_allows(user, command):
    if command.permission is None:
        return True
    if user.has_perm(command.permission):
        return True
    return bool(command.object_permission)


def _snapshot(authz):
    return {
        'version': authz.get('version') or '',
        'status': authz.get('status') or '',
        'affected': int(authz.get('affected') or 0),
    }


def _extract_expected(args, expected_version):
    if not isinstance(args, dict):
        return args, expected_version
    clean = dict(args)
    inline = clean.pop('expected_version', None)
    return clean, (expected_version or inline or '')


def _deny(run, index, command_name, args, hash_value, status, error,
          expected_version='', phase='denied'):
    step = _create_step(
        run, index, command_name, args, hash_value, expected_version,
        status, result={'error': error}, error=error,
    )
    _audit(run, step, phase=phase, result_status=status, critical=True)
    return {
        'status': status,
        'step_id': step.pk,
        'command': step.command,
        'result': {'error': error},
        'error': error,
        'replayed': False,
    }


def execute_agent_command(run, user, command_name, args, expected_version='',
                          step_index=None, dry_run=False):
    """Run one command envelope and return a sanitized result."""
    if run.user_id != user.pk:
        raise CommandDenied('The run belongs to another user.')
    if not user.is_active:
        raise CommandDenied('The user is inactive.')

    clean_args, expected_version = _extract_expected(args or {}, expected_version)
    safe_args = _json_safe(clean_args) if isinstance(clean_args, dict) else {}
    hash_value = args_hash(str(command_name), safe_args)

    with transaction.atomic():
        run = AgentRun.objects.select_for_update().get(pk=run.pk)
        index = step_index if step_index is not None else _next_index(run)
        existing = run.steps.filter(index=index).first()
        if existing is not None:
            return _step_response(existing)

        command = get_command(command_name)
        if command is None:
            return _deny(
                run, index, str(command_name), safe_args, hash_value,
                AgentStep.DENIED, 'Unknown command.',
                expected_version=expected_version, phase='unknown_command',
            )

        policy = AgentPolicy.current()
        if not policy.allows(command.name):
            return _deny(
                run, index, command.name, safe_args, hash_value,
                AgentStep.DENIED,
                'The agent is paused.' if not policy.enabled
                else 'This command is turned off in the agent policy.',
                expected_version=expected_version,
            )

        try:
            normalized = command.validator(clean_args)
        except CommandValidationError as exc:
            step = _create_step(
                run, index, command.name, safe_args, hash_value, expected_version,
                AgentStep.FAILED, result={'error': str(exc)}, error=str(exc),
            )
            _audit(
                run, step, phase='validation_failed', result_status=AgentStep.FAILED,
                critical=False,
            )
            return {
                'status': AgentStep.FAILED,
                'step_id': step.pk,
                'command': command.name,
                'result': {'error': str(exc)},
                'error': str(exc),
                'replayed': False,
            }

        normalized = _json_safe(normalized)
        hash_value = args_hash(command.name, normalized)

        if not _permission_allows(user, command):
            return _deny(
                run, index, command.name, normalized, hash_value,
                AgentStep.DENIED,
                f"Missing permission {command.permission}.",
                expected_version=expected_version,
            )

        try:
            authz = command.authorize(user, normalized)
        except (CommandDenied, CommandNotFound) as exc:
            return _deny(
                run, index, command.name, normalized, hash_value,
                AgentStep.DENIED, str(exc),
                expected_version=expected_version,
            )
        except CommandValidationError as exc:
            step = _create_step(
                run, index, command.name, normalized, hash_value, expected_version,
                AgentStep.FAILED, result={'error': str(exc)}, error=str(exc),
            )
            _audit(
                run, step, phase='validation_failed', result_status=AgentStep.FAILED,
                critical=False,
            )
            return {
                'status': AgentStep.FAILED,
                'step_id': step.pk,
                'command': command.name,
                'result': {'error': str(exc)},
                'error': str(exc),
                'replayed': False,
            }
        except CommandConflict as exc:
            step = _create_step(
                run, index, command.name, normalized, hash_value, expected_version,
                AgentStep.FAILED, result={'error': str(exc)}, error=str(exc),
            )
            _audit(
                run, step, phase='conflict', result_status=AgentStep.FAILED,
                critical=True,
            )
            return {
                'status': AgentStep.FAILED,
                'step_id': step.pk,
                'command': command.name,
                'result': {'error': str(exc)},
                'error': str(exc),
                'replayed': False,
            }

        authz = authz or {}
        status = authz.get('status') or ''
        if command.source_states and status and status not in command.source_states:
            error = 'The object is not in an allowed source state.'
            step = _create_step(
                run, index, command.name, normalized, hash_value, expected_version,
                AgentStep.FAILED, result={'error': error}, error=error,
            )
            _audit(run, step, phase='conflict', result_status=AgentStep.FAILED, critical=True)
            return {
                'status': AgentStep.FAILED,
                'step_id': step.pk,
                'command': command.name,
                'result': {'error': error},
                'error': error,
                'replayed': False,
            }

        is_write = command.name.startswith('mutate.')
        current_version = authz.get('version') or ''
        if is_write and not expected_version and current_version:
            expected_version = current_version
        if is_write and expected_version and current_version and expected_version != current_version:
            error = 'The object changed since it was read. Read it again.'
            step = _create_step(
                run, index, command.name, normalized, hash_value, expected_version,
                AgentStep.INVALIDATED, result={'error': error}, error=error,
            )
            _audit(
                run, step, phase='stale_version', result_status=AgentStep.INVALIDATED,
                critical=True,
            )
            return {
                'status': AgentStep.INVALIDATED,
                'step_id': step.pk,
                'command': command.name,
                'result': {'error': error},
                'error': error,
                'replayed': False,
            }

        preview = _json_safe(command.preview(user, normalized) or {})
        preview['snapshot'] = _snapshot(authz)
        preview['command'] = command.name
        preview['risk'] = command.risk

        if dry_run:
            return {
                'status': 'PREVIEW',
                'command': command.name,
                'preview': preview,
            }

        if policy.requires_approval(command):
            step = _create_step(
                run, index, command.name, normalized, hash_value, expected_version,
                AgentStep.AWAITING_APPROVAL, result={'preview': preview},
            )
            approval = AgentApproval.objects.create(
                step=step,
                args_hash=hash_value,
                preview=preview,
                status=AgentApproval.PENDING,
                expires_at=timezone.now() + timedelta(minutes=policy.approval_minutes),
            )
            run.status = AgentRun.AWAITING_APPROVAL
            run.save(update_fields=['status', 'updated_at'])
            _audit(
                run, step, phase='approval_requested',
                result_status=AgentStep.AWAITING_APPROVAL,
                approval=approval, critical=True,
            )
            return {
                'status': AgentStep.AWAITING_APPROVAL,
                'step_id': step.pk,
                'command': command.name,
                'approval_id': approval.pk,
                'preview': preview,
                'expires_at': approval.expires_at.isoformat(),
                'replayed': False,
            }

        step = _create_step(
            run, index, command.name, normalized, hash_value, expected_version,
            AgentStep.PENDING,
        )
        if is_write:
            _audit(
                run, step, phase='intent', result_status='EXECUTING',
                critical=True,
            )

        try:
            with transaction.atomic():
                with _agent_audit_context(user):
                    result = _json_safe(command.execute(user, normalized, run, step))
        except (CommandError, ValidationError) as exc:
            error = str(exc)
            step.status = AgentStep.FAILED
            step.result = {'error': error}
            step.error = error[:2000]
            step.save(update_fields=['status', 'result', 'error', 'updated_at'])
            _audit(
                run, step, phase='failed', result_status=AgentStep.FAILED,
                critical=False,
            )
            return {
                'status': AgentStep.FAILED,
                'step_id': step.pk,
                'command': command.name,
                'result': {'error': error},
                'error': error,
                'replayed': False,
            }
        except Exception:
            logger.exception('Agent command %s failed', command.name)
            error = 'The command failed.'
            step.status = AgentStep.FAILED
            step.result = {'error': error}
            step.error = error
            step.save(update_fields=['status', 'result', 'error', 'updated_at'])
            _audit(
                run, step, phase='failed', result_status=AgentStep.FAILED,
                critical=False,
            )
            return {
                'status': AgentStep.FAILED,
                'step_id': step.pk,
                'command': command.name,
                'result': {'error': error},
                'error': error,
                'replayed': False,
            }

        step.status = AgentStep.SUCCEEDED
        step.result = result
        step.error = ''
        step.save(update_fields=['status', 'result', 'error', 'updated_at'])

        if is_write:
            _audit(
                run, step, phase='effect', result_status=AgentStep.SUCCEEDED,
                critical=True,
            )
        return {
            'status': AgentStep.SUCCEEDED,
            'step_id': step.pk,
            'command': command.name,
            'result': result,
            'error': '',
            'replayed': False,
        }


def _invalidate(approval, step, run, user, error):
    approval.status = AgentApproval.INVALIDATED
    approval.decided_by = user
    approval.decided_at = timezone.now()
    approval.method = 'confirm'
    approval.reason = error[:2000]
    approval.save()
    step.status = AgentStep.INVALIDATED
    step.error = error[:2000]
    step.result = {'error': error}
    step.save(update_fields=['status', 'error', 'result', 'updated_at'])
    run.status = AgentRun.FAILED
    run.save(update_fields=['status', 'updated_at'])
    _audit(
        run, step, phase='invalidated', result_status=AgentStep.INVALIDATED,
        approval=approval, critical=True,
    )
    return {
        'status': AgentStep.INVALIDATED,
        'step_id': step.pk,
        'command': step.command,
        'result': {'error': error},
        'error': error,
    }


def _expire(approval, step, run, user):
    approval.status = AgentApproval.EXPIRED
    approval.decided_by = user
    approval.decided_at = timezone.now()
    approval.method = 'confirm'
    approval.reason = 'Approval expired.'
    approval.save()
    step.status = AgentStep.EXPIRED
    step.error = 'Approval expired.'
    step.result = {'error': 'Approval expired.'}
    step.save(update_fields=['status', 'error', 'result', 'updated_at'])
    run.status = AgentRun.FAILED
    run.save(update_fields=['status', 'updated_at'])
    _audit(
        run, step, phase='expired', result_status=AgentStep.EXPIRED,
        approval=approval, critical=True,
    )
    return {
        'status': AgentStep.EXPIRED,
        'step_id': step.pk,
        'command': step.command,
        'result': {'error': 'Approval expired.'},
        'error': 'Approval expired.',
    }


def approve_step(approval, user, method='confirm'):
    """Revalidate and execute one approved destructive step."""
    approval_pk = approval.pk if isinstance(approval, AgentApproval) else approval
    with transaction.atomic():
        approval = (
            AgentApproval.objects.select_for_update()
            .select_related('step')
            .get(pk=approval_pk)
        )
        step = AgentStep.objects.select_for_update().get(pk=approval.step_id)
        run = AgentRun.objects.select_for_update().get(pk=step.run_id)

        if run.user_id != user.pk:
            raise CommandDenied('The run belongs to another user.')
        if approval.status != AgentApproval.PENDING:
            return {
                'status': approval.status,
                'approval_id': approval.pk,
                'step_id': step.pk,
                'error': 'This approval is no longer pending.',
                'replayed': True,
            }
        if timezone.now() >= approval.expires_at:
            return _expire(approval, step, run, user)

        command = get_command(step.command)
        policy = AgentPolicy.current()
        if command is None or not policy.requires_approval(command):
            return _invalidate(approval, step, run, user, 'The command is no longer available.')
        if not policy.allows(command.name):
            return _invalidate(approval, step, run, user, 'The agent policy no longer allows this command.')

        if (
            approval.args_hash != step.args_hash
            or args_hash(step.command, step.args) != step.args_hash
        ):
            return _invalidate(approval, step, run, user, 'The arguments changed.')

        if not _permission_allows(user, command):
            return _invalidate(approval, step, run, user, 'The permission was revoked.')

        try:
            authz = command.authorize(user, step.args) or {}
        except (
            CommandDenied, CommandNotFound, CommandConflict, CommandValidationError,
        ) as exc:
            return _invalidate(approval, step, run, user, str(exc))

        status = authz.get('status') or ''
        if command.source_states and status and status not in command.source_states:
            return _invalidate(approval, step, run, user, 'The source state changed.')

        current_version = authz.get('version') or ''
        if step.expected_version and current_version and step.expected_version != current_version:
            return _invalidate(approval, step, run, user, 'The object changed since preview.')

        snapshot = approval.preview.get('snapshot') or {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        current = _snapshot(authz)
        if (
            snapshot.get('affected') != current['affected']
            or (snapshot.get('status') or '') != current['status']
            or (
                snapshot.get('version') and current['version']
                and snapshot['version'] != current['version']
            )
        ):
            return _invalidate(approval, step, run, user, 'The preview no longer matches.')

        approval.status = AgentApproval.APPROVED
        approval.decided_by = user
        approval.decided_at = timezone.now()
        approval.method = method[:30]
        approval.reason = ''
        approval.save()
        _audit(
            run, step, phase='intent', result_status='EXECUTING',
            approval=approval, critical=True,
        )

        try:
            with transaction.atomic():
                with _agent_audit_context(user):
                    result = _json_safe(command.execute(user, step.args, run, step))
        except (CommandError, ValidationError) as exc:
            error = str(exc)
            step.status = AgentStep.FAILED
            step.error = error[:2000]
            step.result = {'error': error}
            step.save(update_fields=['status', 'error', 'result', 'updated_at'])
            run.status = AgentRun.FAILED
            run.save(update_fields=['status', 'updated_at'])
            _audit(
                run, step, phase='failed', result_status=AgentStep.FAILED,
                approval=approval, critical=False,
            )
            return {
                'status': AgentStep.FAILED,
                'step_id': step.pk,
                'command': step.command,
                'result': {'error': error},
                'error': error,
            }
        except Exception:
            logger.exception('Approved agent command %s failed', step.command)
            error = 'The command failed.'
            step.status = AgentStep.FAILED
            step.error = error
            step.result = {'error': error}
            step.save(update_fields=['status', 'error', 'result', 'updated_at'])
            run.status = AgentRun.FAILED
            run.save(update_fields=['status', 'updated_at'])
            _audit(
                run, step, phase='failed', result_status=AgentStep.FAILED,
                approval=approval, critical=False,
            )
            return {
                'status': AgentStep.FAILED,
                'step_id': step.pk,
                'command': step.command,
                'result': {'error': error},
                'error': error,
            }

        step.status = AgentStep.SUCCEEDED
        step.result = result
        step.error = ''
        step.save(update_fields=['status', 'result', 'error', 'updated_at'])
        run.status = AgentRun.RUNNING
        state = dict(run.state or {})
        pending = state.get('pending')
        if isinstance(pending, dict):
            pending['result'] = {
                'status': AgentStep.SUCCEEDED,
                'step_id': step.pk,
                'command': step.command,
                'result': result,
                'error': '',
            }
            state['pending'] = pending
            run.state = state
        run.save(update_fields=['status', 'state', 'updated_at'])
        _audit(
            run, step, phase='effect', result_status=AgentStep.SUCCEEDED,
            approval=approval, critical=True,
        )
        return {
            'status': AgentStep.SUCCEEDED,
            'step_id': step.pk,
            'command': step.command,
            'result': result,
            'error': '',
        }


def reject_step(approval, user, reason=''):
    """Decline a pending approval. Nothing executes."""
    approval_pk = approval.pk if isinstance(approval, AgentApproval) else approval
    with transaction.atomic():
        approval = AgentApproval.objects.select_for_update().select_related('step').get(pk=approval_pk)
        step = AgentStep.objects.select_for_update().get(pk=approval.step_id)
        run = AgentRun.objects.select_for_update().get(pk=step.run_id)
        if run.user_id != user.pk:
            raise CommandDenied('The run belongs to another user.')
        if approval.status != AgentApproval.PENDING:
            return {
                'status': approval.status,
                'approval_id': approval.pk,
                'step_id': step.pk,
                'error': 'This approval is no longer pending.',
            }
        approval.status = AgentApproval.REJECTED
        approval.decided_by = user
        approval.decided_at = timezone.now()
        approval.method = 'cancel'
        approval.reason = (reason or '')[:2000]
        approval.save()
        step.status = AgentStep.REJECTED
        step.error = 'The approver declined the command.'
        step.result = {'error': step.error}
        step.save(update_fields=['status', 'error', 'result', 'updated_at'])
        run.status = AgentRun.REJECTED
        state = dict(run.state or {})
        state.pop('pending', None)
        run.state = state
        run.save(update_fields=['status', 'state', 'updated_at'])
        _audit(
            run, step, phase='rejected', result_status=AgentStep.REJECTED,
            approval=approval, critical=True,
        )
        return {
            'status': AgentStep.REJECTED,
            'approval_id': approval.pk,
            'step_id': step.pk,
            'command': step.command,
            'error': step.error,
        }


class RunGatewayClient:
    """The only object the agent runtime receives. It exposes no ORM handles."""

    def __init__(self, run, user, policy=None):
        self.run = run
        self.user = user
        self.policy = policy or AgentPolicy.current()

    def context(self):
        doctors = [
            {
                'id': doctor.pk,
                'name': doctor.get_full_name().strip() or doctor.get_username(),
                'username': doctor.get_username(),
            }
            for doctor in assignable_doctors()
        ]
        services = [
            {
                'id': service.pk,
                'name': service.name,
                'price': str(service.price),
            }
            for service in Service.objects.filter(active=True).order_by('name', 'pk')
        ]
        return {
            'now': timezone.localtime().isoformat(),
            'user': self.user.get_username(),
            'model': self.run.model,
            'doctors': doctors,
            'services': services,
            'instructions': self.policy.instructions,
            'policy': self.policy.as_dict(),
            'access': self.access(),
        }

    def access(self):
        """What this user may run, from the same permissions the gateway enforces.

        Context for the model only; every call is still checked in ``execute``.
        """
        roles = sorted(self.user.groups.values_list('name', flat=True))
        if self.user.is_superuser:
            roles.insert(0, 'Superuser')
        access = {'roles': roles, 'can': [], 'per_visit': [], 'assigned_only': [], 'cannot': []}
        for name, command in all_commands().items():
            if not self.policy.allows(name):
                continue
            if command.permission is None:
                access['per_visit'].append(name)
            elif self.user.has_perm(command.permission):
                access['can'].append(name)
            elif command.object_permission:
                access['assigned_only'].append(name)
            else:
                access['cannot'].append(name)
        return access

    def tools(self):
        return [
            definition for definition in command_definitions()
            if self.policy.allows(definition['function']['name'])
        ]

    def has_command(self, name):
        return get_command(name) is not None

    def execute(self, command_name, args, expected_version='', step_index=None):
        return execute_agent_command(
            self.run, self.user, command_name, args,
            expected_version=expected_version,
            step_index=step_index,
        )

    def load_state(self):
        self.run.refresh_from_db()
        return dict(self.run.state or {})

    def save_state(self, state):
        self.run.state = state
        self.run.save(update_fields=['state', 'updated_at'])

    def set_status(self, status, error=''):
        self.run.status = status
        if error:
            state = dict(self.run.state or {})
            state['error'] = error
            self.run.state = state
            self.run.save(update_fields=['status', 'state', 'updated_at'])
        else:
            self.run.save(update_fields=['status', 'updated_at'])

    def mark_answer(self, answer):
        state = dict(self.run.state or {})
        state['answer'] = answer
        state['error'] = ''
        self.run.state = state
        self.run.status = AgentRun.SUCCEEDED
        self.run.save(update_fields=['status', 'state', 'updated_at'])
