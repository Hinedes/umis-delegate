

import ipaddress

from django.conf import settings

from .audit import SOURCE_WEB, clear_current, set_current


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        proxies = [part.strip() for part in forwarded.split(',') if part.strip()]
        trusted = getattr(settings, 'TRUSTED_PROXY_COUNT', 1)
        index = max(0, len(proxies) - trusted)
        candidate = proxies[index]
    else:
        candidate = request.META.get('REMOTE_ADDR', '')

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


class AuditLogMiddleware:
    """Capture actor, IP, and source for audit writes during the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current(
            user=getattr(request, 'user', None),
            ip=_client_ip(request),
            source=SOURCE_WEB,
        )
        try:
            return self.get_response(request)
        finally:
            clear_current()
