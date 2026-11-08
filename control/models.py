from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class AgentRun(models.Model):
    """One human goal handled by the agent runtime."""

    RUNNING = 'RUNNING'
    AWAITING_APPROVAL = 'AWAITING_APPROVAL'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    REJECTED = 'REJECTED'
    STATUS_CHOICES = [
        (RUNNING, _("Running")),
        (AWAITING_APPROVAL, _("Awaiting approval")),
        (SUCCEEDED, _("Succeeded")),
        (FAILED, _("Failed")),
        (REJECTED, _("Rejected")),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='agent_runs',
        verbose_name=_("User"),
    )
    session_key = models.CharField(max_length=40, blank=True, verbose_name=_("Session key"))
    goal = models.TextField(verbose_name=_("Goal"))
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=RUNNING, verbose_name=_("Status"),
    )
    state = models.JSONField(default=dict, blank=True, verbose_name=_("State"))
    model = models.CharField(max_length=100, blank=True, verbose_name=_("Model"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated"))

    class Meta:
        verbose_name = _("Agent run")
        verbose_name_plural = _("Agent runs")
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['user', 'created_at'], name='control_run_user_idx')]

    def __str__(self):
        return f"Run {self.pk} ({self.status})"

    @property
    def outcome(self):
        """What happened, for people: a final reply is not success if commands were refused.

        Returns (label, badge class). Uses ``steps.all()`` so callers can prefetch.
        """
        if self.status == self.AWAITING_APPROVAL:
            return _("Awaiting approval"), 'status-pending'
        if self.status in (self.FAILED, self.REJECTED):
            return self.get_status_display(), 'status-cancelled'
        if self.status != self.SUCCEEDED:
            return self.get_status_display(), 'status-planned'
        statuses = {step.status for step in self.steps.all()}
        if AgentStep.DENIED in statuses:
            return _("Denied"), 'status-pending'
        if statuses & {AgentStep.FAILED, AgentStep.INVALIDATED, AgentStep.EXPIRED, AgentStep.REJECTED}:
            return _("Incomplete"), 'status-pending'
        if not statuses:
            return _("Answered"), 'badge-neutral'
        return self.get_status_display(), 'status-completed'


