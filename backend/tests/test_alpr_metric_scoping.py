"""Readiness metrics count ALPR Trailers only.

Deployment lifecycle (available / deployed / in transit / maintenance / out of
service / retired) is an ALPR Trailer concept. Every other type is a real asset
that must stay in the database and stay visible, but they are tracked as plain
inventory and must never move an ALPR readiness number.

Utilization is deliberately not scoped this way: an idle truck is worth seeing,
so it reports the whole fleet and offers a filter instead.
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


def test_the_breakdown_rows_carry_the_same_states_as_the_cards(db_session, org):
    """The table and the cards read one set of buckets, so they cannot disagree."""
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPEALPR0002", AssetType.ALPR_TRAILER, AssetOperationalStatus.IN_TRANSIT)
    add_asset(db_session, org, "SCOPEALPR0003", AssetType.ALPR_TRAILER, AssetOperationalStatus.MAINTENANCE)

    kpis = dashboard_kpis(db_session, org.id)
    row = kpis.by_asset_type[0]

    assert (row.available, row.in_transit, row.in_maintenance) == (
        kpis.available,
        kpis.in_transit,
        kpis.in_maintenance,
    )
    assert (row.deployed, row.out_of_service, row.retired) == (
        kpis.deployed,
        kpis.out_of_service,
        kpis.retired,
    )


def test_the_scope_can_be_pointed_at_another_asset_type(db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)
    add_asset(db_session, org, "SCOPETRUCK002", AssetType.SEMI_TRUCK, AssetOperationalStatus.MAINTENANCE, 120000)

    kpis = dashboard_kpis(db_session, org.id, None, AssetType.SEMI_TRUCK)

    assert kpis.asset_type_scope == AssetType.SEMI_TRUCK.value
    assert kpis.fleet_size == 2
    assert kpis.available == 1
    assert kpis.in_maintenance == 1
    assert [row.asset_type for row in kpis.by_asset_type] == [AssetType.SEMI_TRUCK]
    # The excluded types are what shows up as "other", so ALPR moves there.
    assert AssetType.ALPR_TRAILER in {row.asset_type for row in kpis.non_alpr_inventory}


def test_an_unscoped_rollup_totals_every_type(db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)
    add_asset(db_session, org, "SCOPEVEHIC001", AssetType.FLEET_VEHICLE, AssetOperationalStatus.MAINTENANCE, 40000)

    kpis = dashboard_kpis(db_session, org.id, None, None)

    assert kpis.asset_type_scope == "All asset types"
    assert kpis.fleet_size == 3
    assert kpis.available == 2
    assert kpis.in_maintenance == 1
    assert len(kpis.by_asset_type) == len(list(AssetType))
    # Nothing was excluded, so there is no "other" bucket left to show.
    assert kpis.non_alpr_inventory == []


def test_the_headline_always_sums_the_breakdown_rows(db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.DEPLOYED, 120000)
    add_asset(db_session, org, "SCOPEVEHIC001", AssetType.FLEET_VEHICLE, AssetOperationalStatus.MAINTENANCE, 40000)

    for scope in (None, *AssetType):
        kpis = dashboard_kpis(db_session, org.id, None, scope)
        assert sum(row.total_assets for row in kpis.by_asset_type) == kpis.fleet_size
        assert sum(row.available for row in kpis.by_asset_type) == kpis.available
        assert sum(row.in_maintenance for row in kpis.by_asset_type) == kpis.in_maintenance


def test_the_endpoint_defaults_to_alpr_and_honors_the_filter(client, db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)
    headers = admin_headers(client)

    default = client.get("/dashboard/kpis", headers=headers)
    assert default.status_code == status.HTTP_200_OK, default.text
    assert default.json()["asset_type_scope"] == AssetType.ALPR_TRAILER.value
    assert default.json()["available"] == 1

    trucks = client.get("/dashboard/kpis", params={"asset_type": "Semi Truck"}, headers=headers)
    assert trucks.status_code == status.HTTP_200_OK, trucks.text
    assert trucks.json()["available"] == 1
    assert trucks.json()["asset_type_scope"] == "Semi Truck"

    everything = client.get("/dashboard/kpis", params={"asset_type": "All asset types"}, headers=headers)
    assert everything.status_code == status.HTTP_200_OK, everything.text
    assert everything.json()["available"] == 2


def test_an_unknown_filter_value_is_rejected(client, db_session, org):
    response = client.get(
        "/dashboard/kpis", params={"asset_type": "Spaceship"}, headers=admin_headers(client)
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


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


@pytest.mark.parametrize("asset_type", [AssetType.ATP, AssetType.SKY_CARRIER])
def test_the_newer_types_are_support_equipment_like_the_rest(db_session, org, asset_type):
    """ATP and Sky Carrier track like Semi Trucks: visible, but outside readiness."""
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    before = dashboard_kpis(db_session, org.id)

    add_asset(db_session, org, "SCOPENEW00001", asset_type, AssetOperationalStatus.AVAILABLE, 75000)
    after = dashboard_kpis(db_session, org.id)

    assert after.fleet_size == before.fleet_size
    assert after.available == before.available
    rollup = {row.asset_type: row for row in after.non_alpr_inventory}
    assert rollup[asset_type].total_assets == 1
    assert Decimal(rollup[asset_type].total_purchase_cost) == Decimal(75000)


@pytest.mark.parametrize("label", ["ATP", "Sky Carrier"])
def test_the_newer_types_can_be_created_and_scoped_through_the_api(client, db_session, org, label):
    add_asset(db_session, org, "SCOPENEW00001", AssetType(label), AssetOperationalStatus.AVAILABLE, 75000)

    response = client.get("/dashboard/kpis", params={"asset_type": label}, headers=admin_headers(client))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["asset_type_scope"] == label
    assert response.json()["fleet_size"] == 1


def test_fleet_utilization_covers_every_asset_type(db_session, org):
    """Unlike readiness, utilization reports the whole fleet: an idle truck counts."""
    from app.utilization import fleet_utilization

    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)
    add_asset(db_session, org, "SCOPEVEHIC001", AssetType.FLEET_VEHICLE, AssetOperationalStatus.AVAILABLE, 40000)

    rows = fleet_utilization(db_session, org.id)

    assert {row["vin"] for row in rows} == {"SCOPEALPR0001", "SCOPETRUCK001", "SCOPEVEHIC001"}


def test_fleet_utilization_can_be_narrowed_to_one_type(db_session, org):
    from app.utilization import fleet_utilization

    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)

    rows = fleet_utilization(db_session, org.id, asset_type=AssetType.SEMI_TRUCK)

    assert [row["vin"] for row in rows] == ["SCOPETRUCK001"]


def test_the_utilization_endpoint_reports_the_fleet_and_takes_a_filter(client, db_session, org):
    add_asset(db_session, org, "SCOPEALPR0001", AssetType.ALPR_TRAILER, AssetOperationalStatus.AVAILABLE)
    add_asset(db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000)
    headers = admin_headers(client)

    everything = client.get("/analytics/utilization", headers=headers)
    assert everything.status_code == status.HTTP_200_OK, everything.text
    assert {row["vin"] for row in everything.json()} == {"SCOPEALPR0001", "SCOPETRUCK001"}
    # The type travels with each row, so a mixed table can be read and sorted.
    assert {row["asset_type"] for row in everything.json()} == {"ALPR Trailer", "Semi Truck"}

    filtered = client.get(
        "/analytics/utilization", params={"asset_type": "Semi Truck"}, headers=headers
    )
    assert filtered.status_code == status.HTTP_200_OK, filtered.text
    assert [row["vin"] for row in filtered.json()] == ["SCOPETRUCK001"]


def test_the_utilization_filter_rejects_a_type_that_does_not_exist(client, db_session, org):
    response = client.get(
        "/analytics/utilization", params={"asset_type": "Spaceship"}, headers=admin_headers(client)
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_a_single_non_alpr_asset_can_still_be_inspected_directly(client, db_session, org):
    """The per-asset view keeps working; only the fleet rollup is scoped."""
    truck = add_asset(
        db_session, org, "SCOPETRUCK001", AssetType.SEMI_TRUCK, AssetOperationalStatus.AVAILABLE, 120000
    )

    response = client.get(f"/assets/{truck.id}/utilization", headers=admin_headers(client))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["vin"] == "SCOPETRUCK001"


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
