"""Test multi-tenancy and data isolation."""

import uuid
from datetime import UTC, datetime

from pathlib import Path

from app.auth import hash_password
from app.config import get_settings
from app.database import dispose_engine, ensure_schema, get_db, rebind_engine
from app.models import Asset, AssetType, Organization, User, UserRole
from app.rbac import can_access_organization, filter_assets_by_access, require_asset_access
from fastapi import HTTPException
import pytest

MT_DB = Path(__file__).resolve().parent / "_multitenant_test.db"
MT_DB_URL = f"sqlite:///{MT_DB}"


def setup_module():
    """Use an isolated SQLite file so these tests do not touch fleet.db."""
    get_settings.cache_clear()
    rebind_engine(MT_DB_URL)
    ensure_schema()


def teardown_module():
    dispose_engine()
    MT_DB.unlink(missing_ok=True)


def test_organization_data_isolation():
    """Test that organizations cannot access each other's data."""
    db = next(get_db())
    
    try:
        # Create two organizations
        org1 = Organization(
            id=uuid.uuid4(),
            name="Organization One",
            slug="org-one",
            org_type="internal",
            is_active=True,
        )
        org2 = Organization(
            id=uuid.uuid4(),
            name="Organization Two",
            slug="org-two",
            org_type="customer",
            is_active=True,
        )
        db.add_all([org1, org2])
        db.commit()
        
        # Create users in each org
        user1 = User(
            id=uuid.uuid4(),
            organization_id=org1.id,
            email="admin1@org1.com",
            hashed_password=hash_password("password"),
            full_name="Admin One",
            role=UserRole.ORG_ADMIN,
            is_active=True,
        )
        user2 = User(
            id=uuid.uuid4(),
            organization_id=org2.id,
            email="admin2@org2.com",
            hashed_password=hash_password("password"),
            full_name="Admin Two",
            role=UserRole.ORG_ADMIN,
            is_active=True,
        )
        db.add_all([user1, user2])
        db.commit()
        
        # Create assets in each org
        asset1 = Asset(
            id=uuid.uuid4(),
            organization_id=org1.id,
            vin="ORG1ASSET001",
            make_model="Org1 Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=30000,
            current_location="Org1 Warehouse",
            current_custody_type="Warehouse Depot",
        )
        asset2 = Asset(
            id=uuid.uuid4(),
            organization_id=org2.id,
            vin="ORG2ASSET001",
            make_model="Org2 Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=35000,
            current_location="Org2 Warehouse",
            current_custody_type="Warehouse Depot",
        )
        db.add_all([asset1, asset2])
        db.commit()
        db.refresh(asset1)
        db.refresh(asset2)
        
        # Test: User1 CANNOT access Org2's data
        assert not can_access_organization(user1, org2.id)
        
        # Test: User2 CANNOT access Org1's data
        assert not can_access_organization(user2, org1.id)
        
        # Test: User1 CAN access their own org
        assert can_access_organization(user1, org1.id)
        
        # Test: User1 CANNOT retrieve Org2's asset
        with pytest.raises(HTTPException) as exc_info:
            require_asset_access(db, user1, asset2.id)
        assert exc_info.value.status_code == 404
        
        # Test: User1 CAN retrieve their own asset
        retrieved = require_asset_access(db, user1, asset1.id)
        assert retrieved.id == asset1.id
        
        # Test: Asset query filtering
        from sqlalchemy import select
        query = select(Asset)
        filtered_query = filter_assets_by_access(query, user1, db)
        user1_assets = db.scalars(filtered_query).all()
        
        # User1 should only see assets from Org1
        assert len(user1_assets) >= 1
        assert all(a.organization_id == org1.id for a in user1_assets)
        assert not any(a.id == asset2.id for a in user1_assets)
        
        print("✅ Data isolation test passed")
        
    finally:
        db.rollback()
        db.close()


