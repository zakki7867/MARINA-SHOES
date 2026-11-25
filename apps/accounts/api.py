from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from .models import User
from .permissions import HasRolePermission
from .serializers import UserSerializer


class CurrentUserAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("username")
    serializer_class = UserSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = (User.Role.OWNER,)
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def perform_create(self, serializer):
        user_obj = serializer.save()
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            instance=user_obj,
            changes={"role": user_obj.role},
            request=self.request,
            notes="Created user via API",
        )

    def perform_update(self, serializer):
        old_role = serializer.instance.role
        user_obj = serializer.save()
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=user_obj,
            changes={"role": [old_role, user_obj.role], "is_active": user_obj.is_active},
            request=self.request,
            notes="Updated user via API",
        )
