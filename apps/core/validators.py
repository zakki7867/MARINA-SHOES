from pathlib import Path
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_RECEIPT_BYTES = 10 * 1024 * 1024


def validate_image_upload(file):
    """Validate uploaded product images by extension, size, and Pillow integrity check."""
    if not file:
        return
    ext = Path(file.name).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError("Unsupported image format. Allowed: JPG, JPEG, PNG, WEBP.")
    if file.size > MAX_IMAGE_BYTES:
        raise ValidationError("Image file size cannot exceed 5 MB.")
    try:
        pos = file.tell()
        img = Image.open(file)
        img.verify()
        file.seek(pos)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("Uploaded file is not a valid image.") from exc


def validate_receipt_upload(file):
    """Validate expense receipt uploads (image or PDF, max 10 MB)."""
    if not file:
        return
    ext = Path(file.name).suffix.lower()
    if ext not in ALLOWED_RECEIPT_EXTENSIONS:
        raise ValidationError("Unsupported receipt format. Allowed: JPG, PNG, WEBP, PDF.")
    if file.size > MAX_RECEIPT_BYTES:
        raise ValidationError("Receipt file size cannot exceed 10 MB.")
    if ext != ".pdf":
        validate_image_upload(file)
