import pytest
from django.urls import reverse
from apps.audit.models import AuditLog


@pytest.mark.django_db
def test_owner_can_access_user_management_and_store_settings(client, owner_user):
    client.force_login(owner_user)
    assert client.get(reverse("accounts:user_list")).status_code == 200
    assert client.get(reverse("core:store_settings")).status_code == 200
    assert client.get(reverse("audit:audit_list")).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("role_fixture", ["manager_user", "billing_user", "storekeeper_user"])
def test_non_owner_blocked_from_admin_views_and_api(client, api_client, request, role_fixture):
    user = request.getfixturevalue(role_fixture)
    client.force_login(user)
    assert client.get(reverse("accounts:user_list")).status_code == 403
    assert client.get(reverse("core:store_settings")).status_code == 403
    assert client.get(reverse("audit:audit_list")).status_code == 403

    api_client.force_authenticate(user=user)
    assert api_client.get("/api/v1/accounts/users/").status_code == 403
    assert api_client.get("/api/v1/audit/").status_code == 403


@pytest.mark.django_db
def test_store_settings_update_creates_audit_log(client, owner_user, store_settings):
    client.force_login(owner_user)
    resp = client.post(
        reverse("core:store_settings"),
        {
            "store_name": "MARINA SHOES INDIA",
            "tagline": "Wholesale & Retail Footwear",
            "gstin": "27ABCDE1234F1Z5",
            "state_code": "MH",
            "address": "Mumbai, Maharashtra",
            "phone": "+919820000000",
            "email": "info@marinashoes.in",
            "currency_code": "INR",
            "currency_symbol": "₹",
            "default_tax_mode": "EXCLUSIVE",
            "allow_negative_stock": True,
            "financial_year_start_month": 4,
            "invoice_prefix": "MS",
            "invoice_number_format": "{prefix}/{fy}/{seq:04d}",
            "invoice_terms": "Standard terms",
            "default_credit_days": 30,
        },
    )
    assert resp.status_code == 302
    store_settings.refresh_from_db()
    assert store_settings.allow_negative_stock is True
    assert AuditLog.objects.filter(model_name="StoreSettings", action=AuditLog.Action.UPDATE).exists()


@pytest.mark.django_db
def test_login_bruteforce_protection_audit_immutability_and_user_api(client, api_client, owner_user):
    from django.core.cache import cache
    from django.core.exceptions import ValidationError
    cache.clear()

    for _ in range(5):
        resp = client.post(reverse("accounts:login"), {"username": "owner", "password": "WrongPassword!"})
        assert resp.status_code == 200

    blocked = client.post(reverse("accounts:login"), {"username": "owner", "password": "Owner1234!"})
    assert blocked.status_code == 429

    # AuditLog is immutable
    log = AuditLog.objects.create(user=owner_user, action=AuditLog.Action.CREATE, model_name="Test", object_id="1", object_repr="Test#1")
    log.notes = "tampered"
    with pytest.raises(ValidationError):
        log.save()
    with pytest.raises(ValidationError):
        log.delete()

    # UserViewSet hashes password on create and blocks DELETE
    api_client.force_authenticate(user=owner_user)
    create_resp = api_client.post(
        "/api/v1/accounts/users/",
        {"username": "newcashier", "role": "BILLING_STAFF", "password": "CashierStrongPassword99!"},
        format="json",
    )
    assert create_resp.status_code == 201
    uid = create_resp.data["id"]
    from django.contrib.auth import get_user_model
    new_u = get_user_model().objects.get(pk=uid)
    assert new_u.check_password("CashierStrongPassword99!")
    assert api_client.delete(f"/api/v1/accounts/users/{uid}/").status_code == 405