def test_system_admin_cross_org_access():
    """Test that system admin can access all organizations."""
    db = next(get_db())
    
    try:
        org1 = Organization(
            id=uuid.uuid4(),
            name="Org A",
            slug="org-a",
            org_type="internal",
            is_active=True,
        )
        org2 = Organization(
            id=uuid.uuid4(),
            name="Org B",
            slug="org-b",
            org_type="customer",
            is_active=True,
        )
        db.add_all([org1, org2])
        db.commit()
        
        # Create system admin in org1
        sys_admin = User(
            id=uuid.uuid4(),
            organization_id=org1.id,
            email="sysadmin@system.com",
            hashed_password=hash_password("password"),
            full_name="System Admin",
            role=UserRole.SYSTEM_ADMIN,
            is_active=True,
        )
        db.add(sys_admin)
        db.commit()
        
        # Create asset in org2
        asset = Asset(
            id=uuid.uuid4(),
            organization_id=org2.id,
            vin="SYSADMTEST001",
            make_model="Test Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=30000,
            current_location="Test Location",
            current_custody_type="Warehouse Depot",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
        # Test: System admin CAN access both orgs
        assert can_access_organization(sys_admin, org1.id)
        assert can_access_organization(sys_admin, org2.id)
        
        # Test: System admin CAN retrieve asset from any org
        retrieved = require_asset_access(db, sys_admin, asset.id)
        assert retrieved.id == asset.id
        
        print("✅ System admin cross-org access test passed")
        
    finally:
        db.rollback()
        db.close()


def test_customer_user_restricted_access():
    """Test that customer users only see authorized assets."""
    db = next(get_db())
    
    try:
        from app.models import Agency, AssetAuthorization
        
        org = Organization(
            id=uuid.uuid4(),
            name="Customer Org",
            slug="customer-org",
            org_type="customer",
            is_active=True,
        )
        db.add(org)
        db.commit()

        agency = Agency(id=uuid.uuid4(), organization_id=org.id, name="Customer Agency")
        db.add(agency)
        db.commit()

        customer = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            # Customers are scoped to one agency on top of the per-asset grant.
            agency_id=agency.id,
            email="customer@customer.com",
            hashed_password=hash_password("password"),
            full_name="Customer User",
            role=UserRole.CUSTOMER,
            is_active=True,
        )
        db.add(customer)
        db.commit()
        
        # Create two assets in the same org
        asset_authorized = Asset(
            id=uuid.uuid4(),
            organization_id=org.id,
            vin="CUSTAUTH001",
            make_model="Authorized Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=30000,
            current_location="Location A",
            current_custody_type="Warehouse Depot",
            agency_id=agency.id,
        )
        asset_not_authorized = Asset(
            id=uuid.uuid4(),
            organization_id=org.id,
            vin="CUSTNOAUTH001",
            make_model="Not Authorized Vehicle",
            asset_type=AssetType.FLEET_VEHICLE,
            initial_purchase_cost=30000,
            current_location="Location B",
            current_custody_type="Warehouse Depot",
            agency_id=agency.id,
        )
        db.add_all([asset_authorized, asset_not_authorized])
        db.commit()
        db.refresh(asset_authorized)
        db.refresh(asset_not_authorized)
        
        # Authorize only one asset
        auth = AssetAuthorization(
            id=uuid.uuid4(),
            user_id=customer.id,
            asset_id=asset_authorized.id,
            can_view=True,
            can_report_issue=False,
        )
        db.add(auth)
        db.commit()
        
        # Test: Customer CAN access authorized asset
        retrieved = require_asset_access(db, customer, asset_authorized.id)
        assert retrieved.id == asset_authorized.id
        
        # Test: Customer CANNOT access non-authorized asset
        with pytest.raises(HTTPException) as exc_info:
            require_asset_access(db, customer, asset_not_authorized.id)
        assert exc_info.value.status_code == 404
        
        # Test: Asset filtering returns only authorized assets
        from sqlalchemy import select
        query = select(Asset)
        filtered_query = filter_assets_by_access(query, customer, db)
        customer_assets = db.scalars(filtered_query).all()
        
        assert len(customer_assets) == 1
        assert customer_assets[0].id == asset_authorized.id
        
        print("✅ Customer restricted access test passed")
        
    finally:
        db.rollback()
        db.close()


def test_role_permissions():
    """Test role-based permission checks."""
    from app.rbac import user_has_permission
    
    db = next(get_db())
    
    try:
        org = Organization(
            id=uuid.uuid4(),
            name="Test Org",
            slug="test-org",
            org_type="internal",
            is_active=True,
        )
        db.add(org)
        db.commit()
        
        # Create users with different roles
        roles_and_permissions = [
            (UserRole.SYSTEM_ADMIN, ["manage_organizations", "view_all_organizations", "manage_users"]),
            (UserRole.ORG_ADMIN, ["manage_org_users", "manage_assets", "view_analytics"]),
            (UserRole.FLEET_MANAGER, ["manage_assets", "view_analytics", "export_data"]),
            (UserRole.TECHNICIAN, ["view_assets", "manage_maintenance"]),
            (UserRole.READ_ONLY, ["view_assets", "view_maintenance"]),
            (UserRole.CUSTOMER, ["view_authorized_assets"]),
        ]
        
        for role, expected_perms in roles_and_permissions:
            user = User(
                id=uuid.uuid4(),
                organization_id=org.id,
                email=f"{role.value}@test.com",
                hashed_password=hash_password("password"),
                full_name=f"{role.value} User",
                role=role,
                is_active=True,
            )
            db.add(user)
            
            # Test expected permissions
            for perm in expected_perms:
                assert user_has_permission(user, perm), f"{role.value} should have {perm}"
            
            # Test some permissions they shouldn't have
            if role != UserRole.SYSTEM_ADMIN:
                assert not user_has_permission(user, "manage_organizations")
            if role == UserRole.CUSTOMER:
                assert not user_has_permission(user, "manage_assets")
        
        print("✅ Role permissions test passed")
        
    finally:
        db.rollback()
        db.close()
