from django import forms
from apps.core.models import StoreLocation
from .models import Expense


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["expense_date", "location", "category", "amount", "payment_mode", "reference_no", "notes", "receipt_attachment"]
        widgets = {
            "expense_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = StoreLocation.objects.filter(is_active=True)
        self.fields["location"].initial = StoreLocation.get_default()
