import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from apps.core.models import StoreSettings

User = get_user_model()


@pytest.fixture
def store_settings(db):
    return StoreSettings.get_settings()


@pytest.fixture
def owner_user(db):
    return User.objects.create_user(username="owner", password="Password123!", role=User.Role.OWNER)


@pytest.fixture
def manager_user(db):
    return User.objects.create_user(username="manager", password="Password123!", role=User.Role.MANAGER)


@pytest.fixture
def billing_user(db):
    return User.objects.create_user(username="billing", password="Password123!", role=User.Role.BILLING_STAFF)


@pytest.fixture
def storekeeper_user(db):
    return User.objects.create_user(username="storekeeper", password="Password123!", role=User.Role.STOREKEEPER)


@pytest.fixture
def api_client():
    return APIClient()
