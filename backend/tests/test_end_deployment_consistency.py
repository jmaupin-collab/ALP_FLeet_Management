"""End-deployment dispositions stay consistent with active deployment records."""

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
from app.services import effective_operational_status


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
    org = Organization(name="End Dep Org", slug="end-dep-org", org_type="internal")
    db_session.add(org)
    db_session.flush()
    user = User(
        organization_id=org.id,
        email="end.dep@test.com",
        hashed_password=hash_password("password123"),
        full_name="End Dep Manager",
        role=UserRole.FLEET_MANAGER,
        is_active=True,
    )
    warehouse = Warehouse(
        organization_id=org.id,
        name="Return Yard",
        latitude=Decimal("33.4484000"),
        longitude=Decimal("-112.0740000"),
    )
    agency_a = Agency(
        organization_id=org.id,
        name="Springfield PD",
        latitude=Decimal("37.2081729"),
        longitude=Decimal("-93.2922715"),
    )
    agency_b = Agency(
        organization_id=org.id,
        name="Scottsdale PD",
        latitude=Decimal("33.4942000"),
        longitude=Decimal("-111.9261000"),
    )
    asset = Asset(
        organization_id=org.id,
        vin="ENDDEP0001",
        make_model="Consistency Trailer",
        initial_purchase_cost=Decimal("1000"),
        current_location="Springfield PD",
        asset_type=AssetType.ALPR_TRAILER,
        operational_status=AssetOperationalStatus.DEPLOYED,
        current_custody_type=CustodyType.CUSTOMER_AGENCY,
        agency_id=None,
    )
    db_session.add_all([user, warehouse, agency_a, agency_b, asset])
    db_session.flush()
    asset.agency_id = agency_a.id
    open_dep = Deployment(
        organization_id=org.id,
        asset_id=asset.id,
        location="Springfield PD",
        status=DeploymentStatus.DEPLOYED,
        custody_type=CustodyType.CUSTOMER_AGENCY,
        started_at=datetime.now(UTC) - timedelta(days=3),
    )
    db_session.add(open_dep)
    db_session.commit()
    return {
        "org": org,
        "asset": asset,
        "warehouse": warehouse,
        "agency_a": agency_a,
        "agency_b": agency_b,
        "open_dep": open_dep,
    }


