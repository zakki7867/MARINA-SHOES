from django.urls import path
from .views import StoreLocationListCreateView, StoreLocationToggleActiveView, StoreSettingsView

app_name = "core"

urlpatterns = [
    path("settings/", StoreSettingsView.as_view(), name="store_settings"),
    path("locations/", StoreLocationListCreateView.as_view(), name="locations"),
    path("locations/<int:pk>/toggle/", StoreLocationToggleActiveView.as_view(), name="location_toggle"),
]
