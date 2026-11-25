from rest_framework import serializers
from apps.accounts.models import User
from .models import Brand, Category, Product, ProductVariant, StockBalance, StockMovement


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug"]
        read_only_fields = ["id", "slug"]


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug"]
        read_only_fields = ["id", "slug"]


class StockBalanceSerializer(serializers.ModelSerializer):
    location_code = serializers.CharField(source="location.code", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = StockBalance
        fields = ["location", "location_code", "location_name", "quantity"]


class ProductVariantSerializer(serializers.ModelSerializer):
    current_stock = serializers.IntegerField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    effective_gst_rate = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    location_balances = StockBalanceSerializer(many=True, read_only=True)

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "product",
            "product_name",
            "size",
            "color",
            "sku",
            "barcode",
            "cost_price",
            "wholesale_price",
            "retail_price",
            "gst_rate",
            "effective_gst_rate",
            "reorder_level",
            "is_active",
            "current_stock",
            "is_low_stock",
            "location_balances",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request and getattr(request.user, "is_authenticated", False):
            if not request.user.has_any_role(User.Role.OWNER, User.Role.MANAGER, User.Role.STOREKEEPER):
                data.pop("cost_price", None)
        return data


class ProductSerializer(serializers.ModelSerializer):
    variants = ProductVariantSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    brand_name = serializers.CharField(source="brand.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "style_code",
            "category",
            "category_name",
            "brand",
            "brand_name",
            "gender",
            "hsn_code",
            "tax_rate",
            "unit",
            "status",
            "description",
            "variants",
        ]


class StockMovementSerializer(serializers.ModelSerializer):
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    location_code = serializers.CharField(source="location.code", read_only=True, default="")
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "variant",
            "variant_sku",
            "location",
            "location_code",
            "movement_type",
            "direction",
            "quantity",
            "signed_quantity",
            "reference",
            "reason",
            "notes",
            "username",
            "created_at",
        ]
        read_only_fields = ["id", "signed_quantity", "username", "created_at"]
