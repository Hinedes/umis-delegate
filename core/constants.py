from django.utils.translation import gettext_lazy as _

# Default service-location topology. A deployment with a different layout changes
# this list. The inventory system may later source floors from Location records.
FLOOR_CHOICES = [
    ('2', _('Second floor')),
    ('3', _('Third floor')),
]
