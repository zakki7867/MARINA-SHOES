import io
import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.core.models import StoreLocation
from apps.core.validators import validate_image_upload, validate_receipt_upload


def test_upload_validators_reject_invalid_files():
    bad_ext = SimpleUploadedFile("malware.exe", b"MZ...", content_type="application/octet-stream")
    with pytest.raises(ValidationError):
        validate_image_upload(bad_ext)
    with pytest.raises(ValidationError):
        validate_receipt_upload(bad_ext)

    fake_png = SimpleUploadedFile("fake.png", b"not-a-real-image", content_type="image/png")
    with pytest.raises(ValidationError):
        validate_image_upload(fake_png)

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="PNG")
    valid_png = SimpleUploadedFile("shoe.png", buf.getvalue(), content_type="image/png")
    validate_image_upload(valid_png)

    valid_pdf = SimpleUploadedFile("bill.pdf", b"%PDF-1.4", content_type="application/pdf")
    validate_receipt_upload(valid_pdf)


@pytest.mark.django_db
def test_store_location_owner_only(client, owner_user, manager_user):
    client.force_login(manager_user)
    assert client.get(reverse("core:locations")).status_code == 403

    client.force_login(owner_user)
    resp = client.post(
        reverse("core:locations"),
        {"code": "WH2", "name": "Delhi Hub", "location_type": "WAREHOUSE", "state_code": "DL", "is_active": True},
    )
    assert resp.status_code == 302
    assert StoreLocation.objects.filter(code="WH2").exists()
