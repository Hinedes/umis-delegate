from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.audit import audit
from core.models import AuditLog

from .agent import AgentRuntime
from .gateway import RunGatewayClient, approve_step, reject_step
from .llm import get_llm_client
from .models import AgentApproval, AgentPolicy, AgentRun, AgentStep
from .registry import all_commands

COMMAND_GROUPS = ('Read', 'Write', 'Destructive')


def _command_rows(policy):
    """One row per registered command, grouped Read / Write / Destructive."""
    roles_by_perm = {}
    for app_label, codename, group in Group.permissions.through.objects.values_list(
        'permission__content_type__app_label', 'permission__codename', 'group__name',
    ).order_by('group__name'):
        roles_by_perm.setdefault(f'{app_label}.{codename}', []).append(group)
    rows = []
    for name, command in all_commands().items():
        if command.permission:
            roles = list(roles_by_perm.get(command.permission, []))
            if command.object_permission:
                roles.append('assigned doctor')
            who = ', '.join(roles) or 'Superusers only'
        else:
            who = 'Per visit'
        if command.destructive:
            group = 'Destructive'
        else:
            group = 'Read' if name.startswith('query.') else 'Write'
        rows.append({
            'name': name,
            'group': group,
            # First sentence only: the rest is guidance written for the model.
            'description': command.description.split('. ')[0].rstrip('.') + '.',
            'who': who,
            'destructive': command.destructive,
            'enabled': name not in (policy.disabled_commands or []),
            'approval': policy.requires_approval(command),
        })
    return sorted(rows, key=lambda row: (COMMAND_GROUPS.index(row['group']), row['name']))


def _runtime(run, user, policy):
    llm = get_llm_client(reasoning_effort=policy.reasoning_effort)
    return AgentRuntime(
        llm, RunGatewayClient(run, user, policy=policy), max_steps=policy.max_steps,
    )


@login_required
def panel(request):
    policy = AgentPolicy.current()
    rows = _command_rows(policy)
    runs = (
        AgentRun.objects.filter(user=request.user)
        .prefetch_related('steps').order_by('-created_at', '-pk')[:20]
    )
    return render(request, 'control/panel.html', {
        'policy': policy,
        'commands': rows,
        'enabled_count': sum(1 for row in rows if row['enabled']),
        'approval_count': sum(1 for row in rows if row['approval']),
        'model_name': getattr(settings, 'VULTR_INFERENCE_MODEL', ''),
        'can_manage': request.user.is_superuser,
        'runs': runs,
    })


