from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import hash_password
from app.models import (
    Agency,
    Asset,
    AssetType,
    CustodyType,
    Deployment,
    DeploymentStatus,
    MaintenanceWorkOrder,
    RepairChannel,
    User,
    UserRole,
    Vendor,
    Warehouse,
    WorkOrderStatus,
)
from app.schemas import CustodyUpdate
from app.services import total_repair_cost

CUSTODY_BY_VIN: dict[str, CustodyUpdate] = {
    "1ALPRTRL000000001": CustodyUpdate(
        custody_type=CustodyType.CUSTOMER_AGENCY,
        location="Phoenix PD — I-10 Corridor",
        notes="Hot-spot coverage for weekend event traffic.",
    ),
    "1ALPRTRL000000002": CustodyUpdate(
        custody_type=CustodyType.WAREHOUSE_DEPOT,
        location="Mesa Yard A — Warehouse Depot",
        notes="Held for solar repair before redeploy.",
    ),
    "1ALPRTRL000000003": CustodyUpdate(
        custody_type=CustodyType.CUSTOMER_AGENCY,
        location="AZ DPS — SR-51 / Thomas Rd",
        notes="Lane 1 + lane 2 cameras live.",
    ),
    "1XPWD40X1ED215001": CustodyUpdate(
        custody_type=CustodyType.IN_TRANSIT,
        location="Flagstaff inbound — Mesa Yard A",
        carrier_name="XPO Logistics",
        tracking_code="XPO-884219331",
        notes="Loaded ALPR trailer transfer.",
    ),
    "1XKYDP9X5NJ123002": CustodyUpdate(
        custody_type=CustodyType.WAREHOUSE_DEPOT,
        location="Tempe Shop Bay 2 — Warehouse Depot",
        notes="Aftertreatment hold.",
    ),
    "3AKJHHDR8JSBA3003": CustodyUpdate(
        custody_type=CustodyType.WAREHOUSE_DEPOT,
        location="Tucson Distribution Hub",
        notes="Regional linehaul rotation home base.",
    ),
    "1FTFW1E84PFA41001": CustodyUpdate(
        custody_type=CustodyType.CUSTOMER_AGENCY,
        location="Scottsdale PD — Field Ops",
        notes="Technician ride-along assignment.",
    ),
    "1GNSKCKD5PR123002": CustodyUpdate(
        custody_type=CustodyType.WAREHOUSE_DEPOT,
        location="HQ Motor Pool — Goodyear",
        notes="Reserve PPV.",
    ),
    "5N1AT3BB7PC140003": CustodyUpdate(
        custody_type=CustodyType.WAREHOUSE_DEPOT,
        location="Chandler Service Lane — Warehouse Depot",
        notes="High-cost unit under evaluation.",
    ),
}


