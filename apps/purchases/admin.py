from django.contrib import admin
from .models import GoodsReceipt, PurchaseOrder, PurchaseReturn, Supplier, SupplierPayment

admin.site.register(Supplier)
admin.site.register(PurchaseOrder)
admin.site.register(GoodsReceipt)
admin.site.register(PurchaseReturn)
admin.site.register(SupplierPayment)