class AgentStep(models.Model):
    """One command emitted by the model and handled by the gateway."""

    PENDING = 'PENDING'
    AWAITING_APPROVAL = 'AWAITING_APPROVAL'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    DENIED = 'DENIED'
    REJECTED = 'REJECTED'
    INVALIDATED = 'INVALIDATED'
    EXPIRED = 'EXPIRED'
    STATUS_CHOICES = [
        (PENDING, _("Pending")),
        (AWAITING_APPROVAL, _("Awaiting approval")),
        (SUCCEEDED, _("Succeeded")),
        (FAILED, _("Failed")),
        (DENIED, _("Denied")),
        (REJECTED, _("Rejected")),
        (INVALIDATED, _("Invalidated")),
        (EXPIRED, _("Expired")),
    ]

    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name='steps',
        verbose_name=_("Run"),
    )
    index = models.PositiveIntegerField(verbose_name=_("Index"))
    command = models.CharField(max_length=100, verbose_name=_("Command"))
    args = models.JSONField(default=dict, blank=True, verbose_name=_("Arguments"))
    args_hash = models.CharField(max_length=64, blank=True, verbose_name=_("Arguments hash"))
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=PENDING, verbose_name=_("Status"),
    )
    result = models.JSONField(default=dict, blank=True, verbose_name=_("Result"))
    error = models.TextField(blank=True, verbose_name=_("Error"))
    expected_version = models.CharField(max_length=64, blank=True, verbose_name=_("Expected version"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated"))

    class Meta:
        verbose_name = _("Agent step")
        verbose_name_plural = _("Agent steps")
        ordering = ['run', 'index']
        constraints = [
            models.UniqueConstraint(fields=['run', 'index'], name='control_unique_run_step'),
        ]

    def __str__(self):
        return f"Run {self.run_id} step {self.index}: {self.command} ({self.status})"


class AgentApproval(models.Model):
    """Single-use, argument-bound consent for a destructive step."""

    PENDING = 'PENDING'
    APPROVED = 'APPROVED'
    REJECTED = 'REJECTED'
    EXPIRED = 'EXPIRED'
    INVALIDATED = 'INVALIDATED'
    STATUS_CHOICES = [
        (PENDING, _("Pending")),
        (APPROVED, _("Approved")),
        (REJECTED, _("Rejected")),
        (EXPIRED, _("Expired")),
        (INVALIDATED, _("Invalidated")),
    ]

    step = models.ForeignKey(
        AgentStep,
        on_delete=models.CASCADE,
        related_name='approvals',
        verbose_name=_("Step"),
    )
    args_hash = models.CharField(max_length=64, verbose_name=_("Arguments hash"))
    preview = models.JSONField(default=dict, blank=True, verbose_name=_("Preview"))
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=PENDING, verbose_name=_("Status"),
    )
    expires_at = models.DateTimeField(verbose_name=_("Expires at"))
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='agent_approvals',
        verbose_name=_("Decided by"),
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Decided at"))
    method = models.CharField(max_length=30, blank=True, verbose_name=_("Method"))
    reason = models.TextField(blank=True, verbose_name=_("Reason"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created"))

    class Meta:
        verbose_name = _("Agent approval")
        verbose_name_plural = _("Agent approvals")
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f"Approval {self.pk} for step {self.step_id} ({self.status})"


class AgentPolicy(models.Model):
    """Operator controls for the agent. Singleton row (pk=1).

    Every field can only tighten the bounds the code defines: role
    permissions and object scope are always enforced, destructive commands
    always need approval, and the limits cap at the runtime maximums.
    """

    # DeepSeek thinking levels (api-docs.deepseek.com/guides/thinking_mode):
    # low, high, max; other names are aliases (medium/xhigh -> high). The
    # model default is high. "none" turns thinking off.
    # '' sends nothing, which DeepSeek runs as high.
    EFFORT_CHOICES = [
        ('none', _("Off")),
        ('low', _("Low")),
        ('', _("High (default)")),
        ('max', _("Max")),
    ]

    enabled = models.BooleanField(default=True, verbose_name=_("Agent enabled"))
    reasoning_effort = models.CharField(
        max_length=10, blank=True, default='', choices=EFFORT_CHOICES,
        verbose_name=_("Thinking effort"),
    )
    instructions = models.TextField(
        blank=True, max_length=2000, verbose_name=_("Clinic instructions"),
    )
    max_steps = models.PositiveSmallIntegerField(
        default=12, validators=[MinValueValidator(1), MaxValueValidator(12)],
        verbose_name=_("Step limit per run"),
    )
    approval_minutes = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(15)],
        verbose_name=_("Approval window (minutes)"),
    )
    disabled_commands = models.JSONField(default=list, blank=True, verbose_name=_("Disabled commands"))
    approval_commands = models.JSONField(
        default=list, blank=True, verbose_name=_("Commands that also require approval"),
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name=_("Updated by"),
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated"))

    class Meta:
        verbose_name = _("Agent policy")
        verbose_name_plural = _("Agent policy")

    def __str__(self):
        return "Agent policy"

    @classmethod
    def current(cls):
        policy, _created = cls.objects.get_or_create(pk=1)
        return policy

    def allows(self, command_name):
        return self.enabled and command_name not in (self.disabled_commands or [])

    def requires_approval(self, command):
        return command.destructive or command.name in (self.approval_commands or [])

    def as_dict(self):
        """Settings as plain data: saved on each run and diffed for the audit log."""
        return {
            'enabled': self.enabled,
            'reasoning_effort': self.reasoning_effort,
            'max_steps': self.max_steps,
            'approval_minutes': self.approval_minutes,
            'instructions': self.instructions[:500],
            'disabled_commands': list(self.disabled_commands or []),
            'approval_commands': list(self.approval_commands or []),
        }