def seed_if_empty(db: Session) -> None:
    if db.scalar(select(User).limit(1)):
        apply_phase2_backfill(db)
        return

    # Create default organization first
    from app.models import Organization
    default_org = Organization(
        name="Internal Fleet Operations",
        slug="internal",
        org_type="internal",
        is_active=True,
    )
    db.add(default_org)
    db.flush()

    # Only create technician user - primary admin is created separately
    # Seeds write canonical roles only; legacy values are migrated away at startup.
    tech = User(
        organization_id=default_org.id,
        email="tech@example.com",
        hashed_password=hash_password("ChangeMe123!"),
        full_name="Marcus Hale",
        role=UserRole.TECHNICIAN,
    )
    db.add(tech)
    db.flush()

    now = datetime.now(UTC)
    assets_spec = [
        {
            "vin": "1ALPRTRL000000001",
            "make_model": "Rekor Scout 3 / Dual-Camera Trailer",
            "cost": Decimal("84200.00"),
            "location": "Phoenix PD — I-10 Corridor",
            "type": AssetType.ALPR_TRAILER,
            "status": DeploymentStatus.DEPLOYED,
        },
        {
            "vin": "1ALPRTRL000000002",
            "make_model": "Flock Safety Falcon Trailer",
            "cost": Decimal("76850.00"),
            "location": "Mesa Yard A — Warehouse Depot",
            "type": AssetType.ALPR_TRAILER,
            "status": DeploymentStatus.STAGED,
        },
        {
            "vin": "1ALPRTRL000000003",
            "make_model": "PlateSmart Patrol Trailer",
            "cost": Decimal("71340.00"),
            "location": "AZ DPS — SR-51 / Thomas Rd",
            "type": AssetType.ALPR_TRAILER,
            "status": DeploymentStatus.DEPLOYED,
        },
        {
            "vin": "1XPWD40X1ED215001",
            "make_model": "Peterbilt 579 Day Cab",
            "cost": Decimal("168400.00"),
            "location": "Flagstaff inbound — Mesa Yard A",
            "type": AssetType.SEMI_TRUCK,
            "status": DeploymentStatus.IN_TRANSIT,
        },
        {
            "vin": "1XKYDP9X5NJ123002",
            "make_model": "Kenworth T680 Sleeper",
            "cost": Decimal("192750.00"),
            "location": "Tempe Shop Bay 2 — Warehouse Depot",
            "type": AssetType.SEMI_TRUCK,
            "status": DeploymentStatus.IDLE,
        },
        {
            "vin": "3AKJHHDR8JSBA3003",
            "make_model": "Freightliner Cascadia 126",
            "cost": Decimal("175900.00"),
            "location": "Tucson Distribution Hub",
            "type": AssetType.SEMI_TRUCK,
            "status": DeploymentStatus.DEPLOYED,
        },
        {
            "vin": "1FTFW1E84PFA41001",
            "make_model": "Ford F-150 SuperCrew",
            "cost": Decimal("48620.00"),
            "location": "Scottsdale PD — Field Ops",
            "type": AssetType.FLEET_VEHICLE,
            "status": DeploymentStatus.DEPLOYED,
        },
        {
            "vin": "1GNSKCKD5PR123002",
            "make_model": "Chevrolet Tahoe PPV",
            "cost": Decimal("52980.00"),
            "location": "HQ Motor Pool — Goodyear",
            "type": AssetType.FLEET_VEHICLE,
            "status": DeploymentStatus.STORED,
        },
        {
            "vin": "5N1AT3BB7PC140003",
            "make_model": "Nissan Rogue AWD",
            "cost": Decimal("33410.00"),
            "location": "Chandler Service Lane — Warehouse Depot",
            "type": AssetType.FLEET_VEHICLE,
            "status": DeploymentStatus.IDLE,
        },
    ]

    assets: list[Asset] = []
    for spec in assets_spec:
        custody = CUSTODY_BY_VIN[spec["vin"]]
        asset = Asset(
            organization_id=default_org.id,
            vin=spec["vin"],
            make_model=spec["make_model"],
            initial_purchase_cost=spec["cost"],
            current_location=spec["location"],
            asset_type=spec["type"],
            current_custody_type=custody.custody_type,
            carrier_name=custody.carrier_name,
            tracking_code=custody.tracking_code,
        )
        db.add(asset)
        db.flush()
        assets.append(asset)
        db.add(
            Deployment(
                organization_id=default_org.id,
                asset_id=asset.id,
                location="Vendor delivery — Goodyear AZ",
                status=DeploymentStatus.STAGED,
                custody_type=CustodyType.WAREHOUSE_DEPOT,
                started_at=now - timedelta(days=180),
                ended_at=now - timedelta(days=170),
                notes="Accepted after incoming inspection.",
                created_by_id=tech.id,
            )
        )
        db.add(
            Deployment(
                organization_id=default_org.id,
                asset_id=asset.id,
                location=spec["location"],
                status=spec["status"],
                custody_type=custody.custody_type,
                carrier_name=custody.carrier_name,
                tracking_code=custody.tracking_code,
                started_at=now - timedelta(days=12),
                ended_at=None,
                notes=custody.notes or "Current assignment.",
                created_by_id=tech.id,
            )
        )

    db.add_all(
        [
            MaintenanceWorkOrder(
                organization_id=default_org.id,
                asset_id=assets[1].id,
                title="Solar array output below spec",
                description="Replace two 330W panels and reseal junction box.",
                status=WorkOrderStatus.IN_PROGRESS,
                opened_at=now - timedelta(days=3),
                downtime_hours=Decimal("18.50"),
                labor_cost=Decimal("640.00"),
                parts_cost=Decimal("890.00"),
                created_by_id=tech.id,
            ),
            MaintenanceWorkOrder(
                organization_id=default_org.id,
                asset_id=assets[4].id,
                title="DPF regen failure + DEF leak",
                description="Aftertreatment service; truck held in Shop Bay 2.",
                status=WorkOrderStatus.WAITING_PARTS,
                opened_at=now - timedelta(days=5),
                downtime_hours=Decimal("42.00"),
                labor_cost=Decimal("1280.00"),
                parts_cost=Decimal("2140.00"),
                created_by_id=tech.id,
            ),
            MaintenanceWorkOrder(
                organization_id=default_org.id,
                asset_id=assets[0].id,
                title="ANPR camera realignment",
                description="Lane 2 camera pitch corrected after wind event.",
                status=WorkOrderStatus.COMPLETED,
                opened_at=now - timedelta(days=40),
                closed_at=now - timedelta(days=39),
                downtime_hours=Decimal("4.25"),
                labor_cost=Decimal("180.00"),
                parts_cost=Decimal("0.00"),
                created_by_id=tech.id,
            ),
            MaintenanceWorkOrder(
                organization_id=default_org.id,
                asset_id=assets[5].id,
                title="PM-B 30k service",
                description="Scheduled preventive maintenance.",
                status=WorkOrderStatus.COMPLETED,
                opened_at=now - timedelta(days=55),
                closed_at=now - timedelta(days=55, hours=-8),
                downtime_hours=Decimal("8.30"),
                labor_cost=Decimal("960.00"),
                parts_cost=Decimal("410.00"),
                created_by_id=tech.id,
            ),
        ]
    )
    db.flush()
    _ensure_lemon_costs(db, assets[8], tech.id, now, default_org.id)
    seed_directories(db)
    seed_analytics_history(db)
    db.commit()


