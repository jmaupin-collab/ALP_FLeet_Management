"""Map location resolution: telematics vs synthetic GPS vs agency vs warehouse."""

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
    GPSLocation,
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
def org_users(db_session):
    org = Organization(name="Map Org", slug="map-org", org_type="internal")
    db_session.add(org)
    db_session.flush()
    user = User(
        organization_id=org.id,
        email="map.manager@test.com",
        hashed_password=hash_password("password123"),
        full_name="Map Manager",
        role=UserRole.FLEET_MANAGER,
        is_active=True,
    )
    warehouse = Warehouse(
        organization_id=org.id,
        name="Phoenix Yard",
        latitude=Decimal("33.4484000"),
        longitude=Decimal("-112.0740000"),
    )
    agency = Agency(
        organization_id=org.id,
        name="Houston PD",
        site_name="Central",
        latitude=Decimal("29.7604000"),
        longitude=Decimal("-95.3698000"),
    )
    asset = Asset(
        organization_id=org.id,
        vin="MAPASSET0001",
        make_model="Map Trailer",
        initial_purchase_cost=Decimal("1000"),
        current_location="Phoenix Yard",
        asset_type=AssetType.ALPR_TRAILER,
        operational_status=AssetOperationalStatus.AVAILABLE,
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
    )
    db_session.add_all([user, warehouse, agency, asset])
    db_session.flush()
    asset.warehouse_id = warehouse.id
    db_session.commit()
    return {"org": org, "user": user, "warehouse": warehouse, "agency": agency, "asset": asset}


