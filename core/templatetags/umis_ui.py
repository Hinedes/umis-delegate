"""Small template helpers for the demo-facing UI."""
import re

from django import template
from django.template.defaultfilters import date as date_filter
from django.utils.text import Truncator
from django.utils.translation import override

register = template.Library()


@register.filter
def en_date(value, fmt='D, j M Y, H:i'):
    """Render a date in English regardless of the active language."""
    if not value:
        return ''
    with override('en'):
        return date_filter(value, fmt)


@register.filter
def fc_control(field):
    """Render a bound field with the core input class unless the form already set one."""
    if field.widget_type in ('checkbox', 'radioselect', 'checkboxselectmultiple'):
        return field
    return field.as_widget(attrs={'class': field.field.widget.attrs.get('class') or 'form-control-core'})


@register.filter
def short_result(result):
    """One-line summary of an agent step result for the run timeline."""
    if not result:
        return ''
    if isinstance(result, list):
        return f'{len(result)} records'
    if not isinstance(result, dict):
        return Truncator(str(result)).chars(120)
    preview = result.get('preview')
    if isinstance(preview, dict) and preview.get('summary'):
        return Truncator(preview['summary']).chars(120)
    parts = [
        f'{key}: {len(value)} records' if isinstance(value, list) else f'{key}: {value}'
        for key, value in result.items() if not isinstance(value, dict)
    ]
    return Truncator(', '.join(parts) or f'{len(result)} fields').chars(120)


@register.filter
def plain_text(value):
    """Drop Markdown emphasis and code ticks the model adds; the UI renders plain text."""
    return re.sub(r'(\*\*|__|`)', '', str(value or ''))
