from django.contrib import admin
from .models import CreditNote, FinancialYearSequence, Invoice

admin.site.register(FinancialYearSequence)
admin.site.register(Invoice)
admin.site.register(CreditNote)
