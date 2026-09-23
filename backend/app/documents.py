"""Private asset document service. Management roles only — no customer/technician metadata."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.deps import require_fleet_admin
from app.database import get_db
from app.models import Asset, AssetDocument, DocumentType, User
from app.rbac import require_asset_access
from app.storage import ALLOWED_DOCUMENT_TYPES, MAX_DOCUMENT_BYTES, read_upload, storage

router = APIRouter(tags=["asset-documents"])

DOCUMENT_LABELS = {
    DocumentType.REGISTRATION: "Registration",
    DocumentType.INSURANCE: "Insurance",
    DocumentType.TITLE: "Title",
    DocumentType.PURCHASE_INVOICE: "Purchase Invoice",
    DocumentType.WARRANTY: "Warranty",
    DocumentType.PERMIT: "Permit",
    DocumentType.SERVICE_RECORD: "Service Record",
    DocumentType.OTHER: "Other",
}


DEFAULT_REMINDER_DAYS = 30
MAX_REMINDER_DAYS = 365


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _parse_reminder_days(value: object) -> int:
    """Clamp rather than reject: a bad lead time should not block a filing."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_REMINDER_DAYS
    return max(0, min(days, MAX_REMINDER_DAYS))


def serialize_document(row: AssetDocument) -> dict:
    today = date.today()
    expiration = row.expiration_date
    days_to_expire = (expiration - today).days if expiration else None
    reminder_days = row.reminder_days if row.reminder_days is not None else DEFAULT_REMINDER_DAYS
    return {
        "id": row.id,
        "asset_id": row.asset_id,
        "organization_id": row.organization_id,
        "document_type": row.document_type.value,
        "document_type_label": DOCUMENT_LABELS.get(row.document_type, row.document_type.value),
        "title": row.title,
        "original_filename": row.original_filename,
        "content_type": row.content_type,
        "file_size": row.file_size,
        "issue_date": row.issue_date.isoformat() if row.issue_date else None,
        "expiration_date": expiration.isoformat() if expiration else None,
        "reminder_days": reminder_days,
        "notes": row.notes,
        "uploaded_by_user_id": row.uploaded_by_user_id,
        "uploaded_at": row.uploaded_at,
        "days_to_expire": days_to_expire,
        "expiration_state": (
            "expired" if days_to_expire is not None and days_to_expire < 0 else
            "soon" if days_to_expire is not None and days_to_expire <= reminder_days else
            "ok"
        ),
    }