def login(client):
    response = client.post("/auth/login", json={"email": "map.manager@test.com", "password": "password123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def map_pin(client, headers, asset_id):
    mapped = client.get("/map/assets", headers=headers).json()
    return next(row for row in mapped if row["id"] == str(asset_id))


def test_previous_warehouse_gps_plus_active_agency_deployment_moves_marker_to_agency(
    client, org_users, db_session
):
    asset = org_users["asset"]
    warehouse = org_users["warehouse"]
    agency = org_users["agency"]
    db_session.add(
        GPSLocation(
            asset_id=asset.id,
            latitude=warehouse.latitude,
            longitude=warehouse.longitude,
            location_timestamp=datetime.now(UTC),
        )
    )
    db_session.commit()

    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Houston PD - Central",
            "custody_type": "Customer / LE Agency",
            "agency_id": str(agency.id),
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["operational_status"] == "deployed"

    leftover = db_session.scalars(select(GPSLocation).where(GPSLocation.asset_id == asset.id)).all()
    assert leftover, "synthetic warehouse GPS rows must remain; no cleanup required"
    assert all(not (row.telematics_provider or row.telematics_device_id) for row in leftover)

    pin = map_pin(client, headers, asset.id)
    assert pin["operational_status"] == "deployed"
    assert pin["location_source"] == "customer"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 29.7604
    assert pytest.approx(pin["longitude"], abs=1e-4) == -95.3698


def test_genuine_recent_telematics_gps_wins_over_active_deployment(client, org_users, db_session):
    asset = org_users["asset"]
    agency = org_users["agency"]
    db_session.add(
        GPSLocation(
            asset_id=asset.id,
            telematics_provider="geotab",
            telematics_device_id="device-1",
            latitude=Decimal("32.7767000"),
            longitude=Decimal("-96.7970000"),
            location_timestamp=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    db_session.commit()

    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Houston PD - Central",
            "custody_type": "Customer / LE Agency",
            "agency_id": str(agency.id),
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["operational_status"] == "deployed"

    pin = map_pin(client, headers, asset.id)
    assert pin["operational_status"] == "deployed"
    assert pin["location_source"] == "gps"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 32.7767
    assert pytest.approx(pin["longitude"], abs=1e-4) == -96.7970


def test_warehouse_asset_uses_warehouse_coordinates(client, org_users):
    asset = org_users["asset"]
    headers = login(client)
    pin = map_pin(client, headers, asset.id)
    assert pin["operational_status"] == "available"
    assert pin["location_source"] == "warehouse"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 33.4484
    assert pytest.approx(pin["longitude"], abs=1e-4) == -112.0740


def test_in_transit_keeps_last_known_warehouse_pin(client, org_users):
    asset = org_users["asset"]
    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "To agency yard",
            "custody_type": "In Transit",
            "carrier_name": "FedEx",
            "tracking_code": "1Z999",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["operational_status"] == "in_transit"

    pin = map_pin(client, headers, asset.id)
    assert pin["operational_status"] == "in_transit"
    assert pin["location_source"] == "in_transit"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 33.4484
    assert pytest.approx(pin["longitude"], abs=1e-4) == -112.0740


def test_location_name_deploy_ignores_synthetic_gps_and_uses_agency(client, org_users, db_session):
    """Old start-deploy UI sent location text only, not agency_id."""
    asset = org_users["asset"]
    warehouse = org_users["warehouse"]
    db_session.add(
        GPSLocation(
            asset_id=asset.id,
            latitude=warehouse.latitude,
            longitude=warehouse.longitude,
            location_timestamp=datetime.now(UTC),
        )
    )
    db_session.commit()

    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Houston PD - Central",
            "custody_type": "Customer / LE Agency",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["operational_status"] == "deployed"
    assert started.json()["agency_id"] == str(org_users["agency"].id)

    pin = map_pin(client, headers, asset.id)
    assert pin["location_source"] == "customer"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 29.7604
    assert pytest.approx(pin["longitude"], abs=1e-4) == -95.3698


def test_deploy_address_coords_move_marker_off_warehouse_gps(client, org_users, db_session, monkeypatch):
    asset = org_users["asset"]
    warehouse = org_users["warehouse"]
    db_session.add(
        GPSLocation(
            asset_id=asset.id,
            latitude=warehouse.latitude,
            longitude=warehouse.longitude,
            location_timestamp=datetime.now(UTC),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.geocoding.geocode_address",
        lambda address: (Decimal("37.2090000"), Decimal("-93.2923000"))
        if "springfield" in address.lower()
        else None,
    )

    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Phoneix,AZ",
            "custody_type": "Customer / LE Agency",
            "address": "1317 S Farm Rd 131, Springfield, MO 65807",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["operational_status"] == "deployed"

    pin = map_pin(client, headers, asset.id)
    assert pin["operational_status"] == "deployed"
    assert pin["location_source"] == "deployment"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 37.2090
    assert pytest.approx(pin["longitude"], abs=1e-4) == -93.2923


def test_deployed_address_uses_street_level_coordinates(client, org_users, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.geocoding.geocode_address",
        lambda address: (Decimal("37.1921015"), Decimal("-93.3525271")),
    )
    headers = login(client)
    started = client.post(
        f"/assets/{org_users['asset'].id}/deployments",
        headers=headers,
        json={
            "location": "Field site",
            "custody_type": "Customer / LE Agency",
            "address": "1317 S Farm Rd 131, Springfield, MO 65807",
        },
    )
    assert started.status_code == 200, started.text
    pin = map_pin(client, headers, org_users["asset"].id)
    assert pytest.approx(pin["latitude"], abs=1e-6) == 37.1921015
    assert pytest.approx(pin["longitude"], abs=1e-6) == -93.3525271
    assert abs(pin["latitude"] - 37.2081729) > 0.01


def test_explicit_deploy_lat_lng_used_when_no_agency(client, org_users, db_session):
    asset = org_users["asset"]
    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Field site",
            "custody_type": "Customer / LE Agency",
            "latitude": 37.2089,
            "longitude": -93.2923,
        },
    )
    assert started.status_code == 200, started.text
    pin = map_pin(client, headers, asset.id)
    assert pin["location_source"] == "deployment"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 37.2089
    assert pytest.approx(pin["longitude"], abs=1e-4) == -93.2923


def test_entered_street_address_overrides_agency_hq(client, org_users, monkeypatch):
    asset = org_users["asset"]
    agency = org_users["agency"]
    monkeypatch.setattr(
        "app.geocoding.geocode_address",
        lambda address: (Decimal("37.1921000"), Decimal("-93.3525000"))
        if "farm rd" in address.lower()
        else None,
    )
    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Houston PD - Central",
            "custody_type": "Customer / LE Agency",
            "agency_id": str(agency.id),
            "address": "1317 S Farm Rd 131, Springfield, MO 65807",
        },
    )
    assert started.status_code == 200, started.text
    pin = map_pin(client, headers, asset.id)
    assert pin["operational_status"] == "deployed"
    assert pin["location_source"] == "deployment"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 37.1921
    assert pytest.approx(pin["longitude"], abs=1e-4) == -93.3525


def test_generic_city_state_does_not_override_agency_hq(client, org_users, monkeypatch):
    asset = org_users["asset"]
    agency = org_users["agency"]
    monkeypatch.setattr(
        "app.geocoding.geocode_address",
        lambda address: (Decimal("37.2090000"), Decimal("-93.2923000")),
    )
    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Houston PD - Central",
            "custody_type": "Customer / LE Agency",
            "agency_id": str(agency.id),
            "address": "Springfield, MO 65807",
        },
    )
    assert started.status_code == 200, started.text
    pin = map_pin(client, headers, asset.id)
    assert pytest.approx(pin["latitude"], abs=1e-4) == 29.7604
    assert pytest.approx(pin["longitude"], abs=1e-4) == -95.3698
    assert pin["location_source"] == "customer"


