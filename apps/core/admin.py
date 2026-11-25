from django.contrib import admin
from .models import StoreLocation, StoreSettings

admin.site.register(StoreSettings)
admin.site.register(StoreLocation)