def get_managed_document(db: Session, user: User, document_id: UUID) -> AssetDocument:
    row = db.get(AssetDocument, document_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    require_asset_access(db, user, row.asset_id)
    if user.role.value != "system_admin" and row.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return row


@router.get("/assets/{asset_id}/documents")
def list_asset_documents(
    asset_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    asset = require_asset_access(db, current_user, asset_id)
    rows = db.scalars(
        select(AssetDocument)
        .where(AssetDocument.asset_id == asset.id)
        .where(AssetDocument.organization_id == asset.organization_id)
        .order_by(AssetDocument.uploaded_at.desc())
    ).all()
    return [serialize_document(row) for row in rows]


@router.post("/assets/{asset_id}/documents", status_code=status.HTTP_201_CREATED)
def upload_asset_document(
    asset_id: UUID,
    document_type: str = Form(...),
    title: str = Form(...),
    issue_date: str | None = Form(default=None),
    expiration_date: str | None = Form(default=None),
    reminder_days: int = Form(default=DEFAULT_REMINDER_DAYS),
    notes: str | None = Form(default=None),
    file: UploadFile = File(...),
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    asset = require_asset_access(db, current_user, asset_id)
    try:
        dtype = DocumentType(document_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document type") from exc
    data, content_type = read_upload(file, allowed_types=ALLOWED_DOCUMENT_TYPES, max_bytes=MAX_DOCUMENT_BYTES)
    storage_key = storage.save_bytes(f"asset_documents/{asset.organization_id}/{asset.id}", data, content_type)
    row = AssetDocument(
        organization_id=asset.organization_id,
        asset_id=asset.id,
        document_type=dtype,
        title=title.strip()[:255],
        original_filename=(file.filename or "document")[:255],
        storage_key=storage_key,
        content_type=content_type,
        file_size=len(data),
        issue_date=_parse_date(issue_date),
        expiration_date=_parse_date(expiration_date),
        reminder_days=_parse_reminder_days(reminder_days),
        notes=notes,
        uploaded_by_user_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_document(row)


@router.patch("/documents/{document_id}")
def update_asset_document(
    document_id: UUID,
    payload: dict,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = get_managed_document(db, current_user, document_id)
    if "title" in payload and payload["title"]:
        row.title = str(payload["title"]).strip()[:255]
    if "notes" in payload:
        row.notes = payload["notes"]
    if "document_type" in payload and payload["document_type"]:
        try:
            row.document_type = DocumentType(payload["document_type"])
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document type") from exc
    if "issue_date" in payload:
        row.issue_date = _parse_date(payload["issue_date"]) if payload["issue_date"] else None
    if "expiration_date" in payload:
        row.expiration_date = _parse_date(payload["expiration_date"]) if payload["expiration_date"] else None
    if "reminder_days" in payload:
        row.reminder_days = _parse_reminder_days(payload["reminder_days"])
    db.commit()
    db.refresh(row)
    return serialize_document(row)


# These are the records people actually chase down at renewal or audit time.
# Reported as gaps in the folder view; nothing is enforced, because an ALPR
# trailer legitimately has no insurance card.
CORE_DOCUMENT_TYPES = (DocumentType.REGISTRATION, DocumentType.INSURANCE, DocumentType.TITLE)


def _asset_summary(asset: Asset) -> dict:
    return {
        "asset_id": asset.id,
        "vin": asset.vin,
        "license_plate": asset.license_plate,
        "license_plate_state": asset.license_plate_state,
        "make_model": asset.make_model,
        "asset_type": asset.asset_type.value,
        "is_archived": asset.is_archived,
    }


def _visible_assets(db: Session, user: User) -> list[Asset]:
    stmt = select(Asset).where(Asset.organization_id == user.organization_id)
    return list(db.scalars(stmt.order_by(Asset.vin)).all())


@router.get("/documents")
def list_all_documents(
    state: str | None = Query(default=None, pattern="^(expiring|expired|ok|no_expiry)$"),
    document_type: str | None = None,
    asset_id: UUID | None = None,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Every document in the organization, with its asset attached.

    This is the fleet-wide folder: one place to see what is on file and what is
    about to lapse, instead of opening each vehicle in turn.
    """
    stmt = (
        select(AssetDocument)
        .options(selectinload(AssetDocument.asset))
        .where(AssetDocument.organization_id == current_user.organization_id)
    )
    if asset_id:
        require_asset_access(db, current_user, asset_id)
        stmt = stmt.where(AssetDocument.asset_id == asset_id)
    if document_type:
        try:
            stmt = stmt.where(AssetDocument.document_type == DocumentType(document_type))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document type"
            ) from exc

    rows = db.scalars(stmt.order_by(AssetDocument.expiration_date.is_(None), AssetDocument.expiration_date)).all()
    out = []
    for row in rows:
        data = serialize_document(row)
        if state == "no_expiry" and row.expiration_date is not None:
            continue
        if state in {"expiring", "expired", "ok"} and data["expiration_state"] != (
            "soon" if state == "expiring" else state
        ):
            continue
        data["asset"] = _asset_summary(row.asset) if row.asset else None
        out.append(data)
    return out


@router.get("/documents/summary")
def documents_summary(
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Counts plus, per asset, which core documents are missing or lapsing."""
    assets = _visible_assets(db, current_user)
    documents = db.scalars(
        select(AssetDocument).where(AssetDocument.organization_id == current_user.organization_id)
    ).all()

    by_asset: dict[UUID, list[AssetDocument]] = {}
    for row in documents:
        by_asset.setdefault(row.asset_id, []).append(row)

    counts = {"total": len(documents), "expired": 0, "expiring": 0, "ok": 0, "no_expiry": 0}
    for row in documents:
        state = serialize_document(row)["expiration_state"]
        if row.expiration_date is None:
            counts["no_expiry"] += 1
        elif state == "expired":
            counts["expired"] += 1
        elif state == "soon":
            counts["expiring"] += 1
        else:
            counts["ok"] += 1

    gaps = []
    for asset in assets:
        if asset.is_archived:
            continue
        held = {row.document_type for row in by_asset.get(asset.id, [])}
        missing = [t.value for t in CORE_DOCUMENT_TYPES if t not in held]
        if missing:
            gaps.append({**_asset_summary(asset), "missing": missing})

    return {
        "counts": counts,
        "assets_with_documents": len(by_asset),
        "assets_total": sum(1 for a in assets if not a.is_archived),
        "missing_core_documents": gaps,
    }


@router.get("/documents/{document_id}")
def get_asset_document(
    document_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    return serialize_document(get_managed_document(db, current_user, document_id))


@router.get("/documents/{document_id}/file")
def download_asset_document(
    document_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
):
    row = get_managed_document(db, current_user, document_id)
    return storage.as_response(row.storage_key, row.content_type, row.original_filename)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset_document(
    document_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> None:
    row = get_managed_document(db, current_user, document_id)
    storage.delete(row.storage_key)
    db.delete(row)
    db.commit()
