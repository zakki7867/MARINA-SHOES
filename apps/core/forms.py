from django import forms
from .models import StoreLocation, StoreSettings


class StoreSettingsForm(forms.ModelForm):
    class Meta:
        model = StoreSettings
        fields = [
            "store_name",
            "tagline",
            "gstin",
            "state_code",
            "address",
            "phone",
            "email",
            "currency_code",
            "currency_symbol",
            "default_tax_mode",
            "allow_negative_stock",
            "financial_year_start_month",
            "invoice_prefix",
            "invoice_number_format",
            "invoice_terms",
            "default_credit_days",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
            "invoice_terms": forms.Textarea(attrs={"rows": 2}),
        }


class StoreLocationForm(forms.ModelForm):
    class Meta:
        model = StoreLocation
        fields = ["code", "name", "location_type", "state_code", "phone", "address", "is_default", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}
