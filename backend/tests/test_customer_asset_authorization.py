"""Customer users may only see explicitly authorized assets and related records."""

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
    AssetAuthorization,
    AssetType,
    Deployment,
    DeploymentStatus,
    Inspection,
    InspectionStatus,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
    Organization,
    User,
    UserRole,
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
def customer_org(db_session):
    now = datetime.now(UTC)
    org = Organization(name="Customer Test Org", slug="customer-auth-org", org_type="internal")
    db_session.add(org)
    db_session.flush()

    admin = User(
        organization_id=org.id,
        email="custorg.admin@test.com",
        hashed_password=hash_password("password123"),
        full_name="Org Admin",
        role=UserRole.ORG_ADMIN,
        is_active=True,
    )
    customer = User(
        organization_id=org.id,
        email="limited.customer@test.com",
        hashed_password=hash_password("password123"),
        full_name="Limited Customer",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db_session.add_all([admin, customer])
    db_session.flush()

    warehouse = Warehouse(
        organization_id=org.id,
        name="Cust Warehouse",
        latitude=Decimal("33.4484"),
        longitude=Decimal("-112.0740"),
    )
    agency = Agency(
        organization_id=org.id,
        name="Cust Agency",
        latitude=Decimal("33.4942"),
        longitude=Decimal("-111.9261"),
    )
    db_session.add_all([warehouse, agency])
    db_session.flush()

    asset_a = Asset(
        organization_id=org.id,
        vin="CUSTA0000001",
        make_model="Authorized Trailer",
        initial_purchase_cost=Decimal("10000"),
        current_location="Cust Agency",
        asset_type=AssetType.ALPR_TRAILER,
        warehouse_id=warehouse.id,
        agency_id=agency.id,
    )
    asset_b = Asset(
        organization_id=org.id,
        vin="CUSTB0000001",
        make_model="Unauthorized Trailer",
        initial_purchase_cost=Decimal("99999"),
        current_location="Cust Agency",
        asset_type=AssetType.ALPR_TRAILER,
        warehouse_id=warehouse.id,
        agency_id=agency.id,
    )
    db_session.add_all([asset_a, asset_b])
    db_session.flush()

    db_session.add(
        AssetAuthorization(
            user_id=customer.id,
            asset_id=asset_a.id,
            can_view=True,
            granted_by_id=admin.id,
        )
    )

    dep_a = Deployment(
        organization_id=org.id,
        asset_id=asset_a.id,
        location="Site A",
        status=DeploymentStatus.ACTIVE,
        started_at=now,
    )
    dep_b = Deployment(
        organization_id=org.id,
        asset_id=asset_b.id,
        location="Site B",
        status=DeploymentStatus.ACTIVE,
        started_at=now,
    )
    insp_a = Inspection(
        organization_id=org.id,
        asset_id=asset_a.id,
        status=InspectionStatus.IN_PROGRESS,
        started_at=now,
        source="internal",
    )
    insp_b = Inspection(
        organization_id=org.id,
        asset_id=asset_b.id,
        status=InspectionStatus.IN_PROGRESS,
        started_at=now,
        source="internal",
    )
    wo_a = MaintenanceWorkOrder(
        organization_id=org.id,
        asset_id=asset_a.id,
        title="Authorized WO",
        status=WorkOrderStatus.OPEN,
        opened_at=now,
    )
    wo_b = MaintenanceWorkOrder(
        organization_id=org.id,
        asset_id=asset_b.id,
        title="Unauthorized WO",
        status=WorkOrderStatus.OPEN,
        opened_at=now,
    )
    pm_a = MaintenanceSchedule(
        organization_id=org.id,
        asset_id=asset_a.id,
        name="Authorized PM",
        interval_miles=Decimal("5000"),
    )
    pm_b = MaintenanceSchedule(
        organization_id=org.id,
        asset_id=asset_b.id,
        name="Unauthorized PM",
        interval_miles=Decimal("5000"),
    )
    db_session.add_all([dep_a, dep_b, insp_a, insp_b, wo_a, wo_b, pm_a, pm_b])
    db_session.commit()

    return {
        "org": org,
        "customer": customer,
        "asset_a": asset_a,
        "asset_b": asset_b,
        "dep_a": dep_a,
        "dep_b": dep_b,
        "insp_a": insp_a,
        "insp_b": insp_b,
        "wo_a": wo_a,
        "wo_b": wo_b,
        "pm_a": pm_a,
        "pm_b": pm_b,
    }


def customer_headers(client):
    response = client.post(
        "/auth/login",
        json={"email": "limited.customer@test.com", "password": "password123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class TestCustomerAssetIsolation:
    def test_customer_can_get_authorized_asset(self, client, customer_org):
        headers = customer_headers(client)
        asset_a_id = customer_org["asset_a"].id
        response = client.get(f"/assets/{asset_a_id}", headers=headers)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["id"] == str(asset_a_id)

    def test_customer_gets_404_for_unauthorized_asset(self, client, customer_org):
        headers = customer_headers(client)
        response = client.get(f"/assets/{customer_org['asset_b'].id}", headers=headers)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_deployment_list_excludes_unauthorized_asset(self, client, customer_org):
        headers = customer_headers(client)
        rows = client.get("/deployments", headers=headers).json()
        asset_ids = {row["asset_id"] for row in rows}
        assert str(customer_org["asset_a"].id) in asset_ids
        assert str(customer_org["asset_b"].id) not in asset_ids

    def test_work_order_list_excludes_unauthorized_asset(self, client, customer_org):
        headers = customer_headers(client)
        rows = client.get("/work-orders", headers=headers).json()
        ids = {row["id"] for row in rows}
        assert str(customer_org["wo_a"].id) in ids
        assert str(customer_org["wo_b"].id) not in ids

    def test_work_order_detail_unauthorized_is_404(self, client, customer_org):
        headers = customer_headers(client)
        response = client.get(f"/work-orders/{customer_org['wo_b'].id}", headers=headers)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_pm_list_excludes_unauthorized_asset(self, client, customer_org):
        headers = customer_headers(client)
        rows = client.get("/pm/schedules", headers=headers).json()
        ids = {row["id"] for row in rows}
        assert str(customer_org["pm_a"].id) in ids
        assert str(customer_org["pm_b"].id) not in ids

    def test_pm_detail_unauthorized_is_404(self, client, customer_org):
        headers = customer_headers(client)
        response = client.get(f"/pm/schedules/{customer_org['pm_b'].id}", headers=headers)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_inspection_list_excludes_unauthorized_asset(self, client, customer_org):
        headers = customer_headers(client)
        rows = client.get("/inspections", headers=headers).json()
        ids = {row["id"] for row in rows}
        assert str(customer_org["insp_a"].id) in ids
        assert str(customer_org["insp_b"].id) not in ids

    def test_inspection_detail_unauthorized_is_404(self, client, customer_org):
        headers = customer_headers(client)
        response = client.get(f"/inspections/{customer_org['insp_b'].id}", headers=headers)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_map_excludes_unauthorized_asset(self, client, customer_org):
        headers = customer_headers(client)
        rows = client.get("/map/assets", headers=headers).json()
        ids = {row["id"] for row in rows}
        assert str(customer_org["asset_a"].id) in ids
        assert str(customer_org["asset_b"].id) not in ids

    def test_dashboard_kpis_exclude_unauthorized_asset(self, client, customer_org):
        headers = customer_headers(client)
        kpis = client.get("/dashboard/kpis", headers=headers).json()
        assert kpis["fleet_size"] == 1
        assert float(kpis["total_purchase_cost"]) == 10000

    def test_customer_cannot_access_org_analytics(self, client, customer_org):
        headers = customer_headers(client)
        response = client.get("/analytics", headers=headers)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_customer_cannot_export_analytics(self, client, customer_org):
        headers = customer_headers(client)
        response = client.get("/analytics/export", headers=headers)
        assert response.status_code == status.HTTP_403_FORBIDDEN
