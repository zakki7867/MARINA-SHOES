from django.contrib import admin
from .models import SalesOrder, SalesReturn

admin.site.register(SalesOrder)
admin.site.register(SalesReturn)
