"""Canonical audit writer.

One owner per event: the layer that owns the state change calls this helper,
and callers never log again. Critical actions (refunds, verifications,
rejections, shift closes) are written inside the business transaction and must
succeed with it. Routine actions use a savepoint so an audit failure cannot
poison an enclosing transaction, and the failure is logged, not swallowed.
"""
import logging
from contextvars import ContextVar

from django.db import transaction

logger = logging.getLogger('core.audit')

SOURCE_WEB = 'WEB'
SOURCE_COMMAND = 'COMMAND'
SOURCE_INTEGRATION = 'INTEGRATION'
SOURCE_SYSTEM = 'SYSTEM'
SOURCE_AGENT = 'AGENT'

_current_user = ContextVar('audit_current_user', default=None)
_current_ip = ContextVar('audit_current_ip', default=None)
_current_source = ContextVar('audit_current_source', default=None)

_USE_CONTEXT = object()


def get_current_user():
    return _current_user.get()


def get_current_ip():
    return _current_ip.get()


def get_current_source():
    return _current_source.get()


def set_current(user=None, ip=None, source=None):
    _current_user.set(user)
    _current_ip.set(ip)
    _current_source.set(source)


def clear_current():
    _current_user.set(None)
    _current_ip.set(None)
    _current_source.set(None)


def audit(
    *,
    action,
    obj=None,
    object_type='',
    object_id='',
    description='',
    metadata=None,
    actor=None,
    source=None,
    critical=False,
    ip_address=_USE_CONTEXT,
):
    """Write one audit event. Returns the row, or None when a routine write fails."""
    from .models import AuditLog

    if actor is None:
        actor = get_current_user()
    if actor is not None and not getattr(actor, 'is_authenticated', False):
        actor = None

    label = ''
    if actor is not None:
        label = (actor.get_full_name() or actor.username or '')[:150]

    if obj is not None:
        object_type = f'{obj._meta.app_label}.{obj.__class__.__name__}'
        object_id = str(obj.pk or '')

    if source is None:
        source = get_current_source() or SOURCE_SYSTEM

    if ip_address is _USE_CONTEXT:
        ip_address = get_current_ip()

    event = AuditLog(
        actor=actor,
        actor_label=label,
        action=action,
        object_type=object_type,
        object_id=object_id,
        description=description[:255],
        metadata=metadata or {},
        source=source,
        ip_address=ip_address or None,
    )

    if critical:
        event.save()
        return event

    try:
        with transaction.atomic():
            event.save()
        return event
    except Exception:
        logger.error(
            "Audit write failed: action=%s object=%s#%s",
            action, object_type, object_id,
            exc_info=True,
        )
        return None
