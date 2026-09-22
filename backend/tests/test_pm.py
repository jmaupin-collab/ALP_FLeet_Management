"""Test preventive maintenance calculations and workflow."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.config import get_settings
from app.database import dispose_engine, ensure_schema, get_db, rebind_engine
from app.models import Asset, AssetType, MaintenanceSchedule, MeterReading, Organization, PMCompletion
from app.pm import PMStatus, calculate_pm_status, get_latest_meter_reading
from sqlalchemy import select

PM_DB = Path(__file__).resolve().parent / "_pm_test.db"
PM_DB_URL = f"sqlite:///{PM_DB}"


def setup_module():
    """Use an isolated SQLite file so the full suite does not share engines."""
    get_settings.cache_clear()
    rebind_engine(PM_DB_URL)
    ensure_schema()


def teardown_module():
    dispose_engine()
    PM_DB.unlink(missing_ok=True)


def _org(db):
    org = db.scalar(select(Organization).limit(1))
    if org is None:
        org = Organization(name="PM Test Org", slug="pm-test", org_type="internal")
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


def unique_vin():
    """Generate unique VIN for each test."""
    return f"TEST-PM-{uuid.uuid4().hex[:8].upper()}"


def test_mileage_based_due_calculation():
    """Test mileage-based PM due calculation."""
    db = next(get_db())
    
    try:
        # Create asset
        asset = Asset(
            organization_id=_org(db).id,
            vin=unique_vin(),
            make_model="Test Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=Decimal("30000"),
            current_location="Test Warehouse",
            current_custody_type="Warehouse Depot",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
        # Create schedule
        schedule = MaintenanceSchedule(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            name="Oil Change",
            interval_miles=Decimal("7500"),
            due_soon_threshold_miles=Decimal("500"),
            last_completed_miles=Decimal("30000"),
            last_completed_date=datetime.now(UTC) - timedelta(days=30),
        )
        db.add(schedule)
        db.commit()
        
        # Record meter reading: Current odometer = 37,100 miles
        # Next due = 30,000 + 7,500 = 37,500 mi
        # Remaining = 37,500 - 37,100 = 400 mi (within 500 mi threshold = DUE_SOON)
        reading = MeterReading(
            asset_id=asset.id,
            odometer_miles=Decimal("37100"),
            reading_date=datetime.now(UTC),
            source="manual",
        )
        db.add(reading)
        db.commit()
        
        # Calculate status
        status_detail = calculate_pm_status(
            schedule,
            current_miles=Decimal("37100"),
            current_hours=None,
            current_date=datetime.now(UTC),
        )
        
        assert status_detail.status == PMStatus.DUE_SOON
        assert status_detail.next_due_miles == Decimal("37500")
        assert status_detail.remaining_miles == Decimal("400")
    finally:
        db.rollback()
        db.close()


def test_date_based_due_calculation():
    """Test date-based PM due calculation."""
    db = next(get_db())
    
    try:
        asset = Asset(
            organization_id=_org(db).id,
            vin=unique_vin(),
            make_model="Test Trailer",
            asset_type=AssetType.ALPR_TRAILER,
            initial_purchase_cost=Decimal("50000"),
            current_location="Test Warehouse",
            current_custody_type="Warehouse Depot",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
        last_completed = datetime.now(UTC) - timedelta(days=85)
        schedule = MaintenanceSchedule(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            name="Battery Inspection",
            interval_days=90,
            due_soon_threshold_days=7,
            last_completed_date=last_completed,
        )
        db.add(schedule)
        db.commit()
        
        status_detail = calculate_pm_status(
            schedule,
            current_miles=None,
            current_hours=None,
            current_date=datetime.now(UTC),
        )
        
        assert status_detail.status == PMStatus.DUE_SOON
        assert status_detail.remaining_days is not None
        assert 0 <= status_detail.remaining_days <= 7
    finally:
        db.rollback()
        db.close()


def test_engine_hours_due_calculation():
    """Test engine-hours-based PM due calculation."""
    db = next(get_db())
    
    try:
        asset = Asset(
            organization_id=_org(db).id,
            vin=unique_vin(),
            make_model="Test Semi",
            asset_type=AssetType.SEMI_TRUCK,
            initial_purchase_cost=Decimal("120000"),
            current_location="Test Warehouse",
            current_custody_type="Warehouse Depot",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
        schedule = MaintenanceSchedule(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            name="Engine Service",
            interval_engine_hours=Decimal("500"),
            last_completed_engine_hours=Decimal("1000"),
            last_completed_date=datetime.now(UTC) - timedelta(days=60),
        )
        db.add(schedule)
        db.commit()
        
        reading = MeterReading(
            asset_id=asset.id,
            engine_hours=Decimal("1495"),
            reading_date=datetime.now(UTC),
            source="manual",
        )
        db.add(reading)
        db.commit()
        
        status_detail = calculate_pm_status(
            schedule,
            current_miles=None,
            current_hours=Decimal("1495"),
            current_date=datetime.now(UTC),
        )
        
        assert status_detail.status == PMStatus.DUE_SOON
        assert status_detail.next_due_hours == Decimal("1500")
        assert status_detail.remaining_hours == Decimal("5")
    finally:
        db.rollback()
        db.close()


def test_overdue_status():
    """Test overdue PM status."""
    db = next(get_db())
    
    try:
        asset = Asset(
            organization_id=_org(db).id,
            vin=unique_vin(),
            make_model="Test Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=Decimal("30000"),
            current_location="Test Warehouse",
            current_custody_type="Warehouse Depot",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
        schedule = MaintenanceSchedule(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            name="Tire Rotation",
            interval_miles=Decimal("10000"),
            due_soon_threshold_miles=Decimal("500"),
            last_completed_miles=Decimal("20000"),
            last_completed_date=datetime.now(UTC) - timedelta(days=180),
        )
        db.add(schedule)
        db.commit()
        
        reading = MeterReading(
            asset_id=asset.id,
            odometer_miles=Decimal("32000"),
            reading_date=datetime.now(UTC),
            source="manual",
        )
        db.add(reading)
        db.commit()
        
        status_detail = calculate_pm_status(
            schedule,
            current_miles=Decimal("32000"),
            current_hours=None,
            current_date=datetime.now(UTC),
        )
        
        assert status_detail.status == PMStatus.OVERDUE
        assert status_detail.next_due_miles == Decimal("30000")
    finally:
        db.rollback()
        db.close()


def test_pm_completion_resets_schedule():
    """Test completing PM updates the schedule correctly."""
    db = next(get_db())
    
    try:
        asset = Asset(
            organization_id=_org(db).id,
            vin=unique_vin(),
            make_model="Test Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=Decimal("30000"),
            current_location="Test Warehouse",
            current_custody_type="Warehouse Depot",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
        schedule = MaintenanceSchedule(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            name="Oil Change",
            interval_miles=Decimal("7500"),
            last_completed_miles=Decimal("30000"),
            last_completed_date=datetime.now(UTC) - timedelta(days=60),
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
        
        completion_date = datetime.now(UTC)
        completion_miles = Decimal("37500")
        
        completion = PMCompletion(
            schedule_id=schedule.id,
            completed_at=completion_date,
            odometer_miles=completion_miles,
            notes="Completed on schedule",
        )
        db.add(completion)
        
        schedule.last_completed_date = completion_date
        schedule.last_completed_miles = completion_miles
        db.commit()
        db.refresh(schedule)
        
        assert schedule.last_completed_miles == Decimal("37500")
        assert len(schedule.completions) == 1
        
        status_detail = calculate_pm_status(
            schedule,
            current_miles=Decimal("38000"),
            current_hours=None,
            current_date=datetime.now(UTC),
        )
        
        assert status_detail.next_due_miles == Decimal("45000")
        assert status_detail.status == PMStatus.OK
    finally:
        db.rollback()
        db.close()


def test_combined_interval_logic():
    """Test PM schedule with multiple interval types."""
    db = next(get_db())
    
    try:
        asset = Asset(
            organization_id=_org(db).id,
            vin=unique_vin(),
            make_model="Test Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=Decimal("30000"),
            current_location="Test Warehouse",
            current_custody_type="Warehouse Depot",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
        schedule = MaintenanceSchedule(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            name="Transmission Service",
            interval_miles=Decimal("60000"),
            interval_months=24,
            last_completed_miles=Decimal("40000"),
            last_completed_date=datetime.now(UTC) - timedelta(days=700),
        )
        db.add(schedule)
        db.commit()
        
        reading = MeterReading(
            asset_id=asset.id,
            odometer_miles=Decimal("90000"),
            reading_date=datetime.now(UTC),
            source="manual",
        )
        db.add(reading)
        db.commit()
        
        status_detail = calculate_pm_status(
            schedule,
            current_miles=Decimal("90000"),
            current_hours=None,
            current_date=datetime.now(UTC),
        )
        
        assert status_detail.next_due_miles == Decimal("100000")
        assert status_detail.status in [PMStatus.OK, PMStatus.DUE_SOON]
    finally:
        db.rollback()
        db.close()


def test_meter_reading_history():
    """Test that meter readings preserve history."""
    db = next(get_db())
    
    try:
        asset = Asset(
            organization_id=_org(db).id,
            vin=unique_vin(),
            make_model="Test Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=Decimal("30000"),
            current_location="Test Warehouse",
            current_custody_type="Warehouse Depot",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
        readings = [
            MeterReading(
                asset_id=asset.id,
                odometer_miles=Decimal("30000"),
                reading_date=datetime.now(UTC) - timedelta(days=30),
                source="manual",
            ),
            MeterReading(
                asset_id=asset.id,
                odometer_miles=Decimal("32000"),
                reading_date=datetime.now(UTC) - timedelta(days=15),
                source="manual",
            ),
            MeterReading(
                asset_id=asset.id,
                odometer_miles=Decimal("34000"),
                reading_date=datetime.now(UTC),
                source="manual",
            ),
        ]
        
        for r in readings:
            db.add(r)
        db.commit()
        
        latest = get_latest_meter_reading(db, asset.id)
        assert latest is not None
        assert latest.odometer_miles == Decimal("34000")
        
        all_readings = db.query(MeterReading).filter_by(asset_id=asset.id).all()
        assert len(all_readings) == 3
    finally:
        db.rollback()
        db.close()
