"""Preventive Maintenance calculations and service logic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Asset, AssetType, MaintenanceSchedule, MeterReading, PMCompletion, PMStatus
from app.schemas import MaintenanceScheduleOut, MeterReadingOut, PMStatusDetail


def get_latest_meter_reading(db: Session, asset_id: UUID) -> MeterReading | None:
    """Get the most recent meter reading for an asset."""
    return db.scalar(
        select(MeterReading)
        .where(MeterReading.asset_id == asset_id)
        .order_by(MeterReading.reading_date.desc())
        .limit(1)
    )


def calculate_pm_status(
    schedule: MaintenanceSchedule,
    current_miles: Decimal | None,
    current_hours: Decimal | None,
    current_date: datetime,
) -> PMStatusDetail:
    """Calculate preventive maintenance due status and remaining metrics."""
    
    # Determine next due based on intervals
    next_due_date = None
    next_due_miles = None
    next_due_hours = None
    
    if schedule.interval_days and schedule.last_completed_date:
        base_date = schedule.last_completed_date.replace(tzinfo=UTC) if schedule.last_completed_date.tzinfo is None else schedule.last_completed_date
        next_due_date = base_date + timedelta(days=schedule.interval_days)
    elif schedule.interval_months and schedule.last_completed_date:
        # Approximate months as 30 days
        base_date = schedule.last_completed_date.replace(tzinfo=UTC) if schedule.last_completed_date.tzinfo is None else schedule.last_completed_date
        next_due_date = base_date + timedelta(days=schedule.interval_months * 30)
    
    if schedule.interval_miles and schedule.last_completed_miles is not None:
        next_due_miles = schedule.last_completed_miles + schedule.interval_miles
    
    if schedule.interval_engine_hours and schedule.last_completed_engine_hours is not None:
        next_due_hours = schedule.last_completed_engine_hours + schedule.interval_engine_hours
    
    # Calculate remaining
    remaining_days = None
    remaining_miles = None
    remaining_hours = None
    
    if next_due_date:
        delta = (next_due_date - current_date).days
        remaining_days = delta if delta > 0 else 0
    
    if next_due_miles and current_miles is not None:
        remaining_miles = next_due_miles - current_miles
        if remaining_miles < 0:
            remaining_miles = Decimal("0")
    
    if next_due_hours and current_hours is not None:
        remaining_hours = next_due_hours - current_hours
        if remaining_hours < 0:
            remaining_hours = Decimal("0")
    
    # Determine status based on most critical metric
    status = PMStatus.OK
    
    # Check if overdue
    if next_due_date and current_date > next_due_date:
        status = PMStatus.OVERDUE
    elif next_due_miles and current_miles is not None and current_miles >= next_due_miles:
        status = PMStatus.OVERDUE
    elif next_due_hours and current_hours is not None and current_hours >= next_due_hours:
        status = PMStatus.OVERDUE
    # Check if due or due soon
    elif next_due_date and remaining_days is not None and 0 <= remaining_days <= schedule.due_soon_threshold_days:
        status = PMStatus.DUE if remaining_days == 0 else PMStatus.DUE_SOON
    elif next_due_miles and current_miles is not None and remaining_miles is not None and remaining_miles <= schedule.due_soon_threshold_miles:
        status = PMStatus.DUE if remaining_miles == 0 else PMStatus.DUE_SOON
    elif next_due_hours and current_hours is not None and remaining_hours is not None and remaining_hours <= Decimal("10"):
        status = PMStatus.DUE if remaining_hours == 0 else PMStatus.DUE_SOON
    
    return PMStatusDetail(
        status=status,
        next_due_date=next_due_date,
        next_due_miles=next_due_miles,
        next_due_hours=next_due_hours,
        remaining_days=remaining_days,
        remaining_miles=remaining_miles,
        remaining_hours=remaining_hours,
    )


def serialize_pm_schedule(schedule: MaintenanceSchedule, asset: Asset, db: Session) -> MaintenanceScheduleOut:
    """Serialize a PM schedule with calculated status."""
    # Get current meter reading
    latest = get_latest_meter_reading(db, asset.id)
    current_miles = latest.odometer_miles if latest else None
    current_hours = latest.engine_hours if latest else None
    current_date = datetime.now(UTC)
    
    # Calculate status
    pm_detail = calculate_pm_status(schedule, current_miles, current_hours, current_date)
    
    return MaintenanceScheduleOut(
        id=schedule.id,
        asset_id=schedule.asset_id,
        asset_name=asset.make_model,
        asset_type=asset.asset_type,
        vin=asset.vin,
        name=schedule.name,
        description=schedule.description,
        interval_miles=schedule.interval_miles,
        interval_engine_hours=schedule.interval_engine_hours,
        interval_days=schedule.interval_days,
        interval_months=schedule.interval_months,
        due_soon_threshold_miles=schedule.due_soon_threshold_miles,
        due_soon_threshold_days=schedule.due_soon_threshold_days,
        last_completed_date=schedule.last_completed_date,
        last_completed_miles=schedule.last_completed_miles,
        last_completed_engine_hours=schedule.last_completed_engine_hours,
        is_active=schedule.is_active,
        auto_create_work_order=schedule.auto_create_work_order,
        pending_work_order_id=schedule.pending_work_order_id,
        current_odometer_miles=current_miles,
        current_engine_hours=current_hours,
        pm_status=pm_detail.status,
        next_due_date=pm_detail.next_due_date,
        next_due_miles=pm_detail.next_due_miles,
        next_due_hours=pm_detail.next_due_hours,
        remaining_days=pm_detail.remaining_days,
        remaining_miles=pm_detail.remaining_miles,
        remaining_hours=pm_detail.remaining_hours,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def pm_schedule_query(
    db: Session,
    *,
    asset_id: UUID | None = None,
    asset_type: AssetType | None = None,
    status: PMStatus | None = None,
    location: str | None = None,
    is_active: bool = True,
    organization_id: UUID | None = None,
) -> Select[tuple[MaintenanceSchedule]]:
    """Query PM schedules with filters."""
    stmt = (
        select(MaintenanceSchedule)
        .join(Asset, MaintenanceSchedule.asset_id == Asset.id)
        .options(
            selectinload(MaintenanceSchedule.asset),
            selectinload(MaintenanceSchedule.completions),
        )
    )
    
    # CRITICAL: Organization filtering for multi-tenant security
    if organization_id is not None:
        stmt = stmt.where(MaintenanceSchedule.organization_id == organization_id)
    
    if is_active:
        stmt = stmt.where(MaintenanceSchedule.is_active.is_(True))
    
    if asset_id:
        stmt = stmt.where(MaintenanceSchedule.asset_id == asset_id)
    
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)
    
    if location:
        stmt = stmt.where(Asset.current_location.ilike(f"%{location.strip()}%"))
    
    return stmt.order_by(MaintenanceSchedule.name)


def list_pm_schedules_with_status(
    db: Session,
    asset_id: UUID | None = None,
    asset_type: AssetType | None = None,
    pm_status: PMStatus | None = None,
    location: str | None = None,
    organization_id: UUID | None = None,
    current_user=None,
) -> list[MaintenanceScheduleOut]:
    """List PM schedules with calculated status, optionally filtered by PM status."""
    from app.rbac import filter_by_authorized_assets

    stmt = pm_schedule_query(db, asset_id=asset_id, asset_type=asset_type, location=location, organization_id=organization_id)
    if current_user is not None:
        stmt = filter_by_authorized_assets(stmt, current_user, db, MaintenanceSchedule.asset_id)
    schedules = db.scalars(stmt).all()
    
    results = []
    for schedule in schedules:
        serialized = serialize_pm_schedule(schedule, schedule.asset, db)
        if pm_status is None or serialized.pm_status == pm_status:
            results.append(serialized)
    
    return results


def record_meter_reading(
    db: Session,
    asset_id: UUID,
    odometer_miles: Decimal | None,
    engine_hours: Decimal | None,
    reading_date: datetime,
    source: str = "manual",
    notes: str | None = None,
    user_id: UUID | None = None,
) -> MeterReading:
    """Record a new meter reading."""
    reading = MeterReading(
        asset_id=asset_id,
        odometer_miles=odometer_miles,
        engine_hours=engine_hours,
        reading_date=reading_date,
        source=source,
        notes=notes,
        created_by_id=user_id,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return reading


def serialize_meter_reading(reading: MeterReading) -> MeterReadingOut:
    """Serialize a meter reading."""
    return MeterReadingOut(
        id=reading.id,
        asset_id=reading.asset_id,
        odometer_miles=reading.odometer_miles,
        engine_hours=reading.engine_hours,
        reading_date=reading.reading_date,
        source=reading.source,
        notes=reading.notes,
        created_at=reading.created_at,
    )
