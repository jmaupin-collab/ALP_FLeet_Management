from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.checklists import LEMON_WARNING, checklist_for
from app.models import (
    Asset,
    AssetType,
    CustodyType,
    Deployment,
    DeploymentStatus,
    Inspection,
    InspectionItem,
    InspectionResult,
    InspectionStatus,
    IssueSource,
    MaintenanceWorkOrder,
    RepairChannel,
    WorkOrderEventType,
    WorkOrderPriority,
    WorkOrderStatus,
)
from app.schemas import (
    AssetOut,
    AssetTimeline,
    AssetTypeKpis,
    CustodyUpdate,
    DashboardKpis,
    DeploymentOut,
    InspectionOut,
    TimelineEvent,
    WorkOrderOut,
)
from app.workorders import OPEN_WO_STATUSES, record_event, serialize_work_order


DEPLOYED_STATUSES = {DeploymentStatus.DEPLOYED, DeploymentStatus.IN_TRANSIT}

_ASSET_LOAD = (
    selectinload(Asset.deployments),
    selectinload(Asset.work_orders),
    selectinload(Asset.inspections).selectinload(Inspection.items),
)


def _open_deployment_status(asset: Asset) -> DeploymentStatus | None:
    for row in asset.deployments:
        if row.ended_at is None:
            return row.status
    return None


def total_repair_cost(asset: Asset) -> Decimal:
    return sum(
        (
            Decimal(wo.labor_cost or 0) + Decimal(wo.parts_cost or 0) + Decimal(getattr(wo, "vendor_invoice_cost", 0) or 0)
            for wo in asset.work_orders
        ),
        Decimal("0"),
    )


def open_deployments_for(asset: Asset) -> list[Deployment]:
    return [row for row in (asset.deployments or []) if row.ended_at is None]


def is_retired(asset: Asset) -> bool:
    """Out of service is treated as retired. Warehouse location does not make it active."""
    from app.models import AssetOperationalStatus

    return bool(
        asset.is_archived
        or asset.operational_status in {AssetOperationalStatus.RETIRED, AssetOperationalStatus.OUT_OF_SERVICE}
    )


def resolve_operational_status(
    *,
    retired: bool,
    current_custody_type,
    operational_status,
    open_custody_types: list,
):
    """Single source of truth: Deployed only when an open customer deployment exists.

    Takes plain values rather than an Asset so aggregate queries can reuse the
    same rules without loading every deployment row.
    """
    from app.models import AssetOperationalStatus, CustodyType

    if retired:
        return AssetOperationalStatus.RETIRED
    if current_custody_type == CustodyType.IN_TRANSIT:
        return AssetOperationalStatus.IN_TRANSIT
    if operational_status == AssetOperationalStatus.DEPLOYED and not open_custody_types:
        return AssetOperationalStatus.AVAILABLE
    if any(custody == CustodyType.CUSTOMER_AGENCY for custody in open_custody_types):
        return AssetOperationalStatus.DEPLOYED
    return operational_status


def effective_operational_status(asset: Asset):
    return resolve_operational_status(
        retired=is_retired(asset),
        current_custody_type=asset.current_custody_type,
        operational_status=asset.operational_status,
        open_custody_types=[row.custody_type for row in open_deployments_for(asset)],
    )


def reconcile_asset_deployment_state(db: Session, asset: Asset) -> None:
    """Exactly one open row while Deployed; never Deployed with zero open rows."""
    from app.models import AssetOperationalStatus

    now = datetime.now(UTC)
    opens = db.scalars(
        select(Deployment)
        .where(Deployment.asset_id == asset.id)
        .where(Deployment.ended_at.is_(None))
        .order_by(Deployment.started_at.desc())
    ).all()
    extras = list(opens[1:])
    for extra in extras:
        extra.ended_at = now
        extra.status = DeploymentStatus.COMPLETED
        extra.end_reason = extra.end_reason or "superseded"
    opens = opens[:1]

    if is_retired(asset):
        asset.operational_status = AssetOperationalStatus.RETIRED
        for row in opens:
            row.ended_at = now
            row.status = DeploymentStatus.COMPLETED
            row.end_reason = row.end_reason or "retired"
        return
    if asset.current_custody_type == CustodyType.IN_TRANSIT:
        asset.operational_status = AssetOperationalStatus.IN_TRANSIT
        return
    if not opens:
        if asset.operational_status == AssetOperationalStatus.DEPLOYED:
            asset.operational_status = AssetOperationalStatus.AVAILABLE
        return
    if opens[0].custody_type == CustodyType.CUSTOMER_AGENCY:
        asset.operational_status = AssetOperationalStatus.DEPLOYED


