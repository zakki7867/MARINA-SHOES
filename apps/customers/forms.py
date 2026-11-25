from django import forms
from .models import Customer


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "name",
            "customer_type",
            "phone",
            "whatsapp_number",
            "email",
            "gstin",
            "state_code",
            "city",
            "address",
            "shipping_address",
            "credit_limit",
            "payment_terms_days",
            "opening_balance",
            "notes",
            "is_active",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
            "shipping_address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