def apply_phase2_backfill(db: Session) -> None:
    assets = db.scalars(select(Asset).options(selectinload(Asset.work_orders), selectinload(Asset.deployments))).all()
    if not assets:
        return
    # Use tech user for historical data assignment
    tech = db.scalar(select(User).where(User.email == "tech@example.com"))
    now = datetime.now(UTC)
    changed = False
    for asset in assets:
        payload = CUSTODY_BY_VIN.get(asset.vin)
        if payload and asset.current_custody_type is None:
            open_row = next((row for row in asset.deployments if row.ended_at is None), None)
            if open_row:
                open_row.custody_type = payload.custody_type
                open_row.carrier_name = payload.carrier_name
                open_row.tracking_code = payload.tracking_code
                open_row.location = payload.location
            asset.current_location = payload.location
            asset.current_custody_type = payload.custody_type
            asset.carrier_name = payload.carrier_name
            asset.tracking_code = payload.tracking_code
            changed = True
        if asset.vin == "5N1AT3BB7PC140003" and tech:
            before = total_repair_cost(asset)
            _ensure_lemon_costs(db, asset, tech.id, now, asset.organization_id)
            if total_repair_cost(asset) != before:
                changed = True
    if changed:
        db.commit()
    seed_directories(db)
    seed_analytics_history(db)


def _ensure_lemon_costs(db: Session, asset: Asset, tech_id, now: datetime, org_id: UUID) -> None:
    purchase = Decimal(asset.initial_purchase_cost)
    if total_repair_cost(asset) >= purchase:
        return
    titles = {wo.title for wo in asset.work_orders}
    extras = [
        MaintenanceWorkOrder(
            organization_id=org_id,
            asset_id=asset.id,
            title="A/C compressor replacement",
            description="Cabin cooling failed during summer patrol rotation.",
            status=WorkOrderStatus.OPEN,
            opened_at=now - timedelta(days=1),
            downtime_hours=Decimal("6.00"),
            labor_cost=Decimal("220.00"),
            parts_cost=Decimal("480.00"),
            created_by_id=tech_id,
        ),
        MaintenanceWorkOrder(
            organization_id=org_id,
            asset_id=asset.id,
            title="CVT transmission replacement",
            description="Repeat failure after 41k miles; unit out of powertrain warranty.",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(days=70),
            closed_at=now - timedelta(days=64),
            downtime_hours=Decimal("86.00"),
            labor_cost=Decimal("4200.00"),
            parts_cost=Decimal("14700.00"),
            created_by_id=tech_id,
        ),
        MaintenanceWorkOrder(
            organization_id=org_id,
            asset_id=asset.id,
            title="Unibody and airbag repair after intersection impact",
            description="Structural and SRS work quoted above remaining book value.",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(days=120),
            closed_at=now - timedelta(days=108),
            downtime_hours=Decimal("140.00"),
            labor_cost=Decimal("6100.00"),
            parts_cost=Decimal("9800.00"),
            created_by_id=tech_id,
        ),
    ]
    for wo in extras:
        if wo.title not in titles:
            db.add(wo)
            asset.work_orders.append(wo)