def serialize_asset(asset: Asset) -> AssetOut:
    open_wos = sum(1 for wo in asset.work_orders if wo.status in OPEN_WO_STATUSES)
    repair = total_repair_cost(asset)
    purchase = Decimal(asset.initial_purchase_cost or 0)
    lemon = purchase > 0 and repair >= purchase
    pct = (repair / purchase * 100).quantize(Decimal("0.1")) if purchase > 0 else None
    downtime_hours = sum((Decimal(wo.downtime_hours or 0) for wo in asset.work_orders), Decimal("0"))
    downtime_days = (downtime_hours / Decimal("24")).quantize(Decimal("0.1"))
    repeats = Counter(wo.failed_component for wo in asset.work_orders if wo.failed_component)
    repeat_failure_count = sum(count for count in repeats.values() if count >= 2)
    created = asset.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - created).days if created else None
    
    status = effective_operational_status(asset)
    operational_status = status.value if status else None
    
    return AssetOut.model_validate(asset).model_copy(
        update={
            "current_status": _open_deployment_status(asset),
            "operational_status": operational_status,
            "open_work_orders": open_wos,
            "total_repair_cost": repair,
            "lemon_flag": lemon,
            "lemon_warning": LEMON_WARNING if lemon else None,
            "maintenance_cost_pct": pct,
            "downtime_days": downtime_days,
            "repeat_failure_count": repeat_failure_count,
            "age_days": age_days,
            "replacement_score": None,
        }
    )


def serialize_deployment(row: Deployment) -> DeploymentOut:
    return DeploymentOut(
        id=row.id,
        asset_id=row.asset_id,
        asset=row.asset.make_model,
        asset_type=row.asset.asset_type,
        location=row.location,
        status=row.status,
        custody_type=row.custody_type,
        carrier_name=row.carrier_name,
        tracking_code=row.tracking_code,
        started_at=row.started_at,
        ended_at=row.ended_at,
        notes=row.notes,
        end_reason=row.end_reason,
        completion_notes=row.completion_notes,
        completed_by_id=row.completed_by_id,
    )


def get_asset(db: Session, asset_id: UUID) -> Asset | None:
    return db.scalar(select(Asset).options(*_ASSET_LOAD).where(Asset.id == asset_id))


def asset_query(
    db: Session,
    q: str | None = None,
    asset_type: AssetType | None = None,
    location: str | None = None,
    include_archived: bool = False,
    organization_id: UUID | None = None,
) -> Select[tuple[Asset]]:
    stmt = select(Asset).options(*_ASSET_LOAD)
    # CRITICAL: Organization filtering for multi-tenant security
    if organization_id is not None:
        stmt = stmt.where(Asset.organization_id == organization_id)
    if not include_archived:
        stmt = stmt.where(Asset.is_archived.is_(False))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Asset.vin.ilike(like), Asset.make_model.ilike(like), Asset.current_location.ilike(like)))
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)
    if location:
        stmt = stmt.where(Asset.current_location.ilike(f"%{location.strip()}%"))
    return stmt.order_by(Asset.make_model)


