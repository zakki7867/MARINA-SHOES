from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction

THREE_PLACES = Decimal("0.001")


def money_round(value) -> Decimal:
    """Round monetary amounts to 3 decimal places (Kuwaiti Dinar fils)."""
    if value is None:
        return Decimal("0.000")
    return Decimal(str(value)).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)


def get_financial_year(on_date: date | None = None, start_month: int = 4) -> str:
    """Return financial year string (e.g., '2026-27') for a given date."""
    target = on_date or date.today()
    if target.month >= start_month:
        start_year = target.year
    else:
        start_year = target.year - 1
    end_year_short = str(start_year + 1)[-2:]
    return f"{start_year}-{end_year_short}"


def get_client_ip(request) -> str | None:
    """Extract client IP address from Django request."""
    if not request:
        return None
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@transaction.atomic
def next_sequence_number(prefix: str, model_cls=None, width: int = 5) -> str:
    """Generate unique concurrency-safe sequential document numbers using select_for_update."""
    from .models import DocumentSequence

    _, created = DocumentSequence.objects.get_or_create(
        prefix=prefix,
        defaults={"last_number": 0},
    )
    seq = DocumentSequence.objects.select_for_update().get(prefix=prefix)
    if created and model_cls is not None:
        last_obj = model_cls.objects.order_by("-id").first()
        if last_obj is not None:
            seq.last_number = max(seq.last_number, last_obj.id)
    seq.last_number += 1
    seq.save(update_fields=["last_number"])
    return f"{prefix}-{seq.last_number:0{width}d}"
