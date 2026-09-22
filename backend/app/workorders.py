from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Asset,
    AssetType,
    IssueSource,
    MaintenanceWorkOrder,
    RepairChannel,
    User,
    WorkOrderEvent,
    WorkOrderEventType,
    WorkOrderPriority,
    WorkOrderStatus,
)
from app.schemas import WorkOrderEventOut, WorkOrderOut

OPEN_WO_STATUSES = {
    WorkOrderStatus.OPEN,
    WorkOrderStatus.INVESTIGATING,
    WorkOrderStatus.IN_PROGRESS,
    WorkOrderStatus.WAITING_PARTS,
    WorkOrderStatus.WAITING_VENDOR,
}
TERMINAL_WO = {WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED}

_WO_LOAD = (
    selectinload(MaintenanceWorkOrder.asset),
    selectinload(MaintenanceWorkOrder.created_by),
    selectinload(MaintenanceWorkOrder.assigned_to),
    selectinload(MaintenanceWorkOrder.events).selectinload(WorkOrderEvent.created_by),
)


def wo_total_cost(wo: MaintenanceWorkOrder) -> Decimal:
    return Decimal(wo.labor_cost or 0) + Decimal(wo.parts_cost or 0) + Decimal(wo.vendor_invoice_cost or 0)


def record_event(
    db: Session,
    wo: MaintenanceWorkOrder,
    event_type: WorkOrderEventType,
    user_id: UUID | None,
    previous: str | None = None,
    new: str | None = None,
    notes: str | None = None,
) -> None:
    db.add(
        WorkOrderEvent(
            work_order_id=wo.id,
            event_type=event_type,
            previous_value=previous,
            new_value=new,
            notes=notes,
            created_by_id=user_id,
        )
    )


def serialize_work_order(wo: MaintenanceWorkOrder, *, include_events: bool = False) -> WorkOrderOut:
    events: list[WorkOrderEventOut] = []
    if include_events:
        for event in sorted(wo.events, key=lambda row: row.created_at or datetime.min):
            events.append(
                WorkOrderEventOut(
                    id=event.id,
                    event_type=event.event_type,
                    previous_value=event.previous_value,
                    new_value=event.new_value,
                    notes=event.notes,
                    created_by=event.created_by.full_name if event.created_by else None,
                    created_by_id=event.created_by_id,
                    created_at=event.created_at,
                )
            )
    total = wo_total_cost(wo)
    return WorkOrderOut(
        id=wo.id,
        asset_id=wo.asset_id,
        asset=wo.asset.make_model if wo.asset else "",
        asset_type=wo.asset.asset_type if wo.asset else AssetType.FLEET_VEHICLE,
        vin=wo.asset.vin if wo.asset else None,
        title=wo.title,
        description=wo.description,
        status=wo.status,
        opened_at=wo.opened_at,
        closed_at=wo.closed_at,
        downtime_hours=wo.downtime_hours,
        labor_cost=wo.labor_cost,
        parts_cost=wo.parts_cost,
        vendor_invoice_cost=wo.vendor_invoice_cost or Decimal("0"),
        total_repair_cost=total,
        inspection_id=wo.inspection_id,
        inspection_item_id=wo.inspection_item_id,
        failed_component=wo.failed_component,
        repair_channel=wo.repair_channel or RepairChannel.INTERNAL,
        vendor_name=wo.vendor_name,
        issue_source=wo.issue_source or IssueSource.MANUAL_REPORT,
        priority=wo.priority or WorkOrderPriority.MEDIUM,
        assigned_to_id=wo.assigned_to_id,
        assigned_to=wo.assigned_to.full_name if wo.assigned_to else None,
        inspector=wo.created_by.full_name if wo.created_by else None,
        created_by_id=wo.created_by_id,
        updated_by_id=wo.updated_by_id,
        created_at=wo.created_at,
        updated_at=wo.updated_at,
        investigation_notes=wo.investigation_notes,
        repair_actions=wo.repair_actions,
        parts_used=wo.parts_used,
        labor_hours=wo.labor_hours or Decimal("0"),
        downtime_start=wo.downtime_start,
        downtime_end=wo.downtime_end,
        root_cause_category=wo.root_cause_category,
        root_cause_description=wo.root_cause_description,
        corrective_action=wo.corrective_action,
        preventive_action=wo.preventive_action,
        completion_notes=wo.completion_notes,
        is_archived=bool(wo.is_archived),
        location=wo.asset.current_location if wo.asset else None,
        events=events,
    )


