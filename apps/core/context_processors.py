from django.db.utils import OperationalError, ProgrammingError
from .models import StoreLocation, StoreSettings


def store_context(request):
    """Inject store settings and default location into all template contexts safely."""
    try:
        store = StoreSettings.get_settings()
        default_loc = StoreLocation.get_default()
    except (OperationalError, ProgrammingError):
        store = None
        default_loc = None
    return {"store_settings": store, "default_location": default_loc}
