"""Asset-page operational status corrections stay in sync across tabs."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hash_password
from app.database import get_db
from app.main import app
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
def setup(db_session):
    org = Organization(name="Status Org", slug="status-org", org_type="internal")
    db_session.add(org)
    db_session.flush()
    user = User(
        organization_id=org.id,
        email="status.edit@test.com",
        hashed_password=hash_password("password123"),
        full_name="Status Editor",
        role=UserRole.FLEET_MANAGER,
        is_active=True,
    )
    warehouse = Warehouse(
        organization_id=org.id,
        name="DV",
        latitude=Decimal("33.4484000"),
        longitude=Decimal("-112.0740000"),
    )
    agency = Agency(
        organization_id=org.id,
        name="Springfield PD",
        latitude=Decimal("37.2081729"),
        longitude=Decimal("-93.2922715"),
    )
    asset = Asset(
        organization_id=org.id,
        vin="STATUS0001",
        make_model="Grape Trailer",
        initial_purchase_cost=Decimal("1000"),
        current_location="DV (Maintenance)",
        asset_type=AssetType.ALPR_TRAILER,
        operational_status=AssetOperationalStatus.MAINTENANCE,
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
    )
    db_session.add_all([user, warehouse, agency, asset])
    db_session.flush()
    asset.warehouse_id = warehouse.id
    db_session.commit()
    return {"org": org, "asset": asset, "warehouse": warehouse, "agency": agency}


def login(client):
    response = client.post("/auth/login", json={"email": "status.edit@test.com", "password": "password123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def open_rows(db_session, asset_id):
    return list(
        db_session.scalars(
            select(Deployment).where(Deployment.asset_id == asset_id).where(Deployment.ended_at.is_(None))
        )
    )


def check_tabs(client, headers, asset, expected_status, warehouse):
    asset_id = str(asset.id)
    body = client.get(f"/assets/{asset.id}", headers=headers).json()
    assert body["operational_status"] == expected_status
    assert body["current_custody_type"] == "Warehouse Depot"
    assert body["agency_id"] is None
    if warehouse:
        assert body["warehouse_id"] == str(warehouse.id)

    listed = client.get("/assets", headers=headers).json()
    row = next(item for item in listed if item["id"] == asset_id)
    assert row["operational_status"] == expected_status

    deployments = client.get("/deployments", headers=headers).json()
    assert all(item["asset_id"] != asset_id for item in deployments)

    mapped = client.get("/map/assets", headers=headers).json()
    if expected_status == "retired":
        assert all(item["id"] != asset_id for item in mapped)
    else:
        pin = next(item for item in mapped if item["id"] == asset_id)
        assert pin["operational_status"] == expected_status
        if warehouse:
            assert pytest.approx(pin["latitude"], abs=1e-4) == float(warehouse.latitude)
            assert pytest.approx(pin["longitude"], abs=1e-4) == float(warehouse.longitude)

    kpis = client.get("/dashboard/kpis", headers=headers).json()
    assert kpis["deployed"] == 0
    if expected_status == "available":
        assert kpis["available"] == 1
        assert kpis["in_maintenance"] == 0
    elif expected_status == "maintenance":
        assert kpis["available"] == 0
        assert kpis["in_maintenance"] == 1
    else:
        assert kpis["available"] == 0
        assert kpis["in_maintenance"] == 0
    return body


def test_mark_available_from_maintenance_at_warehouse(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    headers = login(client)

    before = client.get("/dashboard/kpis", headers=headers).json()
    assert before["in_maintenance"] == 1
    assert before["available"] == 0

    updated = client.post(
        f"/assets/{asset.id}/operational-status",
        headers=headers,
        json={"operational_status": "available", "warehouse_id": str(warehouse.id)},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["operational_status"] == "available"
    assert updated.json()["current_location"] == "DV"
    assert open_rows(db_session, asset.id) == []
    check_tabs(client, headers, asset, "available", warehouse)


def test_mark_retired_from_maintenance_keeps_warehouse_off_map(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    headers = login(client)
    updated = client.post(
        f"/assets/{asset.id}/operational-status",
        headers=headers,
        json={"operational_status": "retired", "warehouse_id": str(warehouse.id)},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["operational_status"] == "retired"
    assert updated.json()["warehouse_id"] == str(warehouse.id)
    assert "Retired" in updated.json()["current_location"]
    assert open_rows(db_session, asset.id) == []
    check_tabs(client, headers, asset, "retired", warehouse)


def test_legacy_out_of_service_write_stores_retired(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    headers = login(client)
    updated = client.post(
        f"/assets/{asset.id}/operational-status",
        headers=headers,
        json={"operational_status": "out_of_service", "warehouse_id": str(warehouse.id)},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["operational_status"] == "retired"
    check_tabs(client, headers, asset, "retired", warehouse)


def test_legacy_stored_oos_with_open_row_is_not_active(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    agency = setup["agency"]
    asset.operational_status = AssetOperationalStatus.OUT_OF_SERVICE
    asset.current_custody_type = CustodyType.WAREHOUSE_DEPOT
    asset.warehouse_id = warehouse.id
    asset.agency_id = None
    asset.current_location = f"{warehouse.name} (Retired)"
    db_session.add(
        Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location=agency.name,
            status=DeploymentStatus.DEPLOYED,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            started_at=datetime.now(UTC) - timedelta(days=2),
        )
    )
    db_session.commit()

    headers = login(client)
    body = client.get(f"/assets/{asset.id}", headers=headers).json()
    assert body["operational_status"] == "retired"
    assert body["warehouse_id"] == str(warehouse.id)
    listed = client.get("/deployments", headers=headers).json()
    assert all(item["asset_id"] != str(asset.id) for item in listed)
    mapped = client.get("/map/assets", headers=headers).json()
    assert all(item["id"] != str(asset.id) for item in mapped)
    kpis = client.get("/dashboard/kpis", headers=headers).json()
    assert kpis["deployed"] == 0
    assert kpis["available"] == 0


def test_status_change_closes_open_deployment(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    agency = setup["agency"]
    asset.operational_status = AssetOperationalStatus.DEPLOYED
    asset.current_custody_type = CustodyType.CUSTOMER_AGENCY
    asset.agency_id = agency.id
    asset.warehouse_id = None
    asset.current_location = agency.name
    db_session.add(
        Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location=agency.name,
            status=DeploymentStatus.DEPLOYED,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            started_at=datetime.now(UTC) - timedelta(days=2),
        )
    )
    db_session.commit()

    headers = login(client)
    listed = client.get("/deployments", headers=headers).json()
    assert any(row["asset_id"] == str(asset.id) for row in listed)

    updated = client.post(
        f"/assets/{asset.id}/operational-status",
        headers=headers,
        json={"operational_status": "available", "warehouse_id": str(warehouse.id)},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["operational_status"] == "available"
    assert open_rows(db_session, asset.id) == []
    check_tabs(client, headers, asset, "available", warehouse)


def test_rejects_deployed_and_in_transit_from_status_editor(client, setup):
    headers = login(client)
    for status_value in ("deployed", "in_transit"):
        response = client.post(
            f"/assets/{setup['asset'].id}/operational-status",
            headers=headers,
            json={"operational_status": status_value},
        )
        assert response.status_code == 422
        assert "Start Deployment" in response.json()["detail"]
