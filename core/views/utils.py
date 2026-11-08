from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST


@login_required
@require_POST
def session_ping(request):
    """Extend the current session. Called by the 'Stay signed in' prompt."""
    request.session.modified = True
    return JsonResponse({'status': 'ok'})
