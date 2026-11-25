from functools import wraps
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


class RoleRequiredMixin(LoginRequiredMixin):
    """Enforce authentication and role membership on class-based views."""

    allowed_roles: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), self.get_login_url(), self.get_redirect_field_name())
        if self.allowed_roles and not request.user.has_any_role(*self.allowed_roles):
            raise PermissionDenied("You do not have permission to access this module.")
        return super().dispatch(request, *args, **kwargs)


def role_required(*roles: str):
    """Enforce authentication and role membership on function-based views."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if roles and not request.user.has_any_role(*roles):
                raise PermissionDenied("You do not have permission to access this module.")
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


class HasRolePermission(BasePermission):
    """DRF permission checking `view.allowed_roles` or method-specific `allowed_roles_by_method`."""

    message = "Your role does not permit this action."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        method_roles = getattr(view, "allowed_roles_by_method", None)
        if method_roles and request.method in method_roles:
            return user.has_any_role(*method_roles[request.method])

        allowed_roles = getattr(view, "allowed_roles", None)
        if allowed_roles is None:
            return True
        return user.has_any_role(*allowed_roles)
