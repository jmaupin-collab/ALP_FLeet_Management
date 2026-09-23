"""Parts inventory with append-only quantity transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import require_fleet_admin, require_operator
from app.models import (
    Asset,
    InventoryPart,
    InventoryTransaction,
    InventoryTxnType,
    MaintenanceWorkOrder,
    User,
    UserRole,
    WorkOrderEventType,
    WorkOrderPart,
)
from app.rbac import can_access_organization, canonical_role, require_asset_access, require_work_order_access
from app.workorders import record_event, serialize_work_order

router = APIRouter(tags=["parts-inventory"])

# Canonical roles only; legacy values resolve through canonical_role().
MANAGERS = frozenset({UserRole.SYSTEM_ADMIN, UserRole.ORG_ADMIN, UserRole.FLEET_MANAGER})


class PartCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    manufacturer: str | None = None
    vendor: str | None = None
    warehouse_location: str | None = None
    bin_location: str | None = None
    quantity_on_hand: int = Field(default=0, ge=0)
    reorder_point: int = Field(default=0, ge=0)
    reorder_quantity: int = Field(default=0, ge=0)
    unit_cost: Decimal = Field(default=Decimal("0"), ge=0)
    compatible_models: str | None = None
    is_active: bool = True


class PartUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    manufacturer: str | None = None
    vendor: str | None = None
    warehouse_location: str | None = None
    bin_location: str | None = None
    reorder_point: int | None = Field(default=None, ge=0)
    reorder_quantity: int | None = Field(default=None, ge=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)
    compatible_models: str | None = None
    is_active: bool | None = None


class InventoryTxnCreate(BaseModel):
    """Stock movement request.

    For RECEIPT and RETURN, `quantity` is how many units come in. For
    ADJUSTMENT it is the corrected on-hand count, which is why it may be zero.
    """

    txn_type: InventoryTxnType
    quantity: int = Field(ge=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None
    allow_negative: bool = False
    work_order_id: UUID | None = None
    asset_id: UUID | None = None
    transfer_location: str | None = None

    @model_validator(mode="after")
    def non_adjustments_must_move_stock(self):
        if self.txn_type != InventoryTxnType.ADJUSTMENT and self.quantity == 0:
            raise ValueError("quantity must be greater than zero for this transaction type")
        return self


class WorkOrderPartCreate(BaseModel):
    part_id: UUID
    quantity: int = Field(gt=0)
    allow_negative: bool = False


class PartInstall(BaseModel):
    asset_id: UUID
    quantity: int = Field(gt=0)
    work_order_id: UUID | None = None
    notes: str | None = None
    allow_negative: bool = False


def _stock_state(part: InventoryPart) -> str:
    if part.quantity_on_hand <= 0:
        return "out_of_stock"
    if part.quantity_on_hand <= part.reorder_point:
        return "low_stock"
    return "ok"


def serialize_part(part: InventoryPart) -> dict:
    return {
        "id": part.id,
        "organization_id": part.organization_id,
        "sku": part.sku,
        "name": part.name,
        "description": part.description,
        "manufacturer": part.manufacturer,
        "vendor": part.vendor,
        "warehouse_location": part.warehouse_location,
        "bin_location": part.bin_location,
        "quantity_on_hand": part.quantity_on_hand,
        "reorder_point": part.reorder_point,
        "reorder_quantity": part.reorder_quantity,
        "unit_cost": part.unit_cost,
        "compatible_models": part.compatible_models,
        "is_active": part.is_active,
        "stock_state": _stock_state(part),
        "inventory_value": Decimal(part.quantity_on_hand) * Decimal(part.unit_cost or 0),
        "created_at": part.created_at,
        "updated_at": part.updated_at,
    }


def serialize_txn(row: InventoryTransaction, asset: Asset | None = None) -> dict:
    return {
        "id": row.id,
        "part_id": row.part_id,
        "work_order_id": row.work_order_id,
        "asset_id": row.asset_id,
        "asset_vin": asset.vin if asset else None,
        "asset_name": asset.make_model if asset else None,
        "txn_type": row.txn_type.value,
        "quantity_delta": row.quantity_delta,
        "quantity_after": row.quantity_after,
        "unit_cost": row.unit_cost,
        "allow_negative": row.allow_negative,
        "notes": row.notes,
        "reverses_transaction_id": row.reverses_transaction_id,
        "reversal_reason": row.reversal_reason,
        "created_by_id": row.created_by_id,
        "created_at": row.created_at,
    }


def serialize_wo_part(row: WorkOrderPart) -> dict:
    return {
        "id": row.id,
        "work_order_id": row.work_order_id,
        "part_id": row.part_id,
        "sku": row.part.sku if row.part else None,
        "name": row.part.name if row.part else None,
        "quantity": row.quantity,
        "unit_cost": row.unit_cost,
        "line_cost": Decimal(row.quantity) * Decimal(row.unit_cost),
        "created_at": row.created_at,
    }


class ReversalCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def get_org_part(db: Session, user: User, part_id: UUID, *, lock: bool = False) -> InventoryPart:
    stmt = select(InventoryPart).where(InventoryPart.id == part_id)
    if lock:
        stmt = stmt.with_for_update()
    part = db.scalar(stmt)
    if not part or not can_access_organization(user, part.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Part not found")
    return part


def apply_quantity_change(
    db: Session,
    part: InventoryPart,
    *,
    txn_type: InventoryTxnType,
    delta: int,
    user: User,
    unit_cost: Decimal | None = None,
    notes: str | None = None,
    allow_negative: bool = False,
    work_order_id: UUID | None = None,
    asset_id: UUID | None = None,
    reverses_transaction_id: UUID | None = None,
    reversal_reason: str | None = None,
) -> InventoryTransaction:
    """Append a ledger row and move on-hand quantity. Caller must hold the row lock."""
    if delta == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity change cannot be zero")
    if allow_negative and canonical_role(user) not in MANAGERS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager override required")
    next_qty = part.quantity_on_hand + delta
    if next_qty < 0 and not allow_negative:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Insufficient quantity on hand. A manager override is required to go negative.",
        )
    part.quantity_on_hand = next_qty
    txn = InventoryTransaction(
        organization_id=part.organization_id,
        part_id=part.id,
        work_order_id=work_order_id,
        asset_id=asset_id,
        txn_type=txn_type,
        quantity_delta=delta,
        quantity_after=next_qty,
        unit_cost=unit_cost if unit_cost is not None else Decimal(part.unit_cost or 0),
        allow_negative=allow_negative,
        notes=notes,
        reverses_transaction_id=reverses_transaction_id,
        reversal_reason=reversal_reason,
        created_by_id=user.id,
    )
    db.add(txn)
    db.flush()
    return txn


def _delta_for(txn_type: InventoryTxnType, quantity: int) -> int:
    if txn_type in {InventoryTxnType.RECEIPT, InventoryTxnType.RETURN}:
        return abs(quantity)
    if txn_type in {InventoryTxnType.WORK_ORDER_USAGE, InventoryTxnType.TRANSFER}:
        return -abs(quantity)
    return quantity  # adjustment may be signed by caller via notes; quantity is always positive here


@router.get("/parts")
def list_parts(
    q: str | None = None,
    stock: str | None = Query(default=None, description="ok, low_stock, out_of_stock, reorder"),
    include_inactive: bool = False,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(InventoryPart).where(InventoryPart.organization_id == current_user.organization_id)
    if not include_inactive:
        stmt = stmt.where(InventoryPart.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(InventoryPart.sku.ilike(like) | InventoryPart.name.ilike(like))
    parts = db.scalars(stmt.order_by(InventoryPart.sku)).all()
    rows = [serialize_part(part) for part in parts]
    if stock == "reorder":
        return [row for row in rows if row["stock_state"] in {"low_stock", "out_of_stock"}]
    if stock:
        return [row for row in rows if row["stock_state"] == stock]
    return rows


@router.get("/parts/summary")
def parts_summary(
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    parts = db.scalars(
        select(InventoryPart)
        .where(InventoryPart.organization_id == current_user.organization_id)
        .where(InventoryPart.is_active.is_(True))
    ).all()
    value = sum((Decimal(p.quantity_on_hand) * Decimal(p.unit_cost or 0) for p in parts), Decimal("0"))
    low = [serialize_part(p) for p in parts if _stock_state(p) == "low_stock"]
    out = [serialize_part(p) for p in parts if _stock_state(p) == "out_of_stock"]
    return {
        "part_count": len(parts),
        "inventory_value": value,
        "low_stock_count": len(low),
        "out_of_stock_count": len(out),
        "reorder_needed": low + out,
    }


@router.post("/parts", status_code=status.HTTP_201_CREATED)
def create_part(
    payload: PartCreate,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    existing = db.scalar(
        select(InventoryPart)
        .where(InventoryPart.organization_id == current_user.organization_id)
        .where(func.lower(InventoryPart.sku) == payload.sku.strip().lower())
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SKU already exists in this organization")
    part = InventoryPart(
        organization_id=current_user.organization_id,
        sku=payload.sku.strip(),
        name=payload.name.strip(),
        description=payload.description,
        manufacturer=payload.manufacturer,
        vendor=payload.vendor,
        warehouse_location=payload.warehouse_location,
        bin_location=payload.bin_location,
        quantity_on_hand=0,
        reorder_point=payload.reorder_point,
        reorder_quantity=payload.reorder_quantity,
        unit_cost=payload.unit_cost,
        compatible_models=payload.compatible_models,
        is_active=payload.is_active,
    )
    db.add(part)
    db.flush()
    if payload.quantity_on_hand:
        apply_quantity_change(
            db,
            part,
            txn_type=InventoryTxnType.RECEIPT,
            delta=payload.quantity_on_hand,
            user=current_user,
            unit_cost=payload.unit_cost,
            notes="Initial stock",
        )
    db.commit()
    db.refresh(part)
    return serialize_part(part)


@router.get("/parts/{part_id}")
def get_part(
    part_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return serialize_part(get_org_part(db, current_user, part_id))


@router.patch("/parts/{part_id}")
def update_part(
    part_id: UUID,
    payload: PartUpdate,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    part = get_org_part(db, current_user, part_id)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(part, key, value)
    db.commit()
    db.refresh(part)
    return serialize_part(part)


@router.delete("/parts/{part_id}")
def delete_part(
    part_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Remove a part, keeping its ledger when one exists.

    A part that was never stocked or used is a data-entry mistake and is deleted
    outright. Once it has transactions or sits on a work order, that history is
    the audit trail for stock and repair cost, so the part is retired instead:
    it leaves the parts list but the ledger stays intact. Mirrors the
    delete-or-archive behaviour used for warehouses and agencies.
    """
    part = get_org_part(db, current_user, part_id)

    on_work_orders = db.scalar(
        select(func.count(WorkOrderPart.id)).where(WorkOrderPart.part_id == part.id)
    ) or 0
    transactions = db.scalar(
        select(func.count(InventoryTransaction.id)).where(InventoryTransaction.part_id == part.id)
    ) or 0

    if on_work_orders or transactions:
        if not part.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This part is already retired. Its history cannot be deleted.",
            )
        part.is_active = False
        db.commit()
        reasons = []
        if transactions:
            reasons.append(f"{transactions} stock transaction(s)")
        if on_work_orders:
            reasons.append(f"{on_work_orders} work-order line(s)")
        return {
            "action": "retired",
            "id": str(part.id),
            "sku": part.sku,
            "detail": (
                f"{part.sku} has {' and '.join(reasons)} and was retired instead of deleted, "
                "so the stock and cost history stays intact."
            ),
        }

    db.delete(part)
    db.commit()
    return {"action": "deleted", "id": str(part_id), "sku": part.sku}


