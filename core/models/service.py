from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Service(models.Model):
    """Something the clinic offers and can attach to a visit.

    Price is catalog policy. A visit copies it into a line item at purchase
    time, so later price changes do not touch history.

    A medicine service is a catalog row only. Physical stock and the link to a
    consumable belong to the inventory system.
    """

    SERVICE = 'SERVICE'
    MEDICINE = 'MEDICINE'
    CATEGORY_CHOICES = [
        (SERVICE, _("Service")),
        (MEDICINE, _("Medicine")),
    ]

    name = models.CharField(max_length=255, verbose_name=_("Name"))
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default=SERVICE,
        verbose_name=_("Category"),
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
        verbose_name=_("Price"),
    )
    active = models.BooleanField(default=True, verbose_name=_("Active"))

    class Meta:
        verbose_name = _("Service")
        verbose_name_plural = _("Services")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name='check_service_price_non_negative',
            ),
        ]

    def __str__(self):
        return self.name
