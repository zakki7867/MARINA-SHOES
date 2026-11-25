from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from rest_framework import serializers, viewsets

from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission, RoleRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from .forms import ExpenseForm
from .models import Expense

FINANCE_ROLES = (User.Role.OWNER, User.Role.MANAGER)


class ExpenseListCreateView(RoleRequiredMixin, View):
    allowed_roles = FINANCE_ROLES
    template_name = "expenses/expense_list.html"

    def get(self, request):
        form = ExpenseForm(initial={"expense_date": timezone.localdate()})
        qs = Expense.objects.select_related("created_by", "location").all()
        cat = request.GET.get("category", "").strip()
        if cat:
            qs = qs.filter(category=cat)
        return render(
            request,
            self.template_name,
            {"form": form, "expenses": qs[:100], "categories": Expense.Category.choices},
        )

    def post(self, request):
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            exp = form.save(commit=False)
            exp.created_by = request.user
            exp.save()
            log_audit(
                user=request.user,
                action=AuditLog.Action.CREATE,
                instance=exp,
                changes={"category": exp.category, "amount": exp.amount, "mode": exp.payment_mode},
                request=request,
                notes="Recorded operating expense",
            )
            messages.success(request, "Expense recorded.")
            return redirect("expenses:expense_list")
        qs = Expense.objects.select_related("created_by", "location").all()[:100]
        return render(
            request,
            self.template_name,
            {"form": form, "expenses": qs, "categories": Expense.Category.choices},
        )


class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = ["id", "expense_date", "location", "category", "amount", "payment_mode", "reference_no", "notes"]


class ExpenseViewSet(viewsets.ModelViewSet):
    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer
    permission_classes = [HasRolePermission]
    allowed_roles_by_method = {
        "GET": FINANCE_ROLES,
        "HEAD": FINANCE_ROLES,
        "OPTIONS": FINANCE_ROLES,
        "POST": FINANCE_ROLES,
        "PUT": FINANCE_ROLES,
        "PATCH": FINANCE_ROLES,
        "DELETE": (User.Role.OWNER,),
    }

    def perform_create(self, serializer):
        exp = serializer.save(created_by=self.request.user)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            instance=exp,
            changes={"category": exp.category, "amount": exp.amount},
            request=self.request,
            notes="Recorded expense via API",
        )

    def perform_update(self, serializer):
        exp = serializer.save()
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=exp,
            changes={"category": exp.category, "amount": exp.amount},
            request=self.request,
            notes="Updated expense via API",
        )

    def perform_destroy(self, instance):
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.DELETE,
            instance=instance,
            changes={"category": instance.category, "amount": instance.amount},
            request=self.request,
            notes="Deleted expense via API",
        )
        instance.delete()
