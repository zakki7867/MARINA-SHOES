from django import forms
from apps.customers.models import Customer
from apps.invoicing.models import Invoice
from .models import CustomerPayment


class CustomerPaymentForm(forms.Form):
    customer = forms.ModelChoiceField(queryset=Customer.objects.all())
    invoice = forms.ModelChoiceField(
        queryset=Invoice.objects.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL]).select_related("customer"),
        required=False,
        help_text="Optional: select an invoice, or leave blank to auto-allocate oldest open invoices.",
    )
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(
        max_digits=13,
        decimal_places=3,
        min_value=0.001,
        widget=forms.NumberInput(attrs={"step": "0.001", "min": "0.001", "placeholder": "0.000"}),
    )
    payment_mode = forms.ChoiceField(choices=CustomerPayment.PaymentMode.choices)
    is_advance = forms.BooleanField(required=False, label="Keep as unallocated customer advance")
    reference_no = forms.CharField(max_length=80, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
