from django import forms
from django.forms import formset_factory
from apps.core.models import StoreLocation, StoreSettings
from apps.customers.models import Customer
from apps.inventory.models import ProductVariant
from apps.sales.models import PriceList


class InvoiceCreateForm(forms.Form):
    customer = forms.ModelChoiceField(queryset=Customer.objects.filter(is_active=True).order_by("name"))
    location = forms.ModelChoiceField(queryset=StoreLocation.objects.filter(is_active=True))
    price_list = forms.ChoiceField(choices=PriceList.choices)
    tax_mode = forms.ChoiceField(choices=StoreSettings.TaxMode.choices)
    invoice_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    place_of_supply_state = forms.CharField(
        max_length=10,
        required=False,
        label="Region / Governorate",
        help_text="Leave blank to use customer's governorate/region code.",
    )
    invoice_discount = forms.DecimalField(max_digits=13, decimal_places=3, min_value=0, initial=0, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    issue_now = forms.BooleanField(initial=True, required=False, label="Issue immediately (deduct stock & generate invoice #)")


class InvoiceLineForm(forms.Form):
    variant = forms.ModelChoiceField(
        queryset=ProductVariant.objects.filter(is_active=True, product__status="ACTIVE")
        .select_related("product", "product__brand")
        .order_by("product__name", "size")
    )
    quantity = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={"min": "1", "placeholder": "1"}))
    unit_price = forms.DecimalField(
        max_digits=13,
        decimal_places=3,
        min_value=0,
        required=False,
        help_text="Leave blank for automatic wholesale/retail price.",
    )
    discount_percent = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100", "placeholder": "0"}),
    )


InvoiceLineFormSet = formset_factory(InvoiceLineForm, extra=1, min_num=1, validate_min=True)