def dashboard_kpis(db: Session, organization_id: UUID, current_user=None) -> DashboardKpis:
    """Fleet rollup in three queries rather than loading every child row.

    Status still comes from resolve_operational_status(), so these numbers match
    what the asset list and map report.
    """
    from app.models import AssetOperationalStatus
    from app.rbac import filter_by_authorized_assets

    scoped = (
        select(
            Asset.id,
            Asset.asset_type,
            Asset.initial_purchase_cost,
            Asset.is_archived,
            Asset.operational_status,
            Asset.current_custody_type,
        )
        .where(Asset.is_archived.is_(False))
        .where(Asset.organization_id == organization_id)
    )
    if current_user is not None:
        scoped = filter_by_authorized_assets(scoped, current_user, db, Asset.id)

    assets = db.execute(scoped).all()
    in_scope = select(scoped.subquery().c.id)

    downtime_by_asset: dict[UUID, Decimal] = dict(
        db.execute(
            select(
                MaintenanceWorkOrder.asset_id,
                func.coalesce(func.sum(MaintenanceWorkOrder.downtime_hours), 0),
            )
            .where(MaintenanceWorkOrder.asset_id.in_(in_scope))
            .group_by(MaintenanceWorkOrder.asset_id)
        ).all()
    )

    open_custody: dict[UUID, list] = {}
    for asset_id, custody_type in db.execute(
        select(Deployment.asset_id, Deployment.custody_type)
        .where(Deployment.asset_id.in_(in_scope))
        .where(Deployment.ended_at.is_(None))
    ).all():
        open_custody.setdefault(asset_id, []).append(custody_type)

    totals = {
        asset_type: {
            "count": 0,
            "deployed": 0,
            "available": 0,
            "in_transit": 0,
            "maintenance": 0,
            "idle": 0,
            "cost": Decimal("0"),
            "downtime": Decimal("0"),
        }
        for asset_type in AssetType
    }

    for row in assets:
        bucket = totals[row.asset_type]
        bucket["count"] += 1
        bucket["cost"] += Decimal(row.initial_purchase_cost or 0)
        bucket["downtime"] += Decimal(downtime_by_asset.get(row.id, 0))

        retired = bool(
            row.is_archived
            or row.operational_status
            in {AssetOperationalStatus.RETIRED, AssetOperationalStatus.OUT_OF_SERVICE}
        )
        status = resolve_operational_status(
            retired=retired,
            current_custody_type=row.current_custody_type,
            operational_status=row.operational_status,
            open_custody_types=open_custody.get(row.id, []),
        )
        if status == AssetOperationalStatus.DEPLOYED:
            bucket["deployed"] += 1
        elif status == AssetOperationalStatus.AVAILABLE:
            bucket["available"] += 1
        elif status == AssetOperationalStatus.IN_TRANSIT:
            bucket["in_transit"] += 1
        elif status == AssetOperationalStatus.MAINTENANCE:
            bucket["maintenance"] += 1
        else:
            bucket["idle"] += 1

    by_type = [
        AssetTypeKpis(
            asset_type=asset_type,
            total_assets=bucket["count"],
            deployed=bucket["deployed"],
            in_maintenance=bucket["maintenance"],
            idle_or_stored=bucket["idle"],
            total_purchase_cost=bucket["cost"],
            downtime_hours_ytd=bucket["downtime"],
        )
        for asset_type, bucket in totals.items()
    ]

    def fleet_total(key: str):
        return sum(bucket[key] for bucket in totals.values())

    return DashboardKpis(
        fleet_size=len(assets),
        deployed=fleet_total("deployed"),
        available=fleet_total("available"),
        in_transit=fleet_total("in_transit"),
        in_maintenance=fleet_total("maintenance"),
        total_purchase_cost=sum((bucket["cost"] for bucket in totals.values()), Decimal("0")),
        downtime_hours_ytd=sum((bucket["downtime"] for bucket in totals.values()), Decimal("0")),
        by_asset_type=by_type,
    )


def asset_timeline(db: Session, asset_id: UUID) -> AssetTimeline | None:
    asset = get_asset(db, asset_id)
    if asset is None:
        return None

    events: list[TimelineEvent] = []
    for dep in asset.deployments:
        custody = f"{dep.custody_type.value} · " if dep.custody_type else ""
        tracking = f" · {dep.carrier_name} {dep.tracking_code}" if dep.tracking_code else ""
        events.append(
            TimelineEvent(
                id=dep.id,
                event_type="deployment",
                title=f"{custody}{dep.status.value.replace('_', ' ')}",
                status=dep.status.value,
                location=f"{dep.location}{tracking}",
                started_at=dep.started_at,
                ended_at=dep.ended_at,
                notes=dep.notes,
            )
        )
    for wo in asset.work_orders:
        events.append(
            TimelineEvent(
                id=wo.id,
                event_type="maintenance",
                title=wo.title,
                status=wo.status.value,
                started_at=wo.opened_at,
                ended_at=wo.closed_at,
                downtime_hours=wo.downtime_hours,
                notes=wo.description,
            )
        )
    for insp in asset.inspections:
        failed = [item.component for item in insp.items if item.result == InspectionResult.FAIL]
        events.append(
            TimelineEvent(
                id=insp.id,
                event_type="inspection",
                title="Digital inspection",
                status=insp.status.value,
                started_at=insp.started_at,
                ended_at=insp.submitted_at,
                notes=("Failed: " + ", ".join(failed)) if failed else "No failed components.",
            )
        )
    events.sort(key=lambda e: e.started_at, reverse=True)
    total = sum((wo.downtime_hours for wo in asset.work_orders), Decimal("0"))
    return AssetTimeline(asset=serialize_asset(asset), events=events, total_downtime_hours=total)


