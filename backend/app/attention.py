"""In-app Attention Center with deterministic condition keys."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from time import monotonic
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.deps import require_operator
from app.database import get_db
from app.models import (
    Asset,
    AssetDocument,
    AssetOperationalStatus,
    AttentionItem,
    AttentionSeverity,
    AttentionStatus,
    DocumentType,
    Inspection,
    InspectionItem,
    InspectionResult,
    InspectionStatus,
    InventoryPart,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
    User,
    UserRole,
    WorkOrderStatus,
)
from app.pm import calculate_pm_status, get_latest_meter_reading
from app.rbac import can_access_organization, canonical_role
from app.services import total_repair_cost
from app.utilization import compute_asset_utilization, load_asset_for_utilization

router = APIRouter(tags=["attention"])

WAITING_TOO_LONG_DAYS = 7
DOC_SOON_DAYS = 30
DOCUMENT_KINDS = {"doc_expired", "doc_expiring"}
# Canonical roles only; legacy values resolve through canonical_role().
MANAGERS = frozenset({UserRole.SYSTEM_ADMIN, UserRole.ORG_ADMIN, UserRole.FLEET_MANAGER})


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def serialize_attention(row: AttentionItem) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "condition_key": row.condition_key,
        "kind": row.kind,
        "severity": row.severity.value,
        "title": row.title,
        "description": row.description,
        "asset_id": row.asset_id,
        "work_order_id": row.work_order_id,
        "part_id": row.part_id,
        "document_id": row.document_id,
        "link_path": row.link_path,
        "status": row.status.value,
        "created_at": row.created_at,
        "resolved_at": row.resolved_at,
        "dismissed_at": row.dismissed_at,
    }


def _upsert(
    db: Session,
    existing: dict[str, AttentionItem],
    seen: set[str],
    *,
    organization_id: UUID,
    condition_key: str,
    kind: str,
    severity: AttentionSeverity,
    title: str,
    description: str,
    asset_id: UUID | None = None,
    work_order_id: UUID | None = None,
    part_id: UUID | None = None,
    document_id: UUID | None = None,
    link_path: str | None = None,
) -> None:
    seen.add(condition_key)
    row = existing.get(condition_key)
    if row is None:
        db.add(
            AttentionItem(
                organization_id=organization_id,
                condition_key=condition_key,
                kind=kind,
                severity=severity,
                title=title,
                description=description,
                asset_id=asset_id,
                work_order_id=work_order_id,
                part_id=part_id,
                document_id=document_id,
                link_path=link_path,
                status=AttentionStatus.OPEN,
            )
        )
        return
    row.kind = kind
    row.severity = severity
    row.title = title
    row.description = description
    row.asset_id = asset_id
    row.work_order_id = work_order_id
    row.part_id = part_id
    row.document_id = document_id
    row.link_path = link_path
    if row.status == AttentionStatus.DISMISSED:
        return
    if row.status == AttentionStatus.RESOLVED:
        row.status = AttentionStatus.OPEN
        row.resolved_at = None
        row.dismissed_at = None


_REFRESH_MIN_SECONDS = 15.0
_last_refresh: dict[UUID, float] = {}


def refresh_attention(db: Session, organization_id: UUID, *, force: bool = True) -> None:
    if not force:
        last = _last_refresh.get(organization_id)
        if last is not None and (monotonic() - last) < _REFRESH_MIN_SECONDS:
            return
    now = _now()
    today = date.today()
    existing_rows = db.scalars(
        select(AttentionItem).where(AttentionItem.organization_id == organization_id)
    ).all()
    existing = {row.condition_key: row for row in existing_rows}
    seen: set[str] = set()

    assets = db.scalars(
        select(Asset)
        .options(selectinload(Asset.work_orders), selectinload(Asset.deployments), selectinload(Asset.meter_readings))
        .where(Asset.organization_id == organization_id)
        .where(Asset.is_archived.is_(False))
    ).all()
    asset_by_id = {asset.id: asset for asset in assets}

    schedules = db.scalars(
        select(MaintenanceSchedule)
        .where(MaintenanceSchedule.organization_id == organization_id)
        .where(MaintenanceSchedule.is_active.is_(True))
    ).all()
    for schedule in schedules:
        asset = asset_by_id.get(schedule.asset_id)
        if asset is None:
            continue
        latest = get_latest_meter_reading(db, asset.id)
        detail = calculate_pm_status(
            schedule,
            latest.odometer_miles if latest else None,
            latest.engine_hours if latest else None,
            now,
        )
        pm_status = detail.status.value if hasattr(detail.status, "value") else detail.status
        if pm_status == "overdue":
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"pm_overdue:{schedule.id}",
                kind="pm_overdue",
                severity=AttentionSeverity.CRITICAL,
                title=f"Overdue PM · {asset.make_model}",
                description=f"{schedule.name} is overdue.",
                asset_id=asset.id,
                link_path="/preventive-maintenance",
            )
        elif pm_status in {"due", "due_soon"}:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"pm_due_soon:{schedule.id}",
                kind="pm_due_soon",
                severity=AttentionSeverity.WARNING,
                title=f"PM due soon · {asset.make_model}",
                description=f"{schedule.name} is {str(pm_status).replace('_', ' ')}.",
                asset_id=asset.id,
                link_path="/preventive-maintenance",
            )

    failed_items = db.scalars(
        select(InspectionItem)
        .options(selectinload(InspectionItem.inspection))
        .join(Inspection, InspectionItem.inspection_id == Inspection.id)
        .where(Inspection.organization_id == organization_id)
        .where(Inspection.status == InspectionStatus.SUBMITTED)
        .where(InspectionItem.result == InspectionResult.FAIL)
    ).all()
    for item in failed_items:
        inspection = item.inspection
        if inspection is None or inspection.asset_id not in asset_by_id:
            continue
        wo_open = False
        if item.generated_work_order_id:
            wo = db.get(MaintenanceWorkOrder, item.generated_work_order_id)
            wo_open = wo is not None and wo.status not in {WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED}
        else:
            wo_open = True
        if not wo_open:
            continue
        _upsert(
            db,
            existing,
            seen,
            organization_id=organization_id,
            condition_key=f"failed_inspection:{item.id}",
            kind="failed_inspection",
            severity=AttentionSeverity.CRITICAL,
            title=f"Failed inspection · {item.component}",
            description="A failed inspection item still requires maintenance.",
            asset_id=inspection.asset_id,
            work_order_id=item.generated_work_order_id,
            link_path=f"/assets/{inspection.asset_id}",
        )

    work_orders = db.scalars(
        select(MaintenanceWorkOrder).where(MaintenanceWorkOrder.organization_id == organization_id)
    ).all()
    for wo in work_orders:
        opened = _as_utc(wo.updated_at) or _as_utc(wo.opened_at) or now
        waiting_days = (now - opened).days
        if wo.status == WorkOrderStatus.WAITING_PARTS and waiting_days >= WAITING_TOO_LONG_DAYS:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"wo_waiting_parts:{wo.id}",
                kind="wo_waiting_parts",
                severity=AttentionSeverity.WARNING,
                title=f"Waiting on parts · {wo.title}",
                description=f"Work order has been waiting on parts for {waiting_days} days.",
                asset_id=wo.asset_id,
                work_order_id=wo.id,
                link_path=f"/maintenance/{wo.id}",
            )
        if wo.status == WorkOrderStatus.WAITING_VENDOR and waiting_days >= WAITING_TOO_LONG_DAYS:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"wo_waiting_vendor:{wo.id}",
                kind="wo_waiting_vendor",
                severity=AttentionSeverity.WARNING,
                title=f"Waiting on vendor · {wo.title}",
                description=f"Work order has been waiting on a vendor for {waiting_days} days.",
                asset_id=wo.asset_id,
                work_order_id=wo.id,
                link_path=f"/maintenance/{wo.id}",
            )

    for asset in assets:
        # Retired (and legacy out_of_service) is a terminal status, not downtime.
        repair = total_repair_cost(asset)
        purchase = Decimal(asset.initial_purchase_cost or 0)
        if purchase > 0 and repair >= purchase:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"repair_cost:{asset.id}",
                kind="repair_cost_exceeds_purchase",
                severity=AttentionSeverity.CRITICAL,
                title=f"Repair cost exceeds purchase · {asset.make_model}",
                description="Lifetime maintenance cost has reached or exceeded initial purchase cost.",
                asset_id=asset.id,
                link_path=f"/assets/{asset.id}",
            )
        util = compute_asset_utilization(asset, now)
        if "Low Utilization" in util.flags or "Available > 30 Days" in util.flags:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"low_util:{asset.id}",
                kind="low_utilization",
                severity=AttentionSeverity.INFO,
                title=f"Low utilization · {asset.make_model}",
                description=f"Unused {util.consecutive_unused_days} days · 90-day utilization {util.windows[90].utilization_pct}%.",
                asset_id=asset.id,
                link_path=f"/assets/{asset.id}",
            )
        if "High Downtime" in util.flags:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"high_downtime:{asset.id}",
                kind="high_downtime",
                severity=AttentionSeverity.WARNING,
                title=f"High downtime · {asset.make_model}",
                description=f"Maintenance consumed {util.windows[90].maintenance_days} days in the last 90.",
                asset_id=asset.id,
                link_path=f"/assets/{asset.id}",
            )

    parts = db.scalars(
        select(InventoryPart)
        .where(InventoryPart.organization_id == organization_id)
        .where(InventoryPart.is_active.is_(True))
    ).all()
    for part in parts:
        if part.quantity_on_hand <= 0:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"part_out:{part.id}",
                kind="part_out_of_stock",
                severity=AttentionSeverity.CRITICAL,
                title=f"Out of stock · {part.sku}",
                description=f"{part.name} has no quantity on hand.",
                part_id=part.id,
                link_path="/parts",
            )
        elif part.quantity_on_hand <= part.reorder_point:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"part_low:{part.id}",
                kind="part_low_stock",
                severity=AttentionSeverity.WARNING,
                title=f"Low stock · {part.sku}",
                description=f"{part.name} is at {part.quantity_on_hand} (reorder point {part.reorder_point}).",
                part_id=part.id,
                link_path="/parts",
            )

    documents = db.scalars(
        select(AssetDocument).where(AssetDocument.organization_id == organization_id)
    ).all()
    for doc in documents:
        if not doc.expiration_date:
            continue
        days = (doc.expiration_date - today).days
        # Each document carries its own lead time; registration renewals need
        # more warning than a warranty.
        lead_days = doc.reminder_days if doc.reminder_days is not None else DOC_SOON_DAYS
        label = doc.document_type.value.replace("_", " ")
        if days < 0:
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"doc_expired:{doc.id}",
                kind="doc_expired",
                severity=AttentionSeverity.CRITICAL,
                title=f"Expired {label} · {doc.title}",
                description=f"{doc.title} expired {abs(days)} days ago.",
                asset_id=doc.asset_id,
                document_id=doc.id,
                link_path=f"/assets/{doc.asset_id}",
            )
        elif days <= lead_days:
            kind = {
                DocumentType.REGISTRATION: "doc_expiring_registration",
                DocumentType.INSURANCE: "doc_expiring_insurance",
                DocumentType.WARRANTY: "doc_expiring_warranty",
            }.get(doc.document_type, "doc_expiring")
            _upsert(
                db,
                existing,
                seen,
                organization_id=organization_id,
                condition_key=f"doc_expiring:{doc.id}",
                kind=kind,
                severity=AttentionSeverity.WARNING,
                title=f"Expiring {label} · {doc.title}",
                description=f"{doc.title} expires in {days} days.",
                asset_id=doc.asset_id,
                document_id=doc.id,
                link_path=f"/assets/{doc.asset_id}",
            )

    for key, row in existing.items():
        if key in seen:
            continue
        if row.status == AttentionStatus.OPEN:
            row.status = AttentionStatus.RESOLVED
            row.resolved_at = now
        elif row.status == AttentionStatus.DISMISSED and row.resolved_at is None:
            row.status = AttentionStatus.RESOLVED
            row.resolved_at = now
    db.commit()
    _last_refresh[organization_id] = monotonic()


def _visible_query(user: User):
    stmt = select(AttentionItem).where(AttentionItem.organization_id == user.organization_id)
    if canonical_role(user) not in MANAGERS:
        stmt = stmt.where(AttentionItem.document_id.is_(None)).where(~AttentionItem.kind.like("doc_%"))
    return stmt


@router.get("/attention")
def list_attention(
    include_resolved: bool = False,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> list[dict]:
    refresh_attention(db, current_user.organization_id, force=True)
    stmt = _visible_query(current_user)
    if not include_resolved:
        stmt = stmt.where(AttentionItem.status == AttentionStatus.OPEN)
    rows = db.scalars(stmt.order_by(AttentionItem.created_at.desc())).all()
    return [serialize_attention(row) for row in rows]


@router.get("/attention/summary")
def attention_summary(
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    refresh_attention(db, current_user.organization_id, force=False)
    rows = db.scalars(
        _visible_query(current_user).where(AttentionItem.status == AttentionStatus.OPEN)
    ).all()
    counts = {"critical": 0, "warning": 0, "info": 0, "total": len(rows)}
    for row in rows:
        counts[row.severity.value] = counts.get(row.severity.value, 0) + 1
    return counts


@router.post("/attention/{item_id}/dismiss")
def dismiss_attention(
    item_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(AttentionItem, item_id)
    if not row or not can_access_organization(current_user, row.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attention item not found")
    if canonical_role(current_user) not in MANAGERS and (row.document_id or row.kind.startswith("doc_")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attention item not found")
    row.status = AttentionStatus.DISMISSED
    row.dismissed_at = _now()
    row.dismissed_by_id = current_user.id
    db.commit()
    db.refresh(row)
    return serialize_attention(row)


@router.post("/attention/{item_id}/resolve")
def resolve_attention(
    item_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(AttentionItem, item_id)
    if not row or not can_access_organization(current_user, row.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attention item not found")
    if canonical_role(current_user) not in MANAGERS and (row.document_id or row.kind.startswith("doc_")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attention item not found")
    row.status = AttentionStatus.RESOLVED
    row.resolved_at = _now()
    db.commit()
    db.refresh(row)
    return serialize_attention(row)