def login(client):
    response = client.post("/auth/login", json={"email": "end.dep@test.com", "password": "password123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def open_rows(db_session, asset_id):
    return list(
        db_session.scalars(
            select(Deployment).where(Deployment.asset_id == asset_id).where(Deployment.ended_at.is_(None))
        )
    )


def all_rows(db_session, asset_id):
    return list(db_session.scalars(select(Deployment).where(Deployment.asset_id == asset_id)))


def assert_deployed_matches_active(db_session, asset):
    db_session.refresh(asset)
    opens = open_rows(db_session, asset.id)
    status = effective_operational_status(asset)
    if status == AssetOperationalStatus.DEPLOYED:
        assert len(opens) == 1
    if not opens:
        assert status != AssetOperationalStatus.DEPLOYED
    assert len(opens) <= 1


def test_return_to_warehouse_removes_active_deployment_and_sets_available(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    prior_count = len(all_rows(db_session, asset.id))
    headers = login(client)
    ended = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={
            "disposition": "return_to_warehouse",
            "destination_warehouse_id": str(warehouse.id),
        },
    )
    assert ended.status_code == 200, ended.text
    body = ended.json()
    assert body["operational_status"] == "available"
    assert body["current_custody_type"] == "Warehouse Depot"
    assert body["warehouse_id"] == str(warehouse.id)
    assert body["agency_id"] is None
    assert body["current_location"] == warehouse.name

    listed = client.get("/deployments", headers=headers).json()
    assert all(row["asset_id"] != str(asset.id) for row in listed)
    assert open_rows(db_session, asset.id) == []
    assert len(all_rows(db_session, asset.id)) >= prior_count
    assert_deployed_matches_active(db_session, asset)


def test_transfer_agency_a_to_b_closes_a_and_creates_one_active_for_b(client, setup, db_session):
    asset = setup["asset"]
    agency_a = setup["agency_a"]
    agency_b = setup["agency_b"]
    old_id = setup["open_dep"].id
    prior_count = len(all_rows(db_session, asset.id))
    headers = login(client)
    ended = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={
            "disposition": "transfer_to_agency",
            "next_agency_id": str(agency_b.id),
        },
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["operational_status"] == "deployed"
    assert ended.json()["agency_id"] == str(agency_b.id)
    assert ended.json()["current_location"] == agency_b.name

    db_session.refresh(setup["open_dep"])
    assert setup["open_dep"].ended_at is not None
    assert setup["open_dep"].end_reason == "Transfer"
    assert setup["open_dep"].id == old_id

    opens = open_rows(db_session, asset.id)
    assert len(opens) == 1
    assert opens[0].id != old_id
    assert opens[0].location == agency_b.name
    assert opens[0].custody_type == CustodyType.CUSTOMER_AGENCY

    listed = client.get("/deployments", headers=headers).json()
    visible = [row for row in listed if row["asset_id"] == str(asset.id)]
    assert len(visible) == 1
    assert visible[0]["location"] == agency_b.name
    assert visible[0]["ended_at"] is None

    assert len(all_rows(db_session, asset.id)) == prior_count + 1
    assert_deployed_matches_active(db_session, asset)

    mapped = client.get("/map/assets", headers=headers).json()
    pin = next(row for row in mapped if row["id"] == str(asset.id))
    assert pin["operational_status"] == "deployed"
    assert pin["agency_id"] == str(agency_b.id)
    assert pytest.approx(pin["latitude"], abs=1e-4) == 33.4942

    assets = client.get("/assets", headers=headers).json()
    asset_row = next(row for row in assets if row["id"] == str(asset.id))
    assert asset_row["operational_status"] == "deployed"
    assert asset_row["agency_id"] == str(agency_b.id)
    assert asset_row["current_location"] == agency_b.name

    kpis = client.get("/dashboard/kpis", headers=headers).json()
    assert kpis["deployed"] == 1
    assert visible[0]["location"] != agency_a.name

    history = [row for row in all_rows(db_session, asset.id) if row.id == old_id]
    assert len(history) == 1
    assert history[0].location == agency_a.name
    assert history[0].ended_at is not None


def test_return_to_warehouse_after_transfer_closes_b_and_removes_from_active_list(client, setup, db_session):
    asset = setup["asset"]
    agency_a = setup["agency_a"]
    agency_b = setup["agency_b"]
    warehouse = setup["warehouse"]
    old_a_id = setup["open_dep"].id
    headers = login(client)

    transferred = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={"disposition": "transfer_to_agency", "next_agency_id": str(agency_b.id)},
    )
    assert transferred.status_code == 200, transferred.text
    assert transferred.json()["operational_status"] == "deployed"
    b_open = open_rows(db_session, asset.id)
    assert len(b_open) == 1
    b_id = b_open[0].id

    returned = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={"disposition": "return_to_warehouse", "destination_warehouse_id": str(warehouse.id)},
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["operational_status"] == "available"
    assert returned.json()["agency_id"] is None

    listed = client.get("/deployments", headers=headers).json()
    assert all(row["asset_id"] != str(asset.id) for row in listed)
    assert open_rows(db_session, asset.id) == []

    history = {row.id: row for row in all_rows(db_session, asset.id)}
    assert old_a_id in history
    assert history[old_a_id].location == agency_a.name
    assert history[old_a_id].ended_at is not None
    assert b_id in history
    assert history[b_id].ended_at is not None
    assert history[b_id].location == agency_b.name
    assert_deployed_matches_active(db_session, asset)


def test_create_work_order_keeps_active_customer_deployment(client, setup, db_session):
    asset = setup["asset"]
    old_id = setup["open_dep"].id
    headers = login(client)
    created = client.post(
        "/work-orders",
        headers=headers,
        json={"asset_id": str(asset.id), "title": "Field camera repair"},
    )
    assert created.status_code == 201, created.text

    body = client.get(f"/assets/{asset.id}", headers=headers).json()
    assert body["operational_status"] == "deployed"
    assert body["current_custody_type"] == "Customer / LE Agency"
    assert body["agency_id"] == str(setup["agency_a"].id)
    assert body["open_work_orders"] >= 1

    opens = open_rows(db_session, asset.id)
    assert len(opens) == 1
    assert opens[0].id == old_id
    assert opens[0].ended_at is None
    assert opens[0].custody_type == CustodyType.CUSTOMER_AGENCY

    listed = client.get("/deployments", headers=headers).json()
    visible = [row for row in listed if row["asset_id"] == str(asset.id)]
    assert len(visible) == 1
    assert visible[0]["ended_at"] is None

    kpis = client.get("/dashboard/kpis", headers=headers).json()
    assert kpis["deployed"] == 1
    assert kpis["in_maintenance"] == 0
    assert_deployed_matches_active(db_session, asset)


def test_send_to_maintenance_closes_deployment_and_changes_status(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    prior_ids = {row.id for row in all_rows(db_session, asset.id)}
    headers = login(client)
    ended = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={
            "disposition": "maintenance",
            "maintenance_warehouse_id": str(warehouse.id),
            "create_work_order": True,
            "work_order_title": "Post-deploy inspection",
        },
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["operational_status"] == "maintenance"
    assert ended.json()["operational_status"] != "deployed"
    assert ended.json()["current_custody_type"] == "Warehouse Depot"
    assert ended.json()["agency_id"] is None

    listed = client.get("/deployments", headers=headers).json()
    assert all(row["asset_id"] != str(asset.id) for row in listed)
    assert open_rows(db_session, asset.id) == []
    remaining_ids = {row.id for row in all_rows(db_session, asset.id)}
    assert prior_ids <= remaining_ids
    kpis = client.get("/dashboard/kpis", headers=headers).json()
    assert kpis["deployed"] == 0
    assert kpis["in_maintenance"] == 1
    assert_deployed_matches_active(db_session, asset)


def test_no_deployed_status_with_zero_or_multiple_active_deployments(client, setup, db_session):
    asset = setup["asset"]
    agency_b = setup["agency_b"]
    warehouse = setup["warehouse"]
    headers = login(client)

    client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={"disposition": "transfer_to_agency", "next_agency_id": str(agency_b.id)},
    )
    assert_deployed_matches_active(db_session, asset)

    # Close the transfer so we can return from a single open row
    open_row = open_rows(db_session, asset.id)[0]
    client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={"disposition": "return_to_warehouse", "destination_warehouse_id": str(warehouse.id)},
    )
    assert_deployed_matches_active(db_session, asset)
    db_session.refresh(asset)
    assert effective_operational_status(asset) != AssetOperationalStatus.DEPLOYED
    assert open_rows(db_session, asset.id) == []

    # Stale Deployed flag with no open row must not display as deployed
    asset.operational_status = AssetOperationalStatus.DEPLOYED
    db_session.commit()
    db_session.refresh(asset)
    assert effective_operational_status(asset) != AssetOperationalStatus.DEPLOYED
    kpis = client.get("/dashboard/kpis", headers=headers).json()
    body = client.get(f"/assets/{asset.id}", headers=headers).json()
    assert body["operational_status"] != "deployed"
    assert kpis["deployed"] == 0


