"""
Multi-tenancy migration and seed script.

This script:
1. Creates a default "Internal" organization
2. Migrates existing users to the default organization
3. Migrates all existing assets, deployments, work orders, etc. to the default organization
4. Creates sample organizations for testing
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.database import SessionLocal, get_db, rebind_engine
from app.models import (
    Asset,
    Deployment,
    Inspection,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
    Organization,
    User,
    UserRole,
)


def create_default_organization(db: Session) -> Organization:
    """Create the default internal organization."""
    # Check if default org already exists
    existing = db.scalar(select(Organization).where(Organization.slug == "internal"))
    if existing:
        print(f"✓ Default organization already exists: {existing.name}")
        return existing
    
    org = Organization(
        id=uuid.uuid4(),
        name="Internal Fleet Operations",
        slug="internal",
        org_type="internal",
        is_active=True,
        settings=None,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    print(f"✓ Created default organization: {org.name} ({org.id})")
    return org


def migrate_users(db: Session, default_org: Organization) -> None:
    """Migrate existing users to default organization."""
    # Get users without organization_id
    users = db.scalars(select(User)).all()
    
    migrated = 0
    for user in users:
        if not hasattr(user, 'organization_id') or user.organization_id is None:
            user.organization_id = default_org.id
            migrated += 1
    
    if migrated > 0:
        db.commit()
        print(f"✓ Migrated {migrated} users to default organization")
    else:
        print("✓ All users already have an organization")


def migrate_assets(db: Session, default_org: Organization) -> None:
    """Migrate existing assets to default organization."""
    # Check if assets table has organization_id column
    try:
        assets = db.scalars(select(Asset)).all()
        migrated = 0
        for asset in assets:
            if not hasattr(asset, 'organization_id') or asset.organization_id is None:
                asset.organization_id = default_org.id
                migrated += 1
        
        if migrated > 0:
            db.commit()
            print(f"✓ Migrated {migrated} assets to default organization")
        else:
            print("✓ All assets already have an organization")
    except Exception as e:
        print(f"⚠ Could not migrate assets (column may not exist yet): {e}")


def migrate_deployments(db: Session, default_org: Organization) -> None:
    """Migrate existing deployments to default organization."""
    try:
        deployments = db.scalars(select(Deployment)).all()
        migrated = 0
        for deployment in deployments:
            if not hasattr(deployment, 'organization_id') or deployment.organization_id is None:
                deployment.organization_id = default_org.id
                migrated += 1
        
        if migrated > 0:
            db.commit()
            print(f"✓ Migrated {migrated} deployments to default organization")
        else:
            print("✓ All deployments already have an organization")
    except Exception as e:
        print(f"⚠ Could not migrate deployments: {e}")


def migrate_work_orders(db: Session, default_org: Organization) -> None:
    """Migrate existing work orders to default organization."""
    try:
        work_orders = db.scalars(select(MaintenanceWorkOrder)).all()
        migrated = 0
        for wo in work_orders:
            if not hasattr(wo, 'organization_id') or wo.organization_id is None:
                wo.organization_id = default_org.id
                migrated += 1
        
        if migrated > 0:
            db.commit()
            print(f"✓ Migrated {migrated} work orders to default organization")
        else:
            print("✓ All work orders already have an organization")
    except Exception as e:
        print(f"⚠ Could not migrate work orders: {e}")


def migrate_inspections(db: Session, default_org: Organization) -> None:
    """Migrate existing inspections to default organization."""
    try:
        inspections = db.scalars(select(Inspection)).all()
        migrated = 0
        for inspection in inspections:
            if not hasattr(inspection, 'organization_id') or inspection.organization_id is None:
                inspection.organization_id = default_org.id
                migrated += 1
        
        if migrated > 0:
            db.commit()
            print(f"✓ Migrated {migrated} inspections to default organization")
        else:
            print("✓ All inspections already have an organization")
    except Exception as e:
        print(f"⚠ Could not migrate inspections: {e}")


def migrate_pm_schedules(db: Session, default_org: Organization) -> None:
    """Migrate existing PM schedules to default organization."""
    try:
        schedules = db.scalars(select(MaintenanceSchedule)).all()
        migrated = 0
        for schedule in schedules:
            if not hasattr(schedule, 'organization_id') or schedule.organization_id is None:
                schedule.organization_id = default_org.id
                migrated += 1
        
        if migrated > 0:
            db.commit()
            print(f"✓ Migrated {migrated} PM schedules to default organization")
        else:
            print("✓ All PM schedules already have an organization")
    except Exception as e:
        print(f"⚠ Could not migrate PM schedules: {e}")


def create_sample_organizations(db: Session) -> list[Organization]:
    """Create sample customer/agency organizations for testing."""
    sample_orgs = [
        {
            "name": "LAPD - Los Angeles Police Department",
            "slug": "lapd",
            "org_type": "agency",
        },
        {
            "name": "City of Phoenix Fleet",
            "slug": "phoenix-fleet",
            "org_type": "customer",
        },
    ]
    
    created = []
    for org_data in sample_orgs:
        existing = db.scalar(select(Organization).where(Organization.slug == org_data["slug"]))
        if not existing:
            org = Organization(
                id=uuid.uuid4(),
                name=org_data["name"],
                slug=org_data["slug"],
                org_type=org_data["org_type"],
                is_active=True,
            )
            db.add(org)
            created.append(org)
    
    if created:
        db.commit()
        for org in created:
            db.refresh(org)
            print(f"✓ Created sample organization: {org.name}")
    
    return created


def create_sample_customer_user(db: Session, org: Organization) -> User:
    """Create a sample customer user for testing."""
    existing = db.scalar(select(User).where(User.email == f"user@{org.slug}.com"))
    if existing:
        print(f"✓ Sample customer user already exists: {existing.email}")
        return existing
    
    # Customers are scoped to one agency; without it they would see no assets.
    from app.models import Agency

    agency = db.scalar(
        select(Agency).where(Agency.organization_id == org.id, Agency.is_archived.is_(False)).order_by(Agency.name)
    )

    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        agency_id=agency.id if agency else None,
        email=f"user@{org.slug}.com",
        hashed_password=hash_password("password123"),
        full_name=f"{org.name} User",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if agency is None:
        print(f"! {user.email} has no agency yet and will see no assets until one is assigned.")
    print(f"✓ Created sample customer user: {user.email}")
    return user


def run_migration():
    """Run the full multi-tenancy migration."""
    print("\n" + "=" * 60)
    print("MULTI-TENANCY MIGRATION")
    print("=" * 60 + "\n")
    
    get_settings.cache_clear()
    rebind_engine()
    db = SessionLocal()
    
    try:
        # Step 1: Create default organization
        print("\n1. Creating default organization...")
        default_org = create_default_organization(db)
        
        # Step 2: Migrate existing data
        print("\n2. Migrating existing data to default organization...")
        migrate_users(db, default_org)
        migrate_assets(db, default_org)
        migrate_deployments(db, default_org)
        migrate_work_orders(db, default_org)
        migrate_inspections(db, default_org)
        migrate_pm_schedules(db, default_org)
        
        # Step 3: Create sample organizations (optional)
        print("\n3. Creating sample organizations for testing...")
        sample_orgs = create_sample_organizations(db)
        
        # Step 4: Create sample customer users
        if sample_orgs:
            print("\n4. Creating sample customer users...")
            for org in sample_orgs:
                create_sample_customer_user(db, org)
        
        print("\n" + "=" * 60)
        print("✅ MIGRATION COMPLETE")
        print("=" * 60 + "\n")
        
        print("Summary:")
        print(f"  - Default organization: {default_org.name}")
        print(f"  - Sample organizations: {len(sample_orgs)}")
        print("\nNext steps:")
        print("  1. Restart the backend server")
        print("  2. Test login with existing credentials")
        print("  3. Verify data isolation between organizations")
        print()
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_migration()
