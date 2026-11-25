from django import forms
from django.forms import formset_factory
from apps.core.models import StoreLocation
from apps.inventory.models import ProductVariant
from .models import GoodsReceipt, PurchaseOrder, Supplier, SupplierPayment


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name",
            "contact_person",
            "phone",
            "email",
            "gstin",
            "state_code",
            "address",
            "opening_balance",
            "payment_terms_days",
            "notes",
            "is_active",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["supplier", "location", "order_date", "expected_date", "notes"]
        widgets = {
            "order_date": forms.DateInput(attrs={"type": "date"}),
            "expected_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class PurchaseLineItemForm(forms.Form):
    variant = forms.ModelChoiceField(
        queryset=ProductVariant.objects.select_related("product", "product__brand").order_by("product__name", "size")
    )
    quantity = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={"min": "1", "placeholder": "1"}))
    unit_cost = forms.DecimalField(max_digits=13, decimal_places=3, min_value=0, widget=forms.NumberInput(attrs={"step": "0.001", "min": "0", "placeholder": "0.000"}))
    discount_amount = forms.DecimalField(max_digits=13, decimal_places=3, min_value=0, required=False, widget=forms.NumberInput(attrs={"step": "0.001", "min": "0", "placeholder": "0.000"}))
    tax_rate = forms.DecimalField(max_digits=5, decimal_places=2, min_value=0, required=False, widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0"}))
    damaged_quantity = forms.IntegerField(min_value=0, required=False, widget=forms.NumberInput(attrs={"min": "0", "placeholder": "0"}))
    missing_quantity = forms.IntegerField(min_value=0, required=False, widget=forms.NumberInput(attrs={"min": "0", "placeholder": "0"}))


PurchaseLineItemFormSet = formset_factory(PurchaseLineItemForm, extra=1, min_num=1, validate_min=True)


class GoodsReceiptForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.all())
    location = forms.ModelChoiceField(queryset=StoreLocation.objects.filter(is_active=True))
    purchase_order = forms.ModelChoiceField(queryset=PurchaseOrder.objects.all(), required=False)
    received_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    supplier_invoice_no = forms.CharField(max_length=60, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class SupplierPaymentForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.all())
    goods_receipt = forms.ModelChoiceField(queryset=GoodsReceipt.objects.select_related("supplier"), required=False)
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(
        max_digits=13,
        decimal_places=3,
        min_value=0.001,
        widget=forms.NumberInput(attrs={"step": "0.001", "min": "0.001", "placeholder": "0.000"}),
    )
    payment_mode = forms.ChoiceField(choices=SupplierPayment.PaymentMode.choices)
    reference_no = forms.CharField(max_length=80, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
