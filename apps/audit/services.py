from decimal import Decimal
from apps.core.utils import get_client_ip
from .models import AuditLog


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def log_audit(user, action: str, instance, changes: dict | None = None, request=None, notes: str = "") -> AuditLog:
    """Create an AuditLog entry safely converting Decimals for JSON storage."""
    return AuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action,
        model_name=instance.__class__.__name__,
        object_id=str(getattr(instance, "pk", "")),
        object_repr=str(instance)[:255],
        changes=_json_safe(changes or {}),
        ip_address=get_client_ip(request),
        notes=notes,
    )
