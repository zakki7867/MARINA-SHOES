from django.db.models import Q
from django.views.generic import ListView
from rest_framework import serializers, viewsets

from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission, RoleRequiredMixin
from .models import AuditLog


class AuditLogListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.OWNER,)
    model = AuditLog
    template_name = "audit/audit_list.html"
    context_object_name = "logs"
    paginate_by = 40

    def get_queryset(self):
        qs = super().get_queryset().select_related("user")
        q = self.request.GET.get("q", "").strip()
        action = self.request.GET.get("action", "").strip()
        model_name = self.request.GET.get("model", "").strip()
        if q:
            qs = qs.filter(
                Q(object_repr__icontains=q)
                | Q(object_id__icontains=q)
                | Q(notes__icontains=q)
                | Q(user__username__icontains=q)
            )
        if action:
            qs = qs.filter(action=action)
        if model_name:
            qs = qs.filter(model_name__iexact=model_name)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["actions"] = AuditLog.Action.choices
        return ctx


class AuditLogSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True, default="")

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "username",
            "action",
            "model_name",
            "object_id",
            "object_repr",
            "changes",
            "ip_address",
            "notes",
            "created_at",
        ]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related("user").all()
    serializer_class = AuditLogSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = (User.Role.OWNER,)
