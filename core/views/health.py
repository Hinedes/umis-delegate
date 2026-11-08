import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def healthz(request):
    """Liveness probe for the container and the public edge."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        logger.exception('Health check database probe failed')
        return JsonResponse({'status': 'error'}, status=503)
    return JsonResponse({'status': 'ok'})
