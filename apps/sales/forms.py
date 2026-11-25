from django import forms
from django.forms import formset_factory
from apps.core.models import StoreLocation, StoreSettings
from apps.customers.models import Customer
from apps.inventory.models import ProductVariant
from .models import PriceList, SalesReturn


class SalesOrderForm(forms.Form):
    customer = forms.ModelChoiceField(queryset=Customer.objects.filter(is_active=True).order_by("name"))
    location = forms.ModelChoiceField(queryset=StoreLocation.objects.filter(is_active=True))
    price_list = forms.ChoiceField(choices=PriceList.choices)
    tax_mode = forms.ChoiceField(choices=StoreSettings.TaxMode.choices)
    order_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    invoice_discount = forms.DecimalField(max_digits=13, decimal_places=3, min_value=0, initial=0, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class SalesLineItemForm(forms.Form):
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
        help_text="Leave blank to use price list rate.",
    )
    discount_percent = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100", "placeholder": "0"}),
    )


SalesLineItemFormSet = formset_factory(SalesLineItemForm, extra=1, min_num=1, validate_min=True)


class SalesReturnLineForm(forms.Form):
    variant = forms.ModelChoiceField(queryset=ProductVariant.objects.select_related("product"))
    quantity = forms.IntegerField(min_value=1)


SalesReturnLineFormSet = formset_factory(SalesReturnLineForm, extra=1, min_num=1, validate_min=True)


class SalesReturnOptionsForm(forms.Form):
    condition = forms.ChoiceField(choices=SalesReturn.Condition.choices)
    reason = forms.CharField(max_length=255, required=False)
    refund_amount = forms.DecimalField(max_digits=13, decimal_places=3, min_value=0, initial=0, required=False)
    refund_mode = forms.ChoiceField(
        choices=[("CASH", "Cash"), ("KNET", "KNET"), ("BANK", "Bank Transfer"), ("CHEQUE", "Cheque"), ("OTHER", "Other")],
        required=False,
    )
