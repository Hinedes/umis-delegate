"""
Custom template tags for sortable table headers.

Usage in templates:
    {% load sorting_tags %}
    
    <th>{% sort_header 'patient' 'Patient' %}</th>
    <th>{% sort_header 'doctor' 'Doctor' %}</th>
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def sort_header(context, field, label):
    """
    Render a sortable table header with toggle functionality.
    
    Args:
        context: Template context (must contain 'request', 'current_sort', 'current_dir')
        field: The sort field name (URL param)
        label: Display label for the header
    
    Returns:
        Safe HTML string with sortable header link
    """
    request = context.get('request')
    current_sort = context.get('current_sort', '')
    current_dir = context.get('current_dir', 'asc')
    
    if not request:
        return label
    
    # Build sort URL preserving other params
    params = request.GET.copy()
    params.pop('page', None)  # Reset pagination on sort change
    
    # Toggle direction if same column, else default to asc
    if field == current_sort:
        new_dir = 'desc' if current_dir == 'asc' else 'asc'
    else:
        new_dir = 'asc'
    
    params['sort'] = field
    params['dir'] = new_dir
    url = f'?{params.urlencode()}'
    
    # Determine icon
    if field == current_sort:
        icon = '↑' if current_dir == 'asc' else '↓'
        icon_html = f'<span style="margin-left: 4px;">{icon}</span>'
    else:
        icon_html = ''
    
    html = f'''<a href="{url}" style="text-decoration: none; color: inherit; display: flex; align-items: center; gap: 4px;">
        {label}{icon_html}
    </a>'''
    
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def sort_url(context, field):
    """
    Get the URL for sorting by a specific field.
    
    Args:
        context: Template context
        field: The sort field name
    
    Returns:
        URL string
    """
    request = context.get('request')
    current_sort = context.get('current_sort', '')
    current_dir = context.get('current_dir', 'asc')
    
    if not request:
        return ''
    
    params = request.GET.copy()
    params.pop('page', None)
    
    if field == current_sort:
        new_dir = 'desc' if current_dir == 'asc' else 'asc'
    else:
        new_dir = 'asc'
    
    params['sort'] = field
    params['dir'] = new_dir
    
    return f'?{params.urlencode()}'


@register.simple_tag(takes_context=True)
def sort_icon(context, field):
    """
    Get the sort direction icon for a field.
    
    Returns:
        '↑' for ascending, '↓' for descending, '' if not sorted by this field
    """
    current_sort = context.get('current_sort', '')
    current_dir = context.get('current_dir', 'asc')
    
    if field == current_sort:
        return '↑' if current_dir == 'asc' else '↓'
    return ''
