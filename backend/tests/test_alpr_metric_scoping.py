"""Readiness metrics count ALPR Trailers only.

Deployment lifecycle (available / deployed / in transit / maintenance / out of
service / retired) is an ALPR Trailer concept. Semi Trucks and Fleet Vehicles
are real assets that must stay in the database and stay visible, but they are
tracked as plain inventory and must never move an ALPR readiness number.
"""

from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import (
    Asset,
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    Organization,
    User,
    UserRole,
)
from app.services import dashboard_kpis


@pytest.fixture
def client(db_session):
    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def org(db_session):
    organization = Organization(name="Scope Org", slug="scope-org", org_type="internal")
    db_session.add(organization)
    db_session.flush()

    admin = User(
        organization_id=organization.id,
        email="admin@scope.com",
        hashed_password=hash_password("password123"),
        full_name="System Admin",
        role=UserRole.SYSTEM_ADMIN,
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    return organization


def add_asset(db, org, vin, asset_type, status_value, cost=10000):
    asset = Asset(
        organization_id=org.id,
        vin=vin,
        make_model=f"Model {vin}",
        asset_type=asset_type,
        initial_purchase_cost=Decimal(cost),
        current_location="Depot",
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
        operational_status=status_value,
    )
    db.add(asset)
    db.commit()
    return asset


def admin_headers(client):
    response = client.post(
        "/auth/login", json={"email": "admin@scope.com", "password": "password123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_alpr_trailers_drive_the_readiness_counts(db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPEALPR0002", AssetType.ALPR_TRAILER, AssetOperationalStatus.MAINTENANCE)

    kpis = dashboard_kpis(db_session, org.id)

    assert kpis.asset_type_scope == AssetType.ALPR_TRAILER.value
    assert kpis.fleet_size == 2
    assert kpis.available == 1
    assert kpis.in_maintenance == 1


def test_a_semi_truck_does_not_move_any_alpr_count(db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    before = dashboard_kpis(db_session, org.id)

    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)
    after = dashboard_kpis(db_session, org.id)

    assert after.fleet_size == before.fleet_size == 1
    assert after.available == before.available == 1
    assert Decimal(after.total_purchase_cost) == Decimal(before.total_purchase_cost)


def test_a_fleet_vehicle_does_not_move_any_alpr_count(db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    before = dashboard_kpis(db_session, org.id)

    add_asset(db_session, org, "SCOPEVEHIC001", AssetType.FLEET_VEHICLE, AssetOperationalStatus.MAINTENANCE, 40000)
    after = dashboard_kpis(db_session, org.id)

    assert after.in_maintenance == before.in_maintenance == 0
    assert after.fleet_size == before.fleet_size == 1
    assert all(row.asset_type == AssetType.ALPR_TRAILER for row in after.by_asset_type)


def test_non_alpr_assets_roll_up_in_their_own_section(db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)
    add_asset(db_session, org, "SCOPEVEHIC001", AssetType.FLEET_VEHICLE, AssetOperationalStatus.MAINTENANCE, 40000)
    add_asset(db_session, org, "SCOPEVEHIC002", AssetType.FLEET_VEHICLE, AssetOperationalStatus.RETIRED, 30000)

    kpis = dashboard_kpis(db_session, org.id)
    rollup = {row.asset_type: row for row in kpis.non_alpr_inventory}

    assert AssetType.ALPR_TRAILER not in rollup
    assert rollup[AssetType.SEMI_TRUCK].total_assets == 1
    assert Decimal(rollup[AssetType.SEMI_TRUCK].total_purchase_cost) == Decimal(120000)
    assert rollup[AssetType.FLEET_VEHICLE].total_assets == 2
    assert rollup[AssetType.FLEET_VEHICLE].retired == 1
    assert rollup[AssetType.FLEET_VEHICLE].in_service == 1


def test_admin_still_sees_every_asset_record(client, db_session, org):
    """Scoping is presentation-only: the records are untouched and still listed."""
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)
    add_asset(db_session, org, "SCOPEVEHIC001", AssetType.FLEET_VEHICLE, AssetOperationalStatus.MAINTENANCE, 40000)

    response = client.get("/assets", headers=admin_headers(client))

    assert response.status_code == status.HTTP_200_OK, response.text
    vins = {row["vin"] for row in response.json()}
    assert vins == {"SCOPEALPR0001", "SCOPETRUCK001", "SCOPEVEHIC001"}
    assert db_session.query(Asset).count() == 3
