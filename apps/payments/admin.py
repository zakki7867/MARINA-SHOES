from django.contrib import admin
from .models import CustomerPayment, PaymentAllocation

admin.site.register(CustomerPayment)
admin.site.register(PaymentAllocation)
