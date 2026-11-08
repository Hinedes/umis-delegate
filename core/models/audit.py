from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditLogQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError(_("Audit history is append-only."))

    def update(self, **kwargs):
        raise ValidationError(_("Audit history is append-only."))


class AuditLog(models.Model):
    """Append-only history of meaningful state changes.

    Not application logging and not page-view tracking. System actions carry a
    null actor and a null IP; nothing is fabricated to fill the gaps.
    """

    CREATE = 'CREATE'
    UPDATE = 'UPDATE'
    DELETE = 'DELETE'
    CANCEL = 'CANCEL'
    VOID = 'VOID'
    VERIFY = 'VERIFY'
    REJECT = 'REJECT'
    REFUND = 'REFUND'
    OPEN = 'OPEN'
    CLOSE = 'CLOSE'
    LOGIN = 'LOGIN'
    LOGOUT = 'LOGOUT'
    ACTION_CHOICES = [
        (CREATE, _("Create")),
        (UPDATE, _("Update")),
        (DELETE, _("Delete")),
        (CANCEL, _("Cancel")),
        (VOID, _("Void")),
        (VERIFY, _("Verify")),
        (REJECT, _("Reject")),
        (REFUND, _("Refund")),
        (OPEN, _("Open")),
        (CLOSE, _("Close")),
        (LOGIN, _("Login")),
        (LOGOUT, _("Logout")),
    ]

    WEB = 'WEB'
    COMMAND = 'COMMAND'
    INTEGRATION = 'INTEGRATION'
    SYSTEM = 'SYSTEM'
    AGENT = 'AGENT'
    SOURCE_CHOICES = [
        (WEB, _("Web")),
        (COMMAND, _("Command")),
        (INTEGRATION, _("Integration")),
        (SYSTEM, _("System")),
        (AGENT, _("Agent")),
    ]

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("When"))
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
        verbose_name=_("Actor"),
    )
    actor_label = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=_("Actor label"),
        help_text=_("Snapshot taken when the event is written."),
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES, verbose_name=_("Action"))
    object_type = models.CharField(max_length=50, blank=True, verbose_name=_("Object type"))
    object_id = models.CharField(max_length=64, blank=True, verbose_name=_("Object id"))
    description = models.CharField(max_length=255, blank=True, verbose_name=_("Description"))
    metadata = models.JSONField(default=dict, blank=True, verbose_name=_("Metadata"))
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=WEB, verbose_name=_("Source"))
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("IP address"))

    class Meta:
        verbose_name = _("Audit event")
        verbose_name_plural = _("Audit events")
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['created_at'], name='audit_created_idx'),
            models.Index(fields=['actor'], name='audit_actor_idx'),
            models.Index(fields=['action'], name='audit_action_idx'),
            models.Index(fields=['object_type', 'object_id'], name='audit_object_idx'),
        ]

    objects = AuditLogQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                _("Audit events cannot be changed."),
                code='audit_immutable',
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            _("Audit events cannot be deleted."),
            code='audit_deletion_forbidden',
        )

    def __str__(self):
        return f"{self.created_at} {self.action} {self.object_type}#{self.object_id}"
