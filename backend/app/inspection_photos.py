from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status

from app.models import InspectionPhoto
from app.storage import storage

CUSTOMER_PHOTO_LIMIT = 4
MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALLOWED_PHOTO_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def _safe_content_type(upload: UploadFile) -> str:
    content_type = (upload.content_type or "").lower().split(";")[0].strip()
    if content_type not in ALLOWED_PHOTO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photos must be JPEG, PNG, WebP, or HEIC.",
        )
    return content_type


def save_customer_photos(inspection_id: UUID, uploads: list[UploadFile], user_id: UUID | None) -> list[InspectionPhoto]:
    if len(uploads) > CUSTOMER_PHOTO_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Customers can submit at most {CUSTOMER_PHOTO_LIMIT} photos.",
        )
    photos: list[InspectionPhoto] = []
    for upload in uploads:
        if not upload.filename:
            continue
        content_type = _safe_content_type(upload)
        data = upload.file.read()
        if not data:
            continue
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Each photo must be 8 MB or smaller.",
            )
        photo_id = uuid4()
        storage_key = storage.save_bytes(f"inspection_photos/{inspection_id}", data, content_type)
        photos.append(
            InspectionPhoto(
                id=photo_id,
                inspection_id=inspection_id,
                original_filename=upload.filename[:255],
                stored_path=storage_key,
                content_type=content_type,
                created_by_id=user_id,
            )
        )
    if len(photos) > CUSTOMER_PHOTO_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Customers can submit at most {CUSTOMER_PHOTO_LIMIT} photos.",
        )
    return photos


def resolve_photo_file(photo: InspectionPhoto) -> Path:
    return storage.resolve(photo.stored_path)


def photo_response(photo: InspectionPhoto):
    return storage.as_response(photo.stored_path, photo.content_type, photo.original_filename)
