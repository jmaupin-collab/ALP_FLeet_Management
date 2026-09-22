"""File storage abstraction. Local disk, Railway Volume, or S3/R2 via environment variables."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.config import BACKEND_ROOT, get_settings

UPLOAD_ROOT = BACKEND_ROOT / "uploads"

ALLOWED_DOCUMENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/tiff",
    "text/plain",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/tiff": ".tiff",
    "text/plain": ".txt",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

MAX_DOCUMENT_BYTES = 15 * 1024 * 1024


class LocalFileStorage:
    """Stores files under a local directory. Used for development and Railway Volume."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or UPLOAD_ROOT

    def save_bytes(self, namespace: str, data: bytes, content_type: str) -> str:
        ext = EXTENSIONS.get(content_type, "")
        relative = Path(namespace) / f"{uuid4()}{ext}"
        dest = self.root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return str(relative).replace("\\", "/")

    def resolve(self, storage_key: str) -> Path:
        path = Path(storage_key)
        if path.is_absolute():
            if not path.is_file():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
            return path
        dest = self.root / storage_key
        if not dest.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        return dest

    def delete(self, storage_key: str) -> None:
        try:
            path = self.resolve(storage_key)
        except HTTPException:
            return
        path.unlink(missing_ok=True)

    def as_response(self, storage_key: str, media_type: str, filename: str):
        path = self.resolve(storage_key)
        return FileResponse(path, media_type=media_type, filename=filename)


class S3FileStorage:
    """S3-compatible object storage (AWS S3 or Cloudflare R2). Credentials come from env only."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.s3_bucket or not settings.s3_access_key_id or not settings.s3_secret_access_key:
            raise RuntimeError(
                "STORAGE_BACKEND=s3 requires S3_BUCKET, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY"
            )
        import boto3

        kwargs = {
            "aws_access_key_id": settings.s3_access_key_id,
            "aws_secret_access_key": settings.s3_secret_access_key,
            "region_name": settings.s3_region,
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        self.client = boto3.client("s3", **kwargs)
        self.bucket = settings.s3_bucket
        self.prefix = (settings.s3_prefix or "fleet-uploads").strip("/")
        self._local_fallback = LocalFileStorage()

    def _key(self, storage_key: str) -> str:
        if storage_key.startswith(self.prefix + "/"):
            return storage_key
        return f"{self.prefix}/{storage_key}".replace("\\", "/")

    def save_bytes(self, namespace: str, data: bytes, content_type: str) -> str:
        ext = EXTENSIONS.get(content_type, "")
        relative = f"{namespace}/{uuid4()}{ext}".replace("\\", "/")
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(relative),
            Body=data,
            ContentType=content_type,
        )
        return relative

    def resolve(self, storage_key: str) -> Path:
        path = Path(storage_key)
        if path.is_absolute() and path.is_file():
            return path
        local = self._local_fallback.root / storage_key
        if local.is_file():
            return local
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    def delete(self, storage_key: str) -> None:
        path = Path(storage_key)
        if path.is_absolute():
            path.unlink(missing_ok=True)
            return
        local = self._local_fallback.root / storage_key
        if local.is_file():
            local.unlink(missing_ok=True)
            return
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._key(storage_key))
        except Exception:
            return

    def as_response(self, storage_key: str, media_type: str, filename: str):
        path = Path(storage_key)
        if path.is_absolute() and path.is_file():
            return FileResponse(path, media_type=media_type, filename=filename)
        local = self._local_fallback.root / storage_key
        if local.is_file():
            return FileResponse(local, media_type=media_type, filename=filename)
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._key(storage_key))
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found") from exc
        return StreamingResponse(
            BytesIO(obj["Body"].read()),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


_storage = None


def build_storage():
    settings = get_settings()
    backend = (settings.storage_backend or "local").strip().lower()
    if backend in {"local", "volume"}:
        root = Path(settings.storage_root) if settings.storage_root else UPLOAD_ROOT
        return LocalFileStorage(root)
    if backend in {"s3", "r2"}:
        return S3FileStorage()
    raise RuntimeError(f"Unsupported STORAGE_BACKEND={backend}. Use local, volume, s3, or r2.")


def get_storage():
    global _storage
    if _storage is None:
        _storage = build_storage()
    return _storage


class _StorageProxy:
    def __getattr__(self, name):
        return getattr(get_storage(), name)


storage = _StorageProxy()


def read_upload(upload: UploadFile, *, allowed_types: set[str], max_bytes: int) -> tuple[bytes, str]:
    content_type = (upload.content_type or "").lower().split(";")[0].strip()
    if content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type.",
        )
    data = upload.file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File must be {max_bytes // (1024 * 1024)} MB or smaller.",
        )
    return data, content_type