def seed_analytics_history(db: Session) -> None:
    if db.scalar(select(MaintenanceWorkOrder).where(MaintenanceWorkOrder.vendor_name.is_not(None))):
        return

    tech = db.scalar(select(User).where(User.email == "tech@example.com"))
    if tech is None:
        return

    pm = db.scalar(select(User).where(User.email == "pm@example.com"))
    if pm is None:
        pm = User(
            organization_id=tech.organization_id,
            email="pm@example.com",
            hashed_password=hash_password("ChangeMe123!"),
            full_name="Jordan Ellis",
            role=UserRole.FLEET_MANAGER,
        )
        db.add(pm)
        db.flush()

    assets = {asset.vin: asset for asset in db.scalars(select(Asset)).all()}
    now = datetime.now(UTC)
    tech_id = tech.id

    specs: list[dict] = []
    trailers = [assets[vin] for vin in ("1ALPRTRL000000001", "1ALPRTRL000000002", "1ALPRTRL000000003") if vin in assets]
    semis = [assets[vin] for vin in ("1XPWD40X1ED215001", "1XKYDP9X5NJ123002", "3AKJHHDR8JSBA3003") if vin in assets]
    vehicles = [assets[vin] for vin in ("1FTFW1E84PFA41001", "1GNSKCKD5PR123002", "5N1AT3BB7PC140003") if vin in assets]

    for index, day in enumerate((48, 44, 41)):
        if trailers:
            specs.append(
                {
                    "asset": trailers[index % len(trailers)],
                    "title": f"AN-{100 + index} prior battery pack",
                    "component": "Batteries",
                    "days": day,
                    "hours": 14,
                    "labor": Decimal("380"),
                    "parts": Decimal("620"),
                    "channel": RepairChannel.INTERNAL,
                    "vendor": None,
                }
            )
    for index, day in enumerate((22, 18, 14, 11, 9, 6, 4, 2)):
        if trailers:
            specs.append(
                {
                    "asset": trailers[index % len(trailers)],
                    "title": f"AN-{200 + index} battery voltage collapse",
                    "component": "Batteries",
                    "days": day,
                    "hours": 16,
                    "labor": Decimal("420"),
                    "parts": Decimal("890"),
                    "channel": RepairChannel.INTERNAL,
                    "vendor": None,
                }
            )
    if trailers:
        specs.extend(
            [
                {
                    "asset": trailers[0],
                    "title": "AN-310 Rekor camera module (vendor)",
                    "component": "ALPR Cameras",
                    "days": 12,
                    "hours": 72,
                    "labor": Decimal("0"),
                    "parts": Decimal("2140"),
                    "channel": RepairChannel.THIRD_PARTY,
                    "vendor": "Rekor Field Services",
                },
                {
                    "asset": trailers[1],
                    "title": "AN-311 solar controller (internal)",
                    "component": "Solar Panels",
                    "days": 8,
                    "hours": 9,
                    "labor": Decimal("240"),
                    "parts": Decimal("310"),
                    "channel": RepairChannel.INTERNAL,
                    "vendor": None,
                },
            ]
        )
    if semis:
        specs.extend(
            [
                {
                    "asset": semis[1],
                    "title": "AN-410 DPF/DEF aftertreatment (vendor)",
                    "component": "Engine",
                    "days": 15,
                    "hours": 96,
                    "labor": Decimal("0"),
                    "parts": Decimal("4280"),
                    "channel": RepairChannel.THIRD_PARTY,
                    "vendor": "Penske Truck Leasing Service",
                },
                {
                    "asset": semis[0],
                    "title": "AN-411 brake job (internal)",
                    "component": "Brakes",
                    "days": 20,
                    "hours": 11,
                    "labor": Decimal("640"),
                    "parts": Decimal("480"),
                    "channel": RepairChannel.INTERNAL,
                    "vendor": None,
                },
                {
                    "asset": semis[2],
                    "title": "AN-412 lighting harness (internal)",
                    "component": "Lights",
                    "days": 7,
                    "hours": 6,
                    "labor": Decimal("180"),
                    "parts": Decimal("95"),
                    "channel": RepairChannel.INTERNAL,
                    "vendor": None,
                },
            ]
        )
    if vehicles:
        specs.extend(
            [
                {
                    "asset": vehicles[0],
                    "title": "AN-510 body shop (vendor)",
                    "component": "Cabin",
                    "days": 25,
                    "hours": 80,
                    "labor": Decimal("0"),
                    "parts": Decimal("2650"),
                    "channel": RepairChannel.THIRD_PARTY,
                    "vendor": "Steele Collision Center",
                },
                {
                    "asset": vehicles[1],
                    "title": "AN-511 fluid service (internal)",
                    "component": "Fluids",
                    "days": 10,
                    "hours": 4,
                    "labor": Decimal("120"),
                    "parts": Decimal("85"),
                    "channel": RepairChannel.INTERNAL,
                    "vendor": None,
                },
                {
                    "asset": vehicles[2],
                    "title": "AN-512 tire replacement (internal)",
                    "component": "Tires",
                    "days": 5,
                    "hours": 3,
                    "labor": Decimal("90"),
                    "parts": Decimal("640"),
                    "channel": RepairChannel.INTERNAL,
                    "vendor": None,
                },
            ]
        )

    for spec in specs:
        opened = now - timedelta(days=spec["days"])
        closed = opened + timedelta(hours=spec["hours"])
        db.add(
            MaintenanceWorkOrder(
                organization_id=spec["asset"].organization_id,
                asset_id=spec["asset"].id,
                title=spec["title"],
                description=f"{spec['component']} failure recorded for analytics.",
                status=WorkOrderStatus.COMPLETED,
                opened_at=opened,
                closed_at=closed,
                downtime_hours=Decimal(spec["hours"]),
                labor_cost=spec["labor"],
                parts_cost=spec["parts"],
                failed_component=spec["component"],
                repair_channel=spec["channel"],
                vendor_name=spec["vendor"],
                created_by_id=tech_id,
            )
        )
    db.commit()


