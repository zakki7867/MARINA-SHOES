from django.contrib import admin
from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "customer_type", "phone", "state_code", "credit_limit", "is_deleted")
    list_filter = ("customer_type", "is_deleted")
    search_fields = ("name", "phone", "gstin")