@router.get("/parts/{part_id}/transactions")
def list_part_transactions(
    part_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> list[dict]:
    part = get_org_part(db, current_user, part_id)
    rows = db.scalars(
        select(InventoryTransaction)
        .where(InventoryTransaction.part_id == part.id)
        .order_by(InventoryTransaction.created_at.desc())
    ).all()
    asset_ids = {row.asset_id for row in rows if row.asset_id}
    assets = {
        asset.id: asset
        for asset in db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()
    } if asset_ids else {}
    return [serialize_txn(row, assets.get(row.asset_id)) for row in rows]


@router.post("/parts/{part_id}/transactions", status_code=status.HTTP_201_CREATED)
def create_part_transaction(
    part_id: UUID,
    payload: InventoryTxnCreate,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    part = get_org_part(db, current_user, part_id, lock=True)
    if payload.txn_type == InventoryTxnType.WORK_ORDER_USAGE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Work-order usage must be recorded from the work order.",
        )
    if payload.txn_type == InventoryTxnType.TRANSFER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Multi-location transfers are disabled until stock is modeled per SKU and warehouse. "
                "A single location field cannot split quantity across Phoenix and Mesa. "
                "Receive stock at the destination as a new part record, or wait for location-level inventory."
            ),
        )
    if payload.txn_type == InventoryTxnType.REVERSAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reversals must use POST /parts/transactions/{transaction_id}/reverse.",
        )
    if payload.txn_type == InventoryTxnType.ADJUSTMENT:
        delta = payload.quantity - part.quantity_on_hand
        if delta == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"On-hand quantity is already {payload.quantity}. Nothing to adjust.",
            )
        notes = payload.notes or f"Adjusted on-hand from {part.quantity_on_hand} to {payload.quantity}"
    else:
        delta = _delta_for(payload.txn_type, payload.quantity)
        notes = payload.notes
    txn = apply_quantity_change(
        db,
        part,
        txn_type=payload.txn_type,
        delta=delta,
        user=current_user,
        unit_cost=payload.unit_cost,
        notes=notes,
        allow_negative=payload.allow_negative,
        work_order_id=payload.work_order_id,
        asset_id=payload.asset_id,
    )
    db.commit()
    db.refresh(txn)
    return serialize_txn(txn)