def seed_directories(db: Session) -> None:
    warehouses = [
        ("Mesa Yard A — Warehouse Depot", "Mesa, AZ"),
        ("Tempe Shop Bay 2 — Warehouse Depot", "Tempe, AZ"),
        ("Tucson Distribution Hub", "Tucson, AZ"),
        ("HQ Motor Pool — Goodyear", "Goodyear, AZ"),
        ("Chandler Service Lane — Warehouse Depot", "Chandler, AZ"),
    ]
    agencies = [
        ("Phoenix PD", "Law Enforcement", "I-10 Corridor desk"),
        ("AZ DPS", "Law Enforcement", "District 1"),
        ("Scottsdale PD", "Law Enforcement", "Field Ops"),
    ]
    vendors = [
        ("XPO Logistics", "3PL / linehaul"),
        ("Rekor Field Services", "ALPR cameras"),
        ("Penske Truck Leasing Service", "Powertrain / aftertreatment"),
        ("Steele Collision Center", "Body and cabin"),
    ]
    existing_wh = {row.name for row in db.scalars(select(Warehouse)).all()}
    for name, address in warehouses:
        if name not in existing_wh:
            db.add(Warehouse(name=name, address=address))
    existing_ag = {row.name for row in db.scalars(select(Agency)).all()}
    for name, agency_type, contact in agencies:
        if name not in existing_ag:
            db.add(Agency(name=name, agency_type=agency_type, contact_name=contact))
    existing_vn = {row.name for row in db.scalars(select(Vendor)).all()}
    for name, specialty in vendors:
        if name not in existing_vn:
            db.add(Vendor(name=name, specialty=specialty))
    db.flush()
    by_wh = {row.name: row for row in db.scalars(select(Warehouse)).all()}
    by_ag = {row.name: row for row in db.scalars(select(Agency)).all()}
    for asset in db.scalars(select(Asset)).all():
        if asset.warehouse_id is None and asset.current_location in by_wh:
            asset.warehouse_id = by_wh[asset.current_location].id
        if asset.agency_id is None:
            for name, agency in by_ag.items():
                if name in (asset.current_location or ""):
                    asset.agency_id = agency.id
                    break
    db.commit()
