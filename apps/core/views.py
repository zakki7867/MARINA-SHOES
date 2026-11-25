from django.contrib import messages
from django.db import connections
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from .forms import StoreLocationForm, StoreSettingsForm
from .models import StoreLocation, StoreSettings


class StoreSettingsView(RoleRequiredMixin, View):
    """Owner-only view for configuring store info, tax state, GST mode, and stock rules."""

    allowed_roles = (User.Role.OWNER,)
    template_name = "core/store_settings.html"

    def get(self, request):
        settings_obj = StoreSettings.get_settings()
        form = StoreSettingsForm(instance=settings_obj)
        return render(request, self.template_name, {"form": form, "settings_obj": settings_obj})

    def post(self, request):
        settings_obj = StoreSettings.get_settings()
        old_allow_negative = settings_obj.allow_negative_stock
        old_tax_mode = settings_obj.default_tax_mode
        form = StoreSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            updated = form.save()
            log_audit(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                instance=updated,
                changes={
                    "allow_negative_stock": [old_allow_negative, updated.allow_negative_stock],
                    "default_tax_mode": [old_tax_mode, updated.default_tax_mode],
                },
                request=request,
                notes="Updated store settings",
            )
            messages.success(request, "Store settings saved successfully.")
            return redirect("core:store_settings")
        return render(request, self.template_name, {"form": form, "settings_obj": settings_obj})


class StoreLocationListCreateView(RoleRequiredMixin, View):
    """Owner-only management for stores and warehouses."""

    allowed_roles = (User.Role.OWNER,)
    template_name = "core/locations.html"

    def get(self, request):
        locations = StoreLocation.objects.all()
        form = StoreLocationForm()
        return render(request, self.template_name, {"locations": locations, "form": form})

    def post(self, request):
        form = StoreLocationForm(request.POST)
        if form.is_valid():
            loc = form.save()
            log_audit(
                user=request.user,
                action=AuditLog.Action.CREATE,
                instance=loc,
                changes={"code": loc.code, "name": loc.name},
                request=request,
                notes="Added store/warehouse location",
            )
            messages.success(request, f"Location '{loc.name}' added.")
            return redirect("core:locations")
        locations = StoreLocation.objects.all()
        return render(request, self.template_name, {"locations": locations, "form": form})


class StoreLocationToggleActiveView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.OWNER,)

    def post(self, request, pk):
        loc = get_object_or_404(StoreLocation, pk=pk)
        if loc.is_default and loc.is_active:
            messages.error(request, "Cannot deactivate the default location.")
        else:
            loc.is_active = not loc.is_active
            loc.save(update_fields=["is_active", "updated_at"])
            log_audit(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                instance=loc,
                changes={"is_active": loc.is_active},
                request=request,
                notes="Toggled location active status",
            )
            messages.success(request, f"Location '{loc.name}' updated.")
        return redirect("core:locations")


class HealthCheckView(View):
    """Unauthenticated liveness/readiness endpoint verifying database connectivity."""

    def get(self, request):
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return JsonResponse({"status": "ok", "database": "ok"}, status=200)
        except Exception:
            return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