def test_telematics_still_supersedes_entered_address(client, org_users, db_session, monkeypatch):
    asset = org_users["asset"]
    db_session.add(
        GPSLocation(
            asset_id=asset.id,
            telematics_provider="samsara",
            telematics_device_id="trk-9",
            latitude=Decimal("32.7767000"),
            longitude=Decimal("-96.7970000"),
            location_timestamp=datetime.now(UTC),
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.geocoding.geocode_address",
        lambda address: (Decimal("37.2090000"), Decimal("-93.2923000")),
    )
    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Field site",
            "custody_type": "Customer / LE Agency",
            "address": "Springfield, MO 65807",
        },
    )
    assert started.status_code == 200, started.text
    pin = map_pin(client, headers, asset.id)
    assert pin["location_source"] == "gps"
    assert pytest.approx(pin["latitude"], abs=1e-4) == 32.7767


def test_start_deployment_does_not_write_synthetic_gps(client, org_users, db_session):
    asset = org_users["asset"]
    agency = org_users["agency"]
    headers = login(client)
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "Houston PD - Central",
            "custody_type": "Customer / LE Agency",
            "agency_id": str(agency.id),
            "latitude": 29.7604,
            "longitude": -95.3698,
        },
    )
    assert started.status_code == 200, started.text
    rows = db_session.scalars(select(GPSLocation).where(GPSLocation.asset_id == asset.id)).all()
    assert rows == []


def test_agency_address_geocodes_and_drives_map_on_deploy(client, org_users, monkeypatch):
    monkeypatch.setattr(
        "app.geocoding.geocode_address",
        lambda address: (Decimal("33.4484000"), Decimal("-112.0740000")),
    )
    headers = login(client)
    created = client.post(
        "/agencies",
        headers=headers,
        json={
            "name": "Tempe PD",
            "agency_type": "Law Enforcement",
            "address": "120 E 5th St, Tempe, AZ 85281",
        },
    )
    assert created.status_code == 201, created.text
    agency = created.json()
    assert agency["address"] == "120 E 5th St, Tempe, AZ 85281"
    assert pytest.approx(float(agency["latitude"]), abs=1e-4) == 33.4484
    assert pytest.approx(float(agency["longitude"]), abs=1e-4) == -112.0740

    listed = client.get("/agencies", headers=headers).json()
    row = next(item for item in listed if item["id"] == agency["id"])
    assert row["address"] == agency["address"]
    assert row["latitude"] is not None

    started = client.post(
        f"/assets/{org_users['asset'].id}/deployments",
        headers=headers,
        json={
            "location": agency["name"],
            "custody_type": "Customer / LE Agency",
            "agency_id": agency["id"],
            "address": agency["address"],
            "latitude": agency["latitude"],
            "longitude": agency["longitude"],
        },
    )
    assert started.status_code == 200, started.text
    pin = map_pin(client, headers, org_users["asset"].id)
    assert pytest.approx(pin["latitude"], abs=1e-4) == 33.4484
    assert pytest.approx(pin["longitude"], abs=1e-4) == -112.0740


def test_starting_a_deployment_names_the_location_from_the_agency(client, org_users):
    """The form sends only the agency; the server supplies the location name."""
    headers = login(client)
    agency = org_users["agency"]

    started = client.post(
        f"/assets/{org_users['asset'].id}/deployments",
        headers=headers,
        json={"custody_type": "Customer / LE Agency", "agency_id": str(agency.id)},
    )

    assert started.status_code == 200, started.text
    assert started.json()["current_location"] == agency.name

    pin = map_pin(client, headers, org_users["asset"].id)
    assert pytest.approx(pin["latitude"], abs=1e-4) == float(agency.latitude)
    assert pin["location_source"] == "customer"


def test_starting_a_warehouse_deployment_names_the_location_from_the_warehouse(client, org_users):
    headers = login(client)
    warehouse = org_users["warehouse"]

    started = client.post(
        f"/assets/{org_users['asset'].id}/deployments",
        headers=headers,
        json={"custody_type": "Warehouse Depot", "warehouse_id": str(warehouse.id)},
    )

    assert started.status_code == 200, started.text
    assert started.json()["current_location"] == warehouse.name


def test_starting_a_deployment_with_no_site_and_no_location_is_rejected(client, org_users):
    """Without a site link there is nothing to derive a location from."""
    headers = login(client)

    started = client.post(
        f"/assets/{org_users['asset'].id}/deployments",
        headers=headers,
        json={"custody_type": "Customer / LE Agency"},
    )

    assert started.status_code == 422


def test_a_location_string_alone_still_works_for_a_site_not_in_the_directory(client, org_users):
    headers = login(client)

    started = client.post(
        f"/assets/{org_users['asset'].id}/deployments",
        headers=headers,
        json={"custody_type": "Customer / LE Agency", "location": "Pop-up site, Mesa AZ"},
    )

    assert started.status_code == 200, started.text
    assert started.json()["current_location"] == "Pop-up site, Mesa AZ"

