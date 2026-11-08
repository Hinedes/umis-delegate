"""
Reusable sorting utilities for Django list views.

Usage in views:
    from .sorting import apply_sorting
    
    SORTABLE_FIELDS = {
        'patient': 'patient__full_name',
        'doctor': 'assigned_doctor__username',
        'date': 'visit_datetime',
        'id': 'id',
    }
    
    def my_list_view(request):
        qs = MyModel.objects.all()
        qs, sort_context = apply_sorting(request, qs, SORTABLE_FIELDS, default_sort='-id')
        return render(request, 'my_list.html', {'objects': qs, **sort_context})
"""


def apply_sorting(request, queryset, sortable_fields, default_sort='-id'):
    """
    Apply sorting to a queryset based on request query parameters.
    
    Args:
        request: Django HttpRequest object
        queryset: Django QuerySet to sort
        sortable_fields: Dict mapping URL param names to model field names
                        e.g., {'patient': 'patient__full_name', 'date': 'created_at'}
        default_sort: Default sort field (prefix with '-' for descending)
    
    Returns:
        Tuple of (sorted_queryset, context_dict)
        context_dict contains 'current_sort', 'current_dir', 'sort_field' for template use
    """
    sort_param = request.GET.get('sort', '').strip()
    dir_param = request.GET.get('dir', 'asc').strip().lower()
    
    # Validate direction
    if dir_param not in ('asc', 'desc'):
        dir_param = 'asc'
    
    # Determine the field to sort by
    if sort_param in sortable_fields:
        db_field = sortable_fields[sort_param]
        order_field = f'-{db_field}' if dir_param == 'desc' else db_field
        queryset = queryset.order_by(order_field)
    else:
        # Apply default sort
        sort_param = ''
        queryset = queryset.order_by(default_sort)
        # Extract direction from default for context
        if default_sort.startswith('-'):
            dir_param = 'desc'
        else:
            dir_param = 'asc'
    
    context = {
        'current_sort': sort_param,
        'current_dir': dir_param,
        'sortable_fields': list(sortable_fields.keys()),
    }
    
    return queryset, context

