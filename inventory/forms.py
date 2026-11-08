from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import Service

from .models import Consumable, Location

ADJUSTMENT_REASONS = [
    ('ARRIVAL', _("Arrival (stock received)")),
    ('CORRECTION', _("Stocktake correction")),
    ('DAMAGED', _("Damaged or expired")),
    ('RETURN', _("Return")),
]

REASON_TO_MOVEMENT = {
    'ARRIVAL': 'RESTOCK',
    'CORRECTION': 'ADJUSTMENT',
    'DAMAGED': 'ADJUSTMENT',
    'RETURN': 'RESTOCK',
}


class ConsumableCreateForm(forms.ModelForm):
    location = forms.ModelChoiceField(
        queryset=Location.objects.order_by('floor', 'name'),
        label=_("Initial location"),
    )
    quantity = forms.IntegerField(min_value=0, initial=0, label=_("Initial quantity"))
    price = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        max_digits=10,
        initial=0,
        label=_("Service price"),
        help_text=_("Creates the medicine service this item is billed as."),
    )

    class Meta:
        model = Consumable
        fields = ['name', 'unit', 'category']


class ConsumableEditForm(forms.ModelForm):
    class Meta:
        model = Consumable
        fields = ['name', 'unit', 'category', 'service', 'active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        linked = Consumable.objects.exclude(pk=self.instance.pk).exclude(
            service__isnull=True,
        ).values_list('service_id', flat=True)
        self.fields['service'].queryset = Service.objects.filter(
            category=Service.MEDICINE,
        ).exclude(pk__in=linked).order_by('name')
        self.fields['service'].required = False


class StockAdjustmentForm(forms.Form):
    location = forms.ModelChoiceField(
        queryset=Location.objects.order_by('floor', 'name'),
        label=_("Location"),
    )
    quantity_change = forms.IntegerField(
        label=_("Quantity change"),
        help_text=_("Positive to add, negative to remove."),
    )
    reason_code = forms.ChoiceField(choices=ADJUSTMENT_REASONS, label=_("Reason"))
    notes = forms.CharField(
        required=False,
        label=_("Notes"),
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def clean_quantity_change(self):
        value = self.cleaned_data['quantity_change']
        if value == 0:
            raise forms.ValidationError(_("Quantity change cannot be zero."))
        return value