def load_work_order(db: Session, wo_id: UUID) -> MaintenanceWorkOrder | None:
    return db.scalar(select(MaintenanceWorkOrder).options(*_WO_LOAD).where(MaintenanceWorkOrder.id == wo_id))


def work_order_query(
    db: Session,
    *,
    status: WorkOrderStatus | None = None,
    asset_type: AssetType | None = None,
    asset_id: UUID | None = None,
    priority: WorkOrderPriority | None = None,
    component: str | None = None,
    assigned_to_id: UUID | None = None,
    location: str | None = None,
    vendor: str | None = None,
    repair_channel: RepairChannel | None = None,
    opened_from: datetime | None = None,
    opened_to: datetime | None = None,
    include_archived: bool = False,
    organization_id: UUID | None = None,
) -> Select[tuple[MaintenanceWorkOrder]]:
    stmt = select(MaintenanceWorkOrder).options(*_WO_LOAD).join(Asset, MaintenanceWorkOrder.asset_id == Asset.id)
    # CRITICAL: Organization filtering for multi-tenant security
    if organization_id is not None:
        stmt = stmt.where(MaintenanceWorkOrder.organization_id == organization_id)
    if not include_archived:
        stmt = stmt.where(
            (MaintenanceWorkOrder.is_archived.is_(False)) | (MaintenanceWorkOrder.is_archived.is_(None))
        )
    if status:
        stmt = stmt.where(MaintenanceWorkOrder.status == status)
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)
    if asset_id:
        stmt = stmt.where(MaintenanceWorkOrder.asset_id == asset_id)
    if priority:
        stmt = stmt.where(MaintenanceWorkOrder.priority == priority)
    if component:
        stmt = stmt.where(MaintenanceWorkOrder.failed_component.ilike(f"%{component.strip()}%"))
    if assigned_to_id:
        stmt = stmt.where(MaintenanceWorkOrder.assigned_to_id == assigned_to_id)
    if location:
        stmt = stmt.where(Asset.current_location.ilike(f"%{location.strip()}%"))
    if vendor:
        stmt = stmt.where(MaintenanceWorkOrder.vendor_name.ilike(f"%{vendor.strip()}%"))
    if repair_channel:
        stmt = stmt.where(MaintenanceWorkOrder.repair_channel == repair_channel)
    if opened_from:
        stmt = stmt.where(MaintenanceWorkOrder.opened_at >= opened_from)
    if opened_to:
        stmt = stmt.where(MaintenanceWorkOrder.opened_at <= opened_to)
    return stmt.order_by(MaintenanceWorkOrder.opened_at.desc())


def list_users(db: Session, current_user: User | None = None) -> list[User]:
    """Assignable work-order users: active, same organization, not customer/viewer roles."""
    from app.models import UserRole
    from app.rbac import is_system_admin

    stmt = select(User).where(User.is_active.is_(True))
    if current_user is not None and not is_system_admin(current_user):
        stmt = stmt.where(User.organization_id == current_user.organization_id)
    # Matches on stored values, so legacy spellings must be listed explicitly.
    excluded = {UserRole.CUSTOMER, UserRole.READ_ONLY, UserRole.VIEWER}
    stmt = stmt.where(User.role.notin_(excluded))
    return list(db.scalars(stmt.order_by(User.full_name)).all())
