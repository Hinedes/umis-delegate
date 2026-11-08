"""Auth audit hooks.

The only signal-based auditing: Django owns the login and logout views, so
these signals are the canonical hook. Everything else is an explicit call at
the layer that owns the state change.
"""
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .audit import SOURCE_SYSTEM, SOURCE_WEB, audit
from .models import AuditLog


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    audit(
        action=AuditLog.LOGIN,
        actor=user,
        object_type='auth.User',
        object_id=str(user.pk),
        description='Login',
        source=SOURCE_WEB if request is not None else SOURCE_SYSTEM,
        critical=request is None,
    )


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    if user is None:
        return
    audit(
        action=AuditLog.LOGOUT,
        actor=user,
        object_type='auth.User',
        object_id=str(user.pk),
        description='Logout',
        source=SOURCE_WEB if request is not None else SOURCE_SYSTEM,
        critical=request is None,
    )