def require_directory_in_org(record, organization_id, label: str):
    """Refuse cross-organization warehouse/agency UUIDs. Missing org_id is legacy-local only."""
    if record is None or getattr(record, "is_archived", False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    record_org = getattr(record, "organization_id", None)
    if record_org is not None and organization_id is not None and record_org != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return record


def close_open_deployment(db: Session, asset: Asset, ended_at: datetime | None = None) -> None:
    """Never overwrite a deployment row — close the current one before inserting history."""
    now = ended_at or datetime.now(UTC)
    open_row = db.scalar(
        select(Deployment).where(Deployment.asset_id == asset.id, Deployment.ended_at.is_(None))
    )
    if open_row:
        open_row.ended_at = now
        if open_row.status not in {DeploymentStatus.COMPLETED, DeploymentStatus.RETURNED, DeploymentStatus.CANCELLED}:
            open_row.status = DeploymentStatus.COMPLETED


def _status_for_custody(custody_type: CustodyType) -> DeploymentStatus:
    if custody_type == CustodyType.IN_TRANSIT:
        return DeploymentStatus.IN_TRANSIT
    if custody_type == CustodyType.CUSTOMER_AGENCY:
        return DeploymentStatus.DEPLOYED
    return DeploymentStatus.STORED


def apply_custody(db: Session, asset: Asset, payload: CustodyUpdate, user_id: UUID | None) -> Deployment:
    if asset.is_archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset is archived. Restore it before operational changes.")
    if payload.custody_type == CustodyType.IN_TRANSIT and not (payload.tracking_code and payload.carrier_name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="In-transit custody requires a 3PL carrier name and tracking code.",
        )
    now = datetime.now(UTC)
    from app.models import Agency, AssetOperationalStatus, Warehouse

    agency_id = payload.agency_id
    if payload.custody_type == CustodyType.CUSTOMER_AGENCY and not agency_id:
        from app.location import resolve_agency_by_location
        matched = resolve_agency_by_location(db, asset.organization_id, payload.location)
        if matched:
            agency_id = matched.id
    if payload.warehouse_id:
        require_directory_in_org(db.get(Warehouse, payload.warehouse_id), asset.organization_id, "Warehouse")
    if agency_id:
        require_directory_in_org(db.get(Agency, agency_id), asset.organization_id, "Agency")

    open_deployment = db.scalar(
        select(Deployment).where(Deployment.asset_id == asset.id, Deployment.ended_at.is_(None))
    )
    status_value = _status_for_custody(payload.custody_type)
    carrier = payload.carrier_name if payload.custody_type == CustodyType.IN_TRANSIT else None
    tracking = payload.tracking_code if payload.custody_type == CustodyType.IN_TRANSIT else None
    returning = payload.custody_type == CustodyType.WAREHOUSE_DEPOT
    agency_changed = (
        payload.custody_type == CustodyType.CUSTOMER_AGENCY
        and agency_id is not None
        and asset.agency_id is not None
        and asset.agency_id != agency_id
    )
    custody_changed = open_deployment is not None and open_deployment.custody_type != payload.custody_type
    close_and_append = open_deployment is not None and (returning or agency_changed or custody_changed)

    if close_and_append:
        open_deployment.ended_at = now
        open_deployment.status = DeploymentStatus.COMPLETED
        open_deployment.end_reason = "Transfer" if agency_changed else ("Return" if returning else open_deployment.custody_type.value)
        open_deployment.updated_by_id = user_id
        open_deployment = None

    if open_deployment and not returning:
        open_deployment.location = payload.location.strip()
        open_deployment.status = status_value
        open_deployment.custody_type = payload.custody_type
        open_deployment.carrier_name = carrier
        open_deployment.tracking_code = tracking
        open_deployment.notes = payload.notes or open_deployment.notes
        open_deployment.updated_by_id = user_id
        row = open_deployment
    else:
        row = Deployment(
            asset_id=asset.id,
            organization_id=asset.organization_id,
            location=payload.location.strip(),
            status=DeploymentStatus.COMPLETED if returning else status_value,
            custody_type=payload.custody_type,
            carrier_name=carrier,
            tracking_code=tracking,
            started_at=now,
            ended_at=now if returning else None,
            notes=payload.notes,
            created_by_id=user_id,
        )
        db.add(row)

    asset.current_location = payload.location.strip()
    asset.current_custody_type = payload.custody_type
    asset.carrier_name = carrier
    asset.tracking_code = tracking
    asset.updated_by_id = user_id
    if payload.custody_type == CustodyType.CUSTOMER_AGENCY:
        asset.operational_status = AssetOperationalStatus.DEPLOYED
    elif payload.custody_type == CustodyType.IN_TRANSIT:
        asset.operational_status = AssetOperationalStatus.IN_TRANSIT
    elif payload.custody_type == CustodyType.WAREHOUSE_DEPOT:
        # Warehouse assignment must not un-retire an asset.
        asset.operational_status = (
            AssetOperationalStatus.RETIRED if is_retired(asset) else AssetOperationalStatus.AVAILABLE
        )

    if payload.warehouse_id:
        asset.warehouse_id = payload.warehouse_id
        asset.agency_id = None
    if agency_id and payload.custody_type != CustodyType.WAREHOUSE_DEPOT:
        asset.agency_id = agency_id
        asset.warehouse_id = None
    if returning:
        asset.agency_id = None

    from app.location import apply_location_snapshot
    apply_location_snapshot(
        db,
        row,
        asset,
        agency_id=None if returning else agency_id,
        warehouse_id=payload.warehouse_id or asset.warehouse_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        address=payload.address,
        location=payload.location,
    )
    reconcile_asset_deployment_state(db, asset)
    db.commit()
    db.refresh(row)
    return row


def start_inspection(db: Session, asset: Asset, user_id: UUID | None, organization_id: UUID) -> Inspection:
    if asset.is_archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset is archived. Restore it before operational changes.")
    open_insp = db.scalar(
        select(Inspection)
        .options(selectinload(Inspection.items))
        .where(Inspection.asset_id == asset.id, Inspection.status == InspectionStatus.IN_PROGRESS)
    )
    if open_insp:
        return open_insp

    inspection = Inspection(
        organization_id=organization_id,
        asset_id=asset.id,
        status=InspectionStatus.IN_PROGRESS,
        started_at=datetime.now(UTC),
        source="internal",
        created_by_id=user_id,
    )
    db.add(inspection)
    db.flush()
    for index, component in enumerate(checklist_for(asset.asset_type)):
        db.add(
            InspectionItem(
                inspection_id=inspection.id,
                component=component,
                sort_order=index,
                result=InspectionResult.PENDING,
            )
        )
    db.commit()
    return db.scalar(
        select(Inspection).options(selectinload(Inspection.items)).where(Inspection.id == inspection.id)
    )


def _open_fail_work_order(db: Session, asset: Asset, inspection: Inspection, item: InspectionItem, user_id: UUID | None) -> MaintenanceWorkOrder:
    """Fail on an inspection item opens exactly one Open work order for that item."""
    if item.generated_work_order_id:
        existing = db.get(MaintenanceWorkOrder, item.generated_work_order_id)
        if existing:
            return existing
    existing = db.scalar(select(MaintenanceWorkOrder).where(MaintenanceWorkOrder.inspection_item_id == item.id))
    if existing:
        item.generated_work_order_id = existing.id
        return existing

    now = datetime.now(UTC)
    notes = item.notes or "No technician notes."
    wo = MaintenanceWorkOrder(
        organization_id=asset.organization_id,
        asset_id=asset.id,
        title=f"Inspection fail: {item.component}",
        description=f"Automated ticket created when {item.component} was marked Fail on inspection {inspection.id}. {notes}",
        status=WorkOrderStatus.OPEN,
        opened_at=now,
        downtime_start=now,
        inspection_id=inspection.id,
        inspection_item_id=item.id,
        failed_component=item.component,
        issue_source=IssueSource.INSPECTION,
        priority=WorkOrderPriority.HIGH,
        investigation_notes=notes,
        created_by_id=user_id,
        updated_by_id=user_id,
    )
    db.add(wo)
    db.flush()
    item.generated_work_order_id = wo.id
    record_event(
        db,
        wo,
        WorkOrderEventType.CREATED,
        user_id,
        new=WorkOrderStatus.OPEN.value,
        notes=f"Auto-created from inspection {inspection.id} item {item.id} ({item.component}).",
    )
    return wo


def update_inspection_item(
    db: Session,
    inspection: Inspection,
    item_id: UUID,
    result: InspectionResult,
    notes: str | None,
    user_id: UUID | None,
) -> InspectionItem:
    if inspection.status != InspectionStatus.IN_PROGRESS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Inspection is already submitted")
    item = next((row for row in inspection.items if row.id == item_id), None)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection item not found")
    item.result = result
    if notes is not None:
        item.notes = notes
    if result == InspectionResult.FAIL and item.generated_work_order_id is None:
        asset = inspection.asset or db.get(Asset, inspection.asset_id)
        _open_fail_work_order(db, asset, inspection, item, user_id)
    db.commit()
    db.refresh(item)
    return item


def submit_inspection(db: Session, inspection: Inspection, notes: str | None) -> Inspection:
    if inspection.status == InspectionStatus.SUBMITTED:
        return inspection
    if getattr(inspection, "source", "internal") != "customer":
        pending = [item.component for item in inspection.items if item.result == InspectionResult.PENDING]
        if pending:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Mark every component before submit. Pending: {', '.join(pending)}",
            )
    inspection.status = InspectionStatus.SUBMITTED
    inspection.submitted_at = datetime.now(UTC)
    if notes:
        inspection.notes = notes
    db.commit()
    db.refresh(inspection)
    return inspection