def test_custody_agency_change_closes_history_and_opens_b(client, setup, db_session):
    asset = setup["asset"]
    agency_b = setup["agency_b"]
    old_id = setup["open_dep"].id
    headers = login(client)
    moved = client.post(
        f"/assets/{asset.id}/custody",
        headers=headers,
        json={
            "custody_type": "Customer / LE Agency",
            "location": agency_b.name,
            "agency_id": str(agency_b.id),
        },
    )
    assert moved.status_code == 200, moved.text
    db_session.refresh(setup["open_dep"])
    assert setup["open_dep"].ended_at is not None
    assert setup["open_dep"].id == old_id
    opens = open_rows(db_session, asset.id)
    assert len(opens) == 1
    assert opens[0].id != old_id
    assert opens[0].location == agency_b.name
    listed = client.get("/deployments", headers=headers).json()
    visible = [row for row in listed if row["asset_id"] == str(asset.id)]
    assert len(visible) == 1
    assert visible[0]["location"] == agency_b.name


def test_retire_from_deployment_stays_retired_at_warehouse_off_map(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    headers = login(client)
    ended = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={
            "disposition": "retired",
            "destination_warehouse_id": str(warehouse.id),
            "out_of_service_reason": "End of life",
        },
    )
    assert ended.status_code == 200, ended.text
    body = ended.json()
    assert body["operational_status"] == "retired"
    assert body["warehouse_id"] == str(warehouse.id)
    assert body["agency_id"] is None
    assert "Retired" in body["current_location"]
    assert open_rows(db_session, asset.id) == []

    listed = client.get("/deployments", headers=headers).json()
    assert all(row["asset_id"] != str(asset.id) for row in listed)
    mapped = client.get("/map/assets", headers=headers).json()
    assert all(row["id"] != str(asset.id) for row in mapped)
    kpis = client.get("/dashboard/kpis", headers=headers).json()
    assert kpis["deployed"] == 0
    assert kpis["available"] == 0
    assert_deployed_matches_active(db_session, asset)


def test_legacy_end_deployment_closes_open_row(client, setup, db_session):
    asset = setup["asset"]
    warehouse = setup["warehouse"]
    headers = login(client)
    ended = client.post(
        f"/assets/{asset.id}/deployments/end",
        headers=headers,
        json={"warehouse_id": str(warehouse.id), "location": warehouse.name},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["operational_status"] == "available"
    assert open_rows(db_session, asset.id) == []
    listed = client.get("/deployments", headers=headers).json()
    assert all(row["asset_id"] != str(asset.id) for row in listed)
    assert_deployed_matches_active(db_session, asset)
