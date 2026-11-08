from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.forms import inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .constants import FLOOR_CHOICES
from .models import (
    Expense,
    ExpenseCategory,
    MedicalRecord,
    Patient,
    PaymentMethod,
    Service,
    Visit,
    VisitService,
)


def _apply_form_control_classes(form):
    for field in form.fields.values():
        if not isinstance(field.widget, (forms.CheckboxInput, forms.HiddenInput)):
            field.widget.attrs['class'] = 'form-control-core'


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ['full_name', 'phone', 'national_id', 'birth_date', 'floor', 'notes']
        widgets = {
            'birth_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.duplicate_warning = None
        # A type="tel" input triggers the browser's own phone tooltip, which
        # rejects valid local numbers. Keep the phone keyboard via inputmode;
        # phonenumbers still validates the submitted value.
        self.fields['phone'].widget.input_type = 'text'
        self.fields['phone'].widget.attrs['inputmode'] = 'tel'
        self.fields['phone'].widget.attrs['autocomplete'] = 'tel'
        _apply_form_control_classes(self)

    def clean(self):
        cleaned = super().clean()
        full_name = cleaned.get('full_name')
        phone = cleaned.get('phone')
        if full_name and phone:
            duplicates = Patient.objects.filter(full_name__iexact=full_name, phone=phone)
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                self.duplicate_warning = _("A patient with this name and phone already exists.")
        return cleaned


class MedicalRecordForm(forms.ModelForm):
    class Meta:
        model = MedicalRecord
        fields = ['record_type', 'content']
        widgets = {
            'content': forms.Textarea(attrs={'rows': 6}),
        }


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ['name', 'category', 'price', 'active']
        widgets = {
            'price': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Services default to SERVICE when no category is chosen.
        self.fields['category'].required = False
        _apply_form_control_classes(self)

    def clean_category(self):
        return self.cleaned_data.get('category') or Service.SERVICE


def assignable_doctors():
    """Users with the practice_medicine capability, regardless of role name."""
    User = get_user_model()
    return (
        User.objects.filter(
            Q(user_permissions__codename='practice_medicine')
            | Q(groups__permissions__codename='practice_medicine'),
            is_active=True,
        )
        .distinct()
        .order_by('first_name', 'last_name', 'username')
    )


class VisitForm(forms.ModelForm):
    class Meta:
        model = Visit
        fields = [
            'patient', 'visit_datetime', 'visit_type', 'floor',
            'assigned_doctor', 'referral_source', 'comment',
        ]
        widgets = {
            'visit_datetime': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_doctor'].queryset = assignable_doctors()
        self.fields['patient'].empty_label = _("Select")
        self.fields['assigned_doctor'].empty_label = _("Select")
        _apply_form_control_classes(self)
        if self.instance and self.instance.pk and self.instance.visit_datetime:
            self.initial['visit_datetime'] = self.instance.visit_datetime.strftime('%Y-%m-%dT%H:%M')


class VisitServiceForm(forms.ModelForm):
    class Meta:
        model = VisitService
        fields = ['service', 'quantity']
        widgets = {
            'quantity': forms.NumberInput(attrs={'min': '1', 'step': '1'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Service and price are fixed once a line exists. Changing what was
        # performed means cancel the line and add a new one.
        if self.instance.pk:
            self.fields['service'].disabled = True
        self.fields['quantity'].min_value = 1


VisitServiceFormSet = inlineformset_factory(
    Visit,
    VisitService,
    form=VisitServiceForm,
    extra=1,
    can_delete=True,
)


class PaymentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0.01,
        label=_("Amount"),
    )
    method = forms.ModelChoiceField(
        queryset=PaymentMethod.objects.none(),
        label=_("Method"),
    )
    notes = forms.CharField(
        required=False,
        label=_("Notes"),
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['method'].queryset = PaymentMethod.objects.filter(
            active=True,
        ).order_by('order', 'name')


class ExpenseForm(forms.ModelForm):
    date = forms.DateTimeField(
        initial=timezone.now,
        label=_("Date"),
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local'},
            format='%Y-%m-%dT%H:%M',
        ),
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S'],
    )

    class Meta:
        model = Expense
        fields = ['category', 'amount', 'recipient', 'explanation', 'method', 'floor', 'date']
        widgets = {
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'explanation': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = ExpenseCategory.objects.filter(
            active=True,
        ).order_by('order', 'name')
        self.fields['method'].queryset = PaymentMethod.objects.filter(
            active=True,
        ).order_by('order', 'name')
        self.fields['amount'].min_value = Decimal('0.01')


class ShiftOpenForm(forms.Form):
    floor = forms.ChoiceField(choices=FLOOR_CHOICES, label=_("Floor"))
    opening_cash = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal('0'),
        initial=0,
        label=_("Opening cash"),
    )


class ShiftCloseForm(forms.Form):
    closing_cash = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal('0'),
        label=_("Closing cash"),
    )
    notes = forms.CharField(
        required=False,
        label=_("Notes"),
        widget=forms.Textarea(attrs={'rows': 2}),
    )


class MedicationOrderForm(forms.Form):
    visit = forms.ModelChoiceField(queryset=Visit.objects.none(), label=_("Visit"))
    consumable = forms.ModelChoiceField(queryset=Visit.objects.none(), label=_("Medication"))
    total_quantity = forms.IntegerField(min_value=1, label=_("Total quantity"))

    def __init__(self, *args, visits=None, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import Consumable

        self.fields['visit'].queryset = visits if visits is not None else Visit.objects.none()
        self.fields['consumable'].queryset = Consumable.objects.filter(
            active=True,
        ).order_by('name')