def load_inspection(db: Session, inspection_id: UUID) -> Inspection | None:
    return db.scalar(
        select(Inspection)
        .options(
            selectinload(Inspection.items),
            selectinload(Inspection.asset),
            selectinload(Inspection.photos),
        )
        .where(Inspection.id == inspection_id)
    )


def list_work_orders(db: Session) -> list[WorkOrderOut]:
    rows = db.scalars(
        select(MaintenanceWorkOrder)
        .options(selectinload(MaintenanceWorkOrder.asset))
        .order_by(MaintenanceWorkOrder.opened_at.desc())
    ).all()
    return [serialize_work_order(row) for row in rows]


def list_deployments(db: Session, organization_id: UUID, current_user=None) -> list[DeploymentOut]:
    # Only show active deployments (not completed)
    from app.rbac import filter_by_authorized_assets

    stmt = (
        select(Deployment)
        .where(Deployment.ended_at.is_(None))
        .where(Deployment.organization_id == organization_id)
        .options(selectinload(Deployment.asset))
        .order_by(Deployment.started_at.desc())
    )
    if current_user is not None:
        stmt = filter_by_authorized_assets(stmt, current_user, db, Deployment.asset_id)
    rows = db.scalars(stmt).all()
    return [serialize_deployment(row) for row in rows if row.asset and not is_retired(row.asset)]


def serialize_inspection(inspection: Inspection) -> InspectionOut:
    if not getattr(inspection, "source", None):
        inspection.source = "internal"
    return InspectionOut.model_validate(inspection)


def create_customer_inspection(
    db: Session,
    asset: Asset,
    user_id: UUID | None,
    organization_id: UUID,
    notes: str | None,
    uploads: list[UploadFile],
) -> Inspection:
    if asset.is_archived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset is archived. Restore it before operational changes.",
        )
    from app.inspection_photos import save_customer_photos

    now = datetime.now(UTC)
    inspection = Inspection(
        organization_id=organization_id,
        asset_id=asset.id,
        status=InspectionStatus.SUBMITTED,
        started_at=now,
        submitted_at=now,
        notes=notes,
        source="customer",
        created_by_id=user_id,
        updated_by_id=user_id,
    )
    db.add(inspection)
    db.flush()
    for photo in save_customer_photos(inspection.id, uploads, user_id):
        db.add(photo)
    db.commit()
    loaded = load_inspection(db, inspection.id)
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Inspection could not be loaded")
    return loaded
