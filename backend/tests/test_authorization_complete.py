"""
Comprehensive cross-organization authorization tests.

Tests that Organization A cannot access Organization B's resources through:
- Assets (PATCH, archive, restore, delete)
- Deployments (end, end-workflow, patch)
- Inspections (update item, submit, cancel)
- Work Orders (create, update, close, costs, delete)
- PM Schedules (create, update, complete, delete)
- Meter Readings (list, create, latest)
- Directories (warehouses, agencies, vendors)
- Users (list, update, delete)
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import (
    Agency,
    Asset,
    AssetType,
    Deployment,
    DeploymentStatus,
    Inspection,
    InspectionItem,
    InspectionResult,
    InspectionStatus,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
    MeterReading,
    Organization,
    User,
    UserRole,
    Vendor,
    Warehouse,
    WorkOrderStatus,
)


@pytest.fixture
def client(db_session):
    def _override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def two_orgs_with_data(db_session):
    """Create two organizations with users, assets, and related data."""
    # Organization A
    org_a = Organization(name="Organization A", slug="org-a", org_type="internal")
    db_session.add(org_a)
    db_session.flush()
    
    user_a = User(
        organization_id=org_a.id,
        email="admin_a@test.com",
        hashed_password=hash_password("password123"),
        full_name="Admin A",
        role=UserRole.ORG_ADMIN,
        is_active=True,
    )
    operator_a = User(
        organization_id=org_a.id,
        email="operator_a@test.com",
        hashed_password=hash_password("password123"),
        full_name="Operator A",
        role=UserRole.FLEET_MANAGER,
        is_active=True,
    )
    db_session.add_all([user_a, operator_a])
    db_session.flush()
    
    now = datetime.now(UTC)
    asset_a = Asset(
        organization_id=org_a.id,
        vin="VINA00000001",
        make_model="Test Model A",
        initial_purchase_cost=Decimal("10000"),
        current_location="Warehouse A",
        asset_type=AssetType.ALPR_TRAILER,
        created_by_id=user_a.id,
    )
    db_session.add(asset_a)
    db_session.flush()
    
    warehouse_a = Warehouse(
        organization_id=org_a.id,
        name="Warehouse A",
        created_by_id=user_a.id,
    )
    agency_a = Agency(
        organization_id=org_a.id,
        name="Agency A",
    )
    vendor_a = Vendor(
        organization_id=org_a.id,
        name="Vendor A",
        created_by_id=user_a.id,
    )
    db_session.add_all([warehouse_a, agency_a, vendor_a])
    db_session.flush()
    
    deployment_a = Deployment(
        organization_id=org_a.id,
        asset_id=asset_a.id,
        location="Agency A",
        status=DeploymentStatus.ACTIVE,
        started_at=now,
        created_by_id=user_a.id,
    )
    inspection_a = Inspection(
        organization_id=org_a.id,
        asset_id=asset_a.id,
        status=InspectionStatus.IN_PROGRESS,
        started_at=now,
        created_by_id=user_a.id,
    )
    work_order_a = MaintenanceWorkOrder(
        organization_id=org_a.id,
        asset_id=asset_a.id,
        title="Test Issue",
        description="Test Issue",
        failed_component="Test Component",
        status=WorkOrderStatus.OPEN,
        opened_at=now,
        created_by_id=user_a.id,
    )
    pm_schedule_a = MaintenanceSchedule(
        organization_id=org_a.id,
        asset_id=asset_a.id,
        name="PM Schedule A",
        interval_miles=Decimal("5000"),
        created_by_id=user_a.id,
    )
    meter_reading_a = MeterReading(
        asset_id=asset_a.id,
        odometer_miles=Decimal("1000"),
        reading_date=now,
        source="manual",
        created_by_id=user_a.id,
    )
    db_session.add_all([deployment_a, inspection_a, work_order_a, pm_schedule_a, meter_reading_a])
    db_session.flush()
    
    # Organization B
    org_b = Organization(name="Organization B", slug="org-b", org_type="internal")
    db_session.add(org_b)
    db_session.flush()
    
    user_b = User(
        organization_id=org_b.id,
        email="admin_b@test.com",
        hashed_password=hash_password("password123"),
        full_name="Admin B",
        role=UserRole.ORG_ADMIN,
        is_active=True,
    )
    operator_b = User(
        organization_id=org_b.id,
        email="operator_b@test.com",
        hashed_password=hash_password("password123"),
        full_name="Operator B",
        role=UserRole.FLEET_MANAGER,
        is_active=True,
    )
    db_session.add_all([user_b, operator_b])
    db_session.flush()
    
    asset_b = Asset(
        organization_id=org_b.id,
        vin="VINB00000001",
        make_model="Test Model B",
        initial_purchase_cost=Decimal("20000"),
        current_location="Warehouse B",
        asset_type=AssetType.SEMI_TRUCK,
        created_by_id=user_b.id,
    )
    db_session.add(asset_b)
    db_session.flush()
    
    warehouse_b = Warehouse(
        organization_id=org_b.id,
        name="Warehouse B",
        created_by_id=user_b.id,
    )
    agency_b = Agency(
        organization_id=org_b.id,
        name="Agency B",
    )
    vendor_b = Vendor(
        organization_id=org_b.id,
        name="Vendor B",
        created_by_id=user_b.id,
    )
    db_session.add_all([warehouse_b, agency_b, vendor_b])
    db_session.flush()
    
    db_session.commit()
    
    return {
        "org_a": org_a,
        "user_a": user_a,
        "operator_a": operator_a,
        "asset_a": asset_a,
        "warehouse_a": warehouse_a,
        "agency_a": agency_a,
        "vendor_a": vendor_a,
        "deployment_a": deployment_a,
        "inspection_a": inspection_a,
        "work_order_a": work_order_a,
        "pm_schedule_a": pm_schedule_a,
        "meter_reading_a": meter_reading_a,
        "org_b": org_b,
        "user_b": user_b,
        "operator_b": operator_b,
        "asset_b": asset_b,
        "warehouse_b": warehouse_b,
        "agency_b": agency_b,
        "vendor_b": vendor_b,
    }


def get_token(client, email, password):
    """Helper to get JWT token."""
    response = client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


class TestAssetAuthorization:
    """Test asset operations are organization-scoped."""
    
    def test_cannot_patch_other_org_asset(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.patch(
            f"/assets/{asset_a_id}",
            json={"make": "Hacked"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_archive_other_org_asset(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.post(
            f"/assets/{asset_a_id}/archive",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_restore_other_org_asset(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.post(
            f"/assets/{asset_a_id}/restore",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_delete_other_org_asset(self, client, two_orgs_with_data):
        token_b = get_token(client, "admin_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.delete(
            f"/assets/{asset_a_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestDeploymentAuthorization:
    """Test deployment operations are organization-scoped."""
    
    def test_cannot_end_other_org_deployment(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.post(
            f"/assets/{asset_a_id}/deployments/end",
            json={},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_end_workflow_other_org_deployment(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.post(
            f"/assets/{asset_a_id}/deployments/end-workflow",
            json={"disposition": "return_to_warehouse"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_patch_other_org_deployment(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        deployment_a_id = two_orgs_with_data["deployment_a"].id
        
        response = client.patch(
            f"/deployments/{deployment_a_id}",
            json={"notes": "Hacked"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestInspectionAuthorization:
    """Test inspection operations are organization-scoped."""
    
    def test_cannot_update_other_org_inspection_item(self, client, two_orgs_with_data, db_session):
        # Create an inspection item for org A
        inspection_a = two_orgs_with_data["inspection_a"]
        item = InspectionItem(
            inspection_id=inspection_a.id,
            component="Test Component",
            result=InspectionResult.PASS,
        )
        db_session.add(item)
        db_session.commit()
        
        token_b = get_token(client, "operator_b@test.com", "password123")
        
        response = client.patch(
            f"/inspections/{inspection_a.id}/items/{item.id}",
            json={"result": "fail", "notes": "Hacked"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_submit_other_org_inspection(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        inspection_a_id = two_orgs_with_data["inspection_a"].id
        
        response = client.post(
            f"/inspections/{inspection_a_id}/submit",
            json={"notes": "Completed"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_cancel_other_org_inspection(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        inspection_a_id = two_orgs_with_data["inspection_a"].id
        
        response = client.post(
            f"/inspections/{inspection_a_id}/cancel",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestWorkOrderAuthorization:
    """Test work order operations are organization-scoped."""
    
    def test_cannot_create_wo_for_other_org_asset(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.post(
            "/work-orders",
            json={
                "asset_id": str(asset_a_id),
                "title": "Won't start",
                "failed_component": "Engine",
            },
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_update_other_org_work_order(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        wo_a_id = two_orgs_with_data["work_order_a"].id
        
        response = client.patch(
            f"/work-orders/{wo_a_id}",
            json={"status": "investigating"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_close_other_org_work_order(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        wo_a_id = two_orgs_with_data["work_order_a"].id
        
        response = client.post(
            f"/work-orders/{wo_a_id}/close",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_add_cost_to_other_org_work_order(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        wo_a_id = two_orgs_with_data["work_order_a"].id
        
        response = client.post(
            f"/work-orders/{wo_a_id}/costs",
            json={"parts_cost": 100.0, "labor_cost": 50.0},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_delete_other_org_work_order(self, client, two_orgs_with_data):
        token_b = get_token(client, "admin_b@test.com", "password123")
        wo_a_id = two_orgs_with_data["work_order_a"].id
        
        response = client.delete(
            f"/work-orders/{wo_a_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestPMAuthorization:
    """Test PM schedule operations are organization-scoped."""
    
    def test_cannot_create_pm_for_other_org_asset(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.post(
            "/pm/schedules",
            json={
                "asset_id": str(asset_a_id),
                "name": "Oil Change",
                "interval_miles": 5000,
            },
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_update_other_org_pm_schedule(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        pm_a_id = two_orgs_with_data["pm_schedule_a"].id
        
        response = client.patch(
            f"/pm/schedules/{pm_a_id}",
            json={"interval_miles": 10000},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_complete_other_org_pm_schedule(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        pm_a_id = two_orgs_with_data["pm_schedule_a"].id
        
        response = client.post(
            f"/pm/schedules/{pm_a_id}/complete",
            json={"odometer_miles": 6000},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_delete_other_org_pm_schedule(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        pm_a_id = two_orgs_with_data["pm_schedule_a"].id
        
        response = client.delete(
            f"/pm/schedules/{pm_a_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestMeterReadingAuthorization:
    """Test meter reading operations are organization-scoped."""
    
    def test_cannot_list_other_org_meter_readings(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.get(
            f"/assets/{asset_a_id}/meters",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_create_meter_reading_for_other_org_asset(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.post(
            f"/assets/{asset_a_id}/meters",
            json={"odometer_miles": 2000},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_get_latest_meter_reading_for_other_org_asset(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        asset_a_id = two_orgs_with_data["asset_a"].id
        
        response = client.get(
            f"/assets/{asset_a_id}/meters/latest",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestDirectoryAuthorization:
    """Test directory operations are organization-scoped."""
    
    def test_cannot_see_other_org_warehouses(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        
        response = client.get(
            "/warehouses",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_200_OK
        warehouses = response.json()
        warehouse_names = [w["name"] for w in warehouses]
        assert "Warehouse A" not in warehouse_names
        assert "Warehouse B" in warehouse_names
    
    def test_cannot_patch_other_org_warehouse(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        warehouse_a_id = two_orgs_with_data["warehouse_a"].id
        
        response = client.patch(
            f"/warehouses/{warehouse_a_id}",
            json={"name": "Hacked Warehouse"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_delete_other_org_warehouse(self, client, two_orgs_with_data):
        token_b = get_token(client, "admin_b@test.com", "password123")
        warehouse_a_id = two_orgs_with_data["warehouse_a"].id
        
        response = client.delete(
            f"/warehouses/{warehouse_a_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_see_other_org_agencies(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        
        response = client.get(
            "/agencies",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_200_OK
        agencies = response.json()
        agency_names = [a["name"] for a in agencies]
        assert "Agency A" not in agency_names
        assert "Agency B" in agency_names
    
    def test_cannot_see_other_org_vendors(self, client, two_orgs_with_data):
        token_b = get_token(client, "operator_b@test.com", "password123")
        
        response = client.get(
            "/vendors",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_200_OK
        vendors = response.json()
        vendor_names = [v["name"] for v in vendors]
        assert "Vendor A" not in vendor_names
        assert "Vendor B" in vendor_names


class TestUserManagementAuthorization:
    """Test user management operations are organization-scoped."""
    
    def test_cannot_see_other_org_users(self, client, two_orgs_with_data):
        token_b = get_token(client, "admin_b@test.com", "password123")
        
        response = client.get(
            "/users",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_200_OK
        users = response.json()
        user_emails = [u["email"] for u in users]
        assert "admin_a@test.com" not in user_emails
        assert "admin_b@test.com" in user_emails
    
    def test_cannot_update_other_org_user(self, client, two_orgs_with_data):
        token_b = get_token(client, "admin_b@test.com", "password123")
        user_a_id = two_orgs_with_data["operator_a"].id
        
        response = client.patch(
            f"/users/{user_a_id}",
            json={"full_name": "Hacked"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        # 404 rather than 403 so a caller cannot enumerate user IDs in other tenants.
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_cannot_delete_other_org_user(self, client, two_orgs_with_data):
        token_b = get_token(client, "admin_b@test.com", "password123")
        user_a_id = two_orgs_with_data["operator_a"].id
        
        response = client.delete(
            f"/users/{user_a_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
