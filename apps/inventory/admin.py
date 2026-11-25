from django.contrib import admin
from .models import Brand, Category, Product, ProductVariant, StockBalance, StockCount, StockMovement, StockTransfer

admin.site.register(Category)
admin.site.register(Brand)
admin.site.register(Product)
admin.site.register(ProductVariant)
admin.site.register(StockBalance)
admin.site.register(StockTransfer)
admin.site.register(StockCount)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "location", "variant", "movement_type", "signed_quantity", "reference", "user")
    list_filter = ("movement_type", "location")
    readonly_fields = ("variant", "location", "movement_type", "direction", "quantity", "signed_quantity", "reference", "reason", "notes", "user", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
