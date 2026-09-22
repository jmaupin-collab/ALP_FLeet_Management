"""Quick seed data with assets, deployments, warehouses for testing features"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models import (
    Agency,
    Asset,
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    Deployment,
    DeploymentStatus,
    Organization,
    User,
    UserRole,
    Warehouse,
)


def seed_quick(db: Session) -> None:
    """Create test data: organization, users, warehouses, agencies, assets, and active deployments"""
    if db.scalar(select(User).limit(1)):
        print("Database already seeded")
        return
    
    # Create default organization
    default_org = Organization(
        name="Internal Fleet Operations",
        slug="internal",
        org_type="internal",
        is_active=True,
    )
    db.add(default_org)
    db.flush()
    
    # Create users
    admin = User(
        organization_id=default_org.id,
        email="admin@example.com",
        hashed_password=hash_password("ChangeMe123!"),
        full_name="Avery Chen",
        role=UserRole.ORG_ADMIN,
    )
    tech = User(
        organization_id=default_org.id,
        email="tech@example.com",
        hashed_password=hash_password("ChangeMe123!"),
        full_name="Marcus Hale",
        role=UserRole.TECHNICIAN,
    )
    db.add_all([admin, tech])
    db.flush()
    
    # Create warehouses
    warehouses = [
        Warehouse(
            name="Phoenix Main Depot",
            address="1234 Industrial Pkwy",
            city="Phoenix",
            state="AZ",
            zip_code="85001",
            latitude=Decimal("33.4484"),
            longitude=Decimal("-112.0740"),
        ),
        Warehouse(
            name="Mesa Service Center",
            address="5678 Service Rd",
            city="Mesa",
            state="AZ",
            zip_code="85201",
            latitude=Decimal("33.4152"),
            longitude=Decimal("-111.8315"),
        ),
        Warehouse(
            name="Tucson Distribution Hub",
            address="9012 Distribution Dr",
            city="Tucson",
            state="AZ",
            zip_code="85701",
            latitude=Decimal("32.2226"),
            longitude=Decimal("-110.9747"),
        ),
    ]
    db.add_all(warehouses)
    db.flush()
    
    # Create agencies
    agencies = [
        Agency(
            name="Phoenix Police Department",
            site_name="Traffic Division",
            address="620 W Washington St",
            city="Phoenix",
            state="AZ",
            zip_code="85003",
            latitude=Decimal("33.4484"),
            longitude=Decimal("-112.0740"),
        ),
        Agency(
            name="Arizona Department of Public Safety",
            site_name="Highway Patrol - North",
            address="2102 W Encanto Blvd",
            city="Phoenix",
            state="AZ",
            zip_code="85009",
            latitude=Decimal("33.4700"),
            longitude=Decimal("-112.0950"),
        ),
        Agency(
            name="Scottsdale Police Department",
            site_name="Field Operations",
            address="8401 E Indian School Rd",
            city="Scottsdale",
            state="AZ",
            zip_code="85251",
            latitude=Decimal("33.4942"),
            longitude=Decimal("-111.9261"),
        ),
    ]
    db.add_all(agencies)
    db.flush()
    
    now = datetime.now(UTC)
    
    # Create assets with deployments
    assets_data = [
        {
            "vin": "1ALPR001",
            "make_model": "Rekor Scout ALPR Trailer",
            "cost": Decimal("84200.00"),
            "type": AssetType.ALPR_TRAILER,
            "warehouse": warehouses[0],
            "agency": agencies[0],
            "deployed": True,
        },
        {
            "vin": "1ALPR002",
            "make_model": "Flock Safety Falcon Trailer",
            "cost": Decimal("76850.00"),
            "type": AssetType.ALPR_TRAILER,
            "warehouse": warehouses[1],
            "agency": agencies[1],
            "deployed": True,
        },
        {
            "vin": "1ALPR003",
            "make_model": "Genetec AutoVu Mobile ALPR",
            "cost": Decimal("68500.00"),
            "type": AssetType.ALPR_TRAILER,
            "warehouse": warehouses[0],
            "agency": None,
            "deployed": False,
        },
        {
            "vin": "1SEMI001",
            "make_model": "Peterbilt 579 Day Cab",
            "cost": Decimal("145000.00"),
            "type": AssetType.SEMI_TRUCK,
            "warehouse": warehouses[1],
            "agency": None,
            "deployed": False,
        },
        {
            "vin": "1FLEET001",
            "make_model": "Ford F-150 XLT Crew Cab",
            "cost": Decimal("42500.00"),
            "type": AssetType.FLEET_VEHICLE,
            "warehouse": warehouses[0],
            "agency": agencies[2],
            "deployed": True,
        },
    ]
    
    for data in assets_data:
        if data["deployed"]:
            # Active deployment
            asset = Asset(
                organization_id=default_org.id,
                vin=data["vin"],
                make_model=data["make_model"],
                initial_purchase_cost=data["cost"],
                current_location=f"{data['agency'].name} - {data['agency'].site_name}",
                asset_type=data["type"],
                current_custody_type=CustodyType.CUSTOMER_AGENCY,
                operational_status=AssetOperationalStatus.DEPLOYED,
                agency_id=data["agency"].id,
                created_by_id=admin.id,
            )
            db.add(asset)
            db.flush()
            
            deployment = Deployment(
                organization_id=default_org.id,
                asset_id=asset.id,
                location=f"{data['agency'].name} - {data['agency'].site_name}",
                status=DeploymentStatus.ACTIVE,
                custody_type=CustodyType.CUSTOMER_AGENCY,
                started_at=now - timedelta(days=30),
                notes=f"Active deployment to {data['agency'].name}",
                created_by_id=admin.id,
            )
            db.add(deployment)
        else:
            # Available in warehouse
            asset = Asset(
                organization_id=default_org.id,
                vin=data["vin"],
                make_model=data["make_model"],
                initial_purchase_cost=data["cost"],
                current_location=data["warehouse"].name,
                asset_type=data["type"],
                current_custody_type=CustodyType.WAREHOUSE_DEPOT,
                operational_status=AssetOperationalStatus.AVAILABLE,
                warehouse_id=data["warehouse"].id,
                created_by_id=admin.id,
            )
            db.add(asset)
    
    db.commit()
    
    print(f"✓ Created organization: {default_org.name}")
    print(f"✓ Created {len(warehouses)} warehouses")
    print(f"✓ Created {len(agencies)} agencies")
    print(f"✓ Created {len(assets_data)} assets")
    print(f"✓ Created 3 active deployments")
    print(f"\n✅ Test data ready!")
    print(f"\nLogin credentials:")
    print(f"  Email: admin@example.com")
    print(f"  Password: ChangeMe123!")
