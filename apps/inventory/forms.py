from django import forms
from django.forms import formset_factory
from apps.core.models import StoreLocation
from .models import Brand, Category, Product, ProductVariant, StockMovement


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]


class BrandForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = ["name"]


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "style_code",
            "category",
            "brand",
            "gender",
            "hsn_code",
            "tax_rate",
            "unit",
            "image",
            "status",
            "description",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class ProductVariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = [
            "size",
            "color",
            "sku",
            "barcode",
            "cost_price",
            "wholesale_price",
            "retail_price",
            "gst_rate",
            "reorder_level",
            "is_active",
        ]

    def clean_barcode(self):
        barcode = self.cleaned_data.get("barcode")
        return barcode.strip() if barcode else None


class StockMovementForm(forms.Form):
    location = forms.ModelChoiceField(queryset=StoreLocation.objects.filter(is_active=True))
    variant = forms.ModelChoiceField(queryset=ProductVariant.objects.select_related("product", "product__brand"))
    movement_type = forms.ChoiceField(
        choices=[
            (StockMovement.MovementType.ADJUSTMENT, "Adjustment (+/-)"),
            (StockMovement.MovementType.OPENING_STOCK, "Opening Stock (+)"),
            (StockMovement.MovementType.DAMAGE, "Damage (-)"),
            (StockMovement.MovementType.PURCHASE, "Direct Stock In (+)"),
            (StockMovement.MovementType.SALE_RETURN, "Sale Return (+)"),
            (StockMovement.MovementType.PURCHASE_RETURN, "Purchase Return (-)"),
        ]
    )
    direction = forms.ChoiceField(
        choices=StockMovement.Direction.choices,
        required=False,
        help_text="Used for Adjustment (+/-); automatic for other types.",
    )
    quantity = forms.IntegerField(min_value=1)
    reason = forms.CharField(max_length=120, required=False)
    reference = forms.CharField(max_length=120, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class StockTransferForm(forms.Form):
    from_location = forms.ModelChoiceField(queryset=StoreLocation.objects.filter(is_active=True))
    to_location = forms.ModelChoiceField(queryset=StoreLocation.objects.filter(is_active=True))
    transfer_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class TransferLineForm(forms.Form):
    variant = forms.ModelChoiceField(queryset=ProductVariant.objects.select_related("product", "product__brand"))
    quantity = forms.IntegerField(min_value=1)


TransferLineFormSet = formset_factory(TransferLineForm, extra=1, min_num=1, validate_min=True)


class StockCountForm(forms.Form):
    location = forms.ModelChoiceField(queryset=StoreLocation.objects.filter(is_active=True))
    count_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class CountLineForm(forms.Form):
    variant = forms.ModelChoiceField(queryset=ProductVariant.objects.select_related("product", "product__brand"))
    counted_quantity = forms.IntegerField(min_value=0)


CountLineFormSet = formset_factory(CountLineForm, extra=1, min_num=1, validate_min=True)


class ProductImportForm(forms.Form):
    file = forms.FileField(help_text="Upload .csv or .xlsx with columns: Product, Brand, Category, HSN, GST%, Size, Color, SKU, Barcode, Cost, Wholesale, Retail, Reorder Level, Opening Stock")