@router.get("/work-orders/{wo_id}/parts")
def list_work_order_parts(
    wo_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> list[dict]:
    wo = require_work_order_access(db, current_user, wo_id)
    rows = db.scalars(
        select(WorkOrderPart)
        .options(selectinload(WorkOrderPart.part))
        .where(WorkOrderPart.work_order_id == wo.id)
        .order_by(WorkOrderPart.created_at.asc())
    ).all()
    return [serialize_wo_part(row) for row in rows]


@router.post("/work-orders/{wo_id}/parts", status_code=status.HTTP_201_CREATED)
def add_work_order_part(
    wo_id: UUID,
    payload: WorkOrderPartCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    wo = require_work_order_access(db, current_user, wo_id)
    part = get_org_part(db, current_user, payload.part_id, lock=True)
    if part.organization_id != wo.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Part not found")
    unit_cost = Decimal(part.unit_cost or 0)
    txn = apply_quantity_change(
        db,
        part,
        txn_type=InventoryTxnType.WORK_ORDER_USAGE,
        delta=-abs(payload.quantity),
        user=current_user,
        unit_cost=unit_cost,
        notes=f"Used on work order {wo.title}",
        allow_negative=payload.allow_negative,
        work_order_id=wo.id,
        asset_id=wo.asset_id,
    )
    line = WorkOrderPart(
        organization_id=wo.organization_id,
        work_order_id=wo.id,
        part_id=part.id,
        quantity=payload.quantity,
        unit_cost=unit_cost,
        transaction_id=txn.id,
        created_by_id=current_user.id,
    )
    db.add(line)
    line_cost = Decimal(payload.quantity) * unit_cost
    wo.parts_cost = Decimal(wo.parts_cost or 0) + line_cost
    used = (wo.parts_used or "").strip()
    addition = f"{part.sku} x{payload.quantity}"
    wo.parts_used = f"{used}; {addition}".strip("; ") if used else addition
    record_event(
        db,
        wo,
        WorkOrderEventType.PARTS_ADDED,
        current_user.id,
        new=addition,
        notes=f"{money_plain(line_cost)} added to parts cost",
    )
    db.commit()
    db.refresh(line)
    line.part = part
    return {"line": serialize_wo_part(line), "work_order": serialize_work_order(wo, include_events=True)}


@router.post("/parts/{part_id}/install", status_code=status.HTTP_201_CREATED)
def install_part(
    part_id: UUID,
    payload: PartInstall,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    part = get_org_part(db, current_user, part_id, lock=True)
    asset = require_asset_access(db, current_user, payload.asset_id)
    wo = None
    if payload.work_order_id:
        wo = require_work_order_access(db, current_user, payload.work_order_id)
        if wo.asset_id != asset.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Work order does not belong to that asset")
    unit_cost = Decimal(part.unit_cost or 0)
    notes = payload.notes or f"Installed on {asset.vin} ({asset.make_model})"
    txn = apply_quantity_change(
        db,
        part,
        txn_type=InventoryTxnType.WORK_ORDER_USAGE,
        delta=-abs(payload.quantity),
        user=current_user,
        unit_cost=unit_cost,
        notes=notes,
        allow_negative=payload.allow_negative,
        work_order_id=wo.id if wo else None,
        asset_id=asset.id,
    )
    if wo:
        line = WorkOrderPart(
            organization_id=wo.organization_id,
            work_order_id=wo.id,
            part_id=part.id,
            quantity=payload.quantity,
            unit_cost=unit_cost,
            transaction_id=txn.id,
            created_by_id=current_user.id,
        )
        db.add(line)
        line_cost = Decimal(payload.quantity) * unit_cost
        wo.parts_cost = Decimal(wo.parts_cost or 0) + line_cost
        used = (wo.parts_used or "").strip()
        addition = f"{part.sku} x{payload.quantity}"
        wo.parts_used = f"{used}; {addition}".strip("; ") if used else addition
        record_event(
            db,
            wo,
            WorkOrderEventType.PARTS_ADDED,
            current_user.id,
            new=addition,
            notes=f"{money_plain(line_cost)} installed on {asset.vin}",
        )
    db.commit()
    db.refresh(txn)
    db.refresh(part)
    return {"transaction": serialize_txn(txn, asset), "part": serialize_part(part)}


@router.post("/parts/transactions/{transaction_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_part_transaction(
    transaction_id: UUID,
    payload: ReversalCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    original = db.get(InventoryTransaction, transaction_id)
    if not original or not can_access_organization(current_user, original.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    if original.txn_type == InventoryTxnType.REVERSAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A reversal cannot be reversed. Record a new usage if needed.")
    already = db.scalar(
        select(InventoryTransaction).where(InventoryTransaction.reverses_transaction_id == original.id)
    )
    if already:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This transaction has already been reversed.")
    part = get_org_part(db, current_user, original.part_id, lock=True)
    reason = payload.reason.strip()
    opposite = apply_quantity_change(
        db,
        part,
        txn_type=InventoryTxnType.REVERSAL,
        delta=-original.quantity_delta,
        user=current_user,
        unit_cost=original.unit_cost,
        notes=f"Reversal of {original.txn_type.value}: {reason}",
        work_order_id=original.work_order_id,
        asset_id=original.asset_id,
        reverses_transaction_id=original.id,
        reversal_reason=reason,
    )
    wo = None
    if original.work_order_id and original.txn_type == InventoryTxnType.WORK_ORDER_USAGE:
        wo = require_work_order_access(db, current_user, original.work_order_id)
        line = db.scalar(
            select(WorkOrderPart).where(WorkOrderPart.transaction_id == original.id)
        )
        if line:
            line_cost = Decimal(line.quantity) * Decimal(line.unit_cost)
            wo.parts_cost = max(Decimal("0"), Decimal(wo.parts_cost or 0) - line_cost)
        record_event(
            db,
            wo,
            WorkOrderEventType.PARTS_REVERSED,
            current_user.id,
            new=f"{part.sku} x{abs(original.quantity_delta)}",
            notes=f"Reversed by {current_user.full_name or current_user.email}: {reason}",
        )
    db.commit()
    db.refresh(opposite)
    db.refresh(part)
    asset = db.get(Asset, original.asset_id) if original.asset_id else None
    return {
        "transaction": serialize_txn(opposite, asset),
        "part": serialize_part(part),
        "work_order": serialize_work_order(wo, include_events=True) if wo else None,
        "reversed_transaction_id": original.id,
        "reversed_by_id": current_user.id,
        "reason": reason,
    }


def money_plain(value: Decimal) -> str:
    return f"${value:.2f}"