@login_required
@require_POST
def policy_update(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    policy = AgentPolicy.current()
    before = policy.as_dict()

    if 'toggle' in request.POST:
        policy.enabled = request.POST.get('toggle') == 'resume'
    else:
        names = set(all_commands())
        enabled = set(request.POST.getlist('enabled_commands')) & names
        policy.disabled_commands = sorted(names - enabled)
        policy.approval_commands = sorted(set(request.POST.getlist('approval_commands')) & names)
        policy.reasoning_effort = request.POST.get('reasoning_effort', '')
        policy.instructions = (request.POST.get('instructions') or '').strip()[:2000]
        try:
            policy.max_steps = int(request.POST.get('max_steps') or policy.max_steps)
            policy.approval_minutes = int(request.POST.get('approval_minutes') or policy.approval_minutes)
        except ValueError:
            messages.error(request, 'Limits must be whole numbers.')
            return redirect('control_panel')

    try:
        policy.full_clean(exclude=['updated_by'])
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('control_panel')
    policy.updated_by = request.user
    policy.save()

    after = policy.as_dict()
    changed = {key: value for key, value in after.items() if value != before[key]}
    audit(
        action=AuditLog.UPDATE,
        object_type='control.AgentPolicy',
        object_id=str(policy.pk),
        description='Agent policy updated',
        metadata={'changed': changed},
        actor=request.user,
        critical=True,
    )
    if 'toggle' in request.POST:
        messages.success(request, 'The agent is running.' if policy.enabled else 'The agent is paused.')
    else:
        messages.success(request, 'Agent policy saved.')
    return redirect('control_panel')


def _run_goal(request, goal, policy, prompt=None):
    """Create a run for ``goal`` and start it. Returns (run, result, error)."""
    if not request.session.session_key:
        request.session.save()
    run = AgentRun.objects.create(
        user=request.user,
        session_key=request.session.session_key or '',
        goal=goal,
        model=getattr(settings, 'VULTR_INFERENCE_MODEL', ''),
    )
    try:
        return run, _runtime(run, request.user, policy).start(prompt or goal), ''
    except Exception as exc:
        run.refresh_from_db()
        if run.status == AgentRun.RUNNING:
            run.status = AgentRun.FAILED
            state = dict(run.state or {})
            state['error'] = str(exc) or 'The agent failed.'
            run.state = state
            run.save(update_fields=['status', 'state', 'updated_at'])
        return run, {}, str(exc) or 'The agent could not start.'


@login_required
@require_POST
def start_run(request):
    goal = (request.POST.get('goal') or '').strip()
    if not goal:
        messages.error(request, 'Enter a goal for the agent.')
        return redirect('control_panel')
    goal = goal[:4000]
    policy = AgentPolicy.current()
    if not policy.enabled:
        messages.error(request, 'The agent is paused by an administrator.')
        return redirect(request.META.get('HTTP_REFERER') or 'control_panel')

    run, result, error = _run_goal(request, goal, policy)
    if error:
        messages.error(request, error)
        return redirect('control_run_detail', pk=run.pk)

    if result.get('status') == AgentRun.AWAITING_APPROVAL:
        messages.info(request, 'The agent is waiting for your approval.')
    elif result.get('status') == AgentRun.FAILED:
        messages.error(request, result.get('error') or 'The agent failed.')
    else:
        messages.success(request, 'The agent finished.')
    return redirect('control_run_detail', pk=run.pk)


@login_required
@require_POST
def assistant_ask(request):
    """Chat widget: one message starts one ordinary run and returns the reply inline."""
    goal = (request.POST.get('goal') or '').strip()[:4000]
    if not goal:
        return HttpResponse(status=204)
    policy = AgentPolicy.current()
    if not policy.enabled:
        return render(request, 'control/partials/assistant_reply.html', {
            'goal': goal, 'paused': True,
        })
    page = (request.POST.get('page') or '').strip()
    # The page path lets "this visit" resolve; it is context, not an instruction.
    prompt = goal
    if page.startswith('/') and len(page) <= 200:
        prompt = f'{goal}\n\n(Context: the user sent this from the page {page}.)'

    run, result, error = _run_goal(request, goal, policy, prompt=prompt)
    if request.headers.get('HX-Request') != 'true':
        return redirect('control_run_detail', pk=run.pk)
    run.refresh_from_db()
    state = run.state or {}
    return render(request, 'control/partials/assistant_reply.html', {
        'goal': goal,
        'run': run,
        'answer': state.get('answer', ''),
        'error': error or state.get('error', ''),
        'preview': result.get('preview') or {},
    })


@login_required
def run_detail(request, pk):
    run = get_object_or_404(AgentRun, pk=pk)
    if run.user_id != request.user.pk and not request.user.is_superuser:
        raise PermissionDenied

    steps = list(run.steps.order_by('index').prefetch_related('approvals'))
    pending_approval = None
    for step in steps:
        for approval in step.approvals.all():
            if approval.status == AgentApproval.PENDING:
                pending_approval = approval
                break
        if pending_approval:
            break

    state = run.state or {}
    saved_effort = (state.get('policy') or {}).get('reasoning_effort', '')
    audit_events = (
        AuditLog.objects
        .filter(metadata__run_id=str(run.pk))
        .order_by('created_at', 'pk')
    )
    return render(request, 'control/run_detail.html', {
        'run': run,
        'steps': steps,
        'pending_approval': pending_approval,
        'transcript': state.get('messages', []),
        'answer': state.get('answer', ''),
        # Older runs stored the label itself, so an unknown value is shown as is.
        'run_effort': dict(AgentPolicy.EFFORT_CHOICES).get(saved_effort, saved_effort),
        'error': state.get('error', ''),
        'audit_events': audit_events,
    })


@login_required
@require_POST
def approval_decide(request, pk, approval_pk, decision):
    run = get_object_or_404(AgentRun, pk=pk, user=request.user)
    approval = get_object_or_404(
        AgentApproval.objects.select_related('step', 'step__run'),
        pk=approval_pk,
        step__run=run,
    )

    if decision == 'cancel':
        try:
            reject_step(approval, request.user, reason='Rejected in the control panel.')
        except Exception as exc:
            messages.error(request, str(exc) or 'The approval could not be declined.')
        else:
            messages.warning(request, 'The destructive command was declined.')
        return redirect('control_run_detail', pk=run.pk)

    if decision != 'confirm':
        messages.error(request, 'Unknown approval action.')
        return redirect('control_run_detail', pk=run.pk)

    try:
        result = approve_step(approval, request.user, method='confirm')
    except Exception as exc:
        messages.error(request, str(exc) or 'The approval could not be executed.')
        return redirect('control_run_detail', pk=run.pk)

    if result.get('status') != AgentStep.SUCCEEDED:
        messages.error(request, result.get('error') or 'The approved command did not run.')
        return redirect('control_run_detail', pk=run.pk)

    messages.success(request, 'The approved command ran.')
    try:
        resumed = _runtime(run, request.user, AgentPolicy.current()).resume()
    except Exception as exc:
        run.refresh_from_db()
        run.status = AgentRun.FAILED
        state = dict(run.state or {})
        state['error'] = (
            'The approved command ran, but the agent could not continue: '
            f'{exc}'
        )
        run.state = state
        run.save(update_fields=['status', 'state', 'updated_at'])
        messages.warning(
            request,
            'The approved command ran, but the agent could not continue: '
            f'{exc}',
        )
        return redirect('control_run_detail', pk=run.pk)

    if resumed.get('status') == AgentRun.AWAITING_APPROVAL:
        messages.info(request, 'The agent is waiting for another approval.')
    elif resumed.get('status') == AgentRun.FAILED:
        messages.error(request, resumed.get('error') or 'The agent failed.')
    else:
        messages.success(request, 'The agent finished.')
    return redirect('control_run_detail', pk=run.pk)


@login_required
def audit_activity(request):
    if not (request.user.is_superuser or request.user.has_perm('core.view_auditlog')):
        raise PermissionDenied
    events = (
        AuditLog.objects
        .filter(source=AuditLog.AGENT)
        .order_by('-created_at', '-pk')[:200]
    )
    return render(request, 'control/audit.html', {'events': events})
