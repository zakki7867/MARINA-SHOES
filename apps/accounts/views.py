from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.core.cache import cache
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from apps.core.utils import get_client_ip
from .forms import LoginForm, StaffUserCreateForm, StaffUserUpdateForm
from .models import User
from .permissions import RoleRequiredMixin

MAX_LOGIN_ATTEMPTS = 5
MAX_USERNAME_LOGIN_ATTEMPTS = 10
LOGIN_LOCKOUT_SECONDS = 300


class UserLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def _cache_keys(self, request, username: str) -> tuple[str, str]:
        ip = get_client_ip(request) or "unknown"
        uname = username.strip().lower()
        return f"login_fail:{ip}:{uname}", f"login_fail_user:{uname}"

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "")
        ip_key, user_key = self._cache_keys(request, username)
        if cache.get(ip_key, 0) >= MAX_LOGIN_ATTEMPTS or cache.get(user_key, 0) >= MAX_USERNAME_LOGIN_ATTEMPTS:
            form = self.get_form()
            form.add_error(None, "Too many failed login attempts. Please wait 5 minutes and try again.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        ip_key, user_key = self._cache_keys(self.request, form.get_user().username)
        cache.delete_many([ip_key, user_key])
        return super().form_valid(form)

    def form_invalid(self, form):
        username = self.request.POST.get("username", "")
        ip_key, user_key = self._cache_keys(self.request, username)
        cache.set(ip_key, cache.get(ip_key, 0) + 1, timeout=LOGIN_LOCKOUT_SECONDS)
        cache.set(user_key, cache.get(user_key, 0) + 1, timeout=LOGIN_LOCKOUT_SECONDS)
        return super().form_invalid(form)


class UserLogoutView(LogoutView):
    next_page = reverse_lazy("accounts:login")


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/profile.html"


class UserListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.OWNER,)
    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 25


class UserCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (User.Role.OWNER,)
    model = User
    form_class = StaffUserCreateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            instance=self.object,
            changes={"role": self.object.role},
            request=self.request,
            notes="Created staff user",
        )
        messages.success(self.request, f"User '{self.object.username}' created.")
        return response


class UserUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (User.Role.OWNER,)
    model = User
    form_class = StaffUserUpdateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def form_valid(self, form):
        old_role = self.get_object().role
        response = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=self.object,
            changes={"role": [old_role, self.object.role], "is_active": self.object.is_active},
            request=self.request,
            notes="Updated staff user",
        )
        messages.success(self.request, f"User '{self.object.username}' updated.")
        return response
