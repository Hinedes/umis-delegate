from django.conf import settings


def site_settings(request):
    scheme = request.COOKIES.get('umis_color_scheme', 'light')
    if scheme not in ('light', 'dark'):
        scheme = 'light'
    return {
        'SITE_NAME': settings.SITE_NAME,
        'theme': {
            'color_scheme': scheme,
            'color_scheme_resolved': scheme,
            'animations': 'reduced',
        },
    }

def session_settings(request):
    return {
        'SESSION_COOKIE_AGE': settings.SESSION_COOKIE_AGE,
        'SESSION_WARN_SECONDS': settings.SESSION_WARN_SECONDS,
    }
