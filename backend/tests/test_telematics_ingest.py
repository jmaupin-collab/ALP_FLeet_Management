"""The position ingest endpoint: shared-key auth, matching, and geofence effects.

The caller is a machine, so it authenticates with a key rather than a session.
The endpoint must refuse everything when no key is configured, must not let one
bad row discard a whole batch, and must never accept a write for another tenant.
"""

from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models import (
    Asset,
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    GPSLocation,
    Notification,
    Organization,
    User,
    UserRole,
    Warehouse,
)

KEY = "test-ingest-key"
YARD = ("33.6846", "-112.0491")
AWAY = ("33.7300", "-112.0491")


@pytest.fixture(autouse=True)
def configured_key(monkeypatch):
    """A key is configured for every test here unless one clears it."""
    monkeypatch.setattr(get_settings(), "telematics_api_key", KEY, raising=False)


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
    organization = Organization(name="Ingest Org", slug="ingest-org", org_type="internal")
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        User(
            organization_id=organization.id,
            email="manager@ingest.com",
            hashed_password=hash_password("password123"),
            full_name="Fleet Manager",
            role=UserRole.FLEET_MANAGER,
            is_active=True,
        )
    )
    db_session.add(
        Warehouse(
            organization_id=organization.id,
            name="Mohawk DC",
            latitude=Decimal(YARD[0]),
            longitude=Decimal(YARD[1]),
        )
    )
    db_session.commit()
    return organization


@pytest.fixture
def asset(db_session, org):
    row = Asset(
        organization_id=org.id,
        vin="INGEST0000001",
        make_model="Falcon",
        asset_type=AssetType.ALPR_TRAILER,
        initial_purchase_cost=Decimal(18000),
        current_location="Mohawk DC",
        current_custody_type=CustodyType.CUSTOMER_AGENCY,
        operational_status=AssetOperationalStatus.DEPLOYED,
        telematics_provider="geotab",
        telematics_device_id="GEO-123",
        telematics_tracking_enabled=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def unenrolled_asset(db_session, org):
    """A unit the provider knows about that nobody asked to track."""
    row = Asset(
        organization_id=org.id,
        vin="INGEST0000002",
        make_model="Falcon",
        asset_type=AssetType.ALPR_TRAILER,
        initial_purchase_cost=Decimal(18000),
        current_location="Mohawk DC",
        current_custody_type=CustodyType.CUSTOMER_AGENCY,
        operational_status=AssetOperationalStatus.DEPLOYED,
        telematics_provider="geotab",
        telematics_device_id="GEO-999",
        telematics_tracking_enabled=False,
    )
    db_session.add(row)
    db_session.commit()
    return row


def login(client, email):
    token = client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def post(client, fixes, key=KEY):
    headers = {"X-API-Key": key} if key is not None else {}
    return client.post("/telematics/locations", json={"fixes": fixes}, headers=headers)


def fix(**overrides):
    base = {"device_id": "GEO-123", "latitude": YARD[0], "longitude": YARD[1]}
    base.update(overrides)
    return base


# --- authentication --------------------------------------------------------


def test_a_position_cannot_be_posted_without_the_key(client, asset):
    assert post(client, [fix()], key=None).status_code == status.HTTP_401_UNAUTHORIZED


def test_a_wrong_key_is_rejected(client, asset):
    assert post(client, [fix()], key="not-the-key").status_code == status.HTTP_401_UNAUTHORIZED


def test_with_no_key_configured_the_endpoint_refuses_everything(client, asset, monkeypatch):
    """Absent configuration must not mean 'open to anyone'."""
    monkeypatch.setattr(get_settings(), "telematics_api_key", None, raising=False)

    assert post(client, [fix()]).status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# --- matching and recording ------------------------------------------------


def test_a_fix_is_stored_against_the_asset_that_owns_the_device(client, db_session, asset):
    response = post(client, [fix()])

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["accepted"] == 1
    stored = db_session.query(GPSLocation).filter(GPSLocation.asset_id == asset.id).all()
    assert len(stored) == 1
    assert stored[0].telematics_provider == "geotab"


def test_an_asset_can_be_addressed_by_its_own_id(client, db_session, asset):
    response = post(client, [fix(device_id=None, asset_id=str(asset.id))])

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["accepted"] == 1


def test_a_fix_with_nothing_to_match_on_is_rejected(client, asset):
    response = post(client, [{"latitude": YARD[0], "longitude": YARD[1]}])

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_an_unknown_device_is_reported_without_failing_the_batch(client, db_session, asset):
    """A tracker moved to another unit must not block everyone else's positions."""
    response = post(client, [fix(device_id="GHOST-1"), fix()])

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["accepted"] == 1
    assert response.json()["unmatched"] == ["GHOST-1"]


def test_an_impossible_coordinate_is_rejected(client, asset):
    response = post(client, [fix(latitude="99.0")])

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_unknown_fields_are_rejected_rather_than_silently_dropped(client, asset):
    response = post(client, [fix(battery="full")])

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# --- only what was asked for ------------------------------------------------


def test_a_position_for_a_unit_nobody_enrolled_is_discarded(client, db_session, unenrolled_asset):
    """A provider sweep carries the whole account; only the selection is kept."""
    response = post(client, [fix(device_id="GEO-999")])

    body = response.json()
    assert body["accepted"] == 0
    assert body["not_enrolled"] == ["GEO-999"]
    assert db_session.query(GPSLocation).filter(
        GPSLocation.asset_id == unenrolled_asset.id
    ).count() == 0


def test_an_unenrolled_unit_is_reported_apart_from_an_unknown_one(client, asset, unenrolled_asset):
    """Not asked for and not recognised are different problems."""
    body = post(client, [fix(), fix(device_id="GEO-999"), fix(device_id="GHOST-1")]).json()

    assert body["accepted"] == 1
    assert body["not_enrolled"] == ["GEO-999"]
    assert body["unmatched"] == ["GHOST-1"]


def test_addressing_an_unenrolled_unit_by_id_does_not_bypass_the_choice(client, unenrolled_asset):
    body = post(client, [fix(device_id=None, asset_id=str(unenrolled_asset.id))]).json()

    assert body["accepted"] == 0


def test_turning_tracking_off_stops_the_positions(client, db_session, asset):
    post(client, [fix()])
    asset.telematics_tracking_enabled = False
    db_session.commit()

    body = post(client, [fix(latitude=AWAY[0], longitude=AWAY[1])]).json()

    assert body["accepted"] == 0
    assert db_session.query(GPSLocation).filter(GPSLocation.asset_id == asset.id).count() == 1


def test_the_poller_is_told_exactly_which_devices_to_ask_for(client, asset, unenrolled_asset):
    """So the pull stays narrow at the provider, not just at our door."""
    response = client.get("/telematics/tracked-devices", headers={"X-API-Key": KEY})

    devices = response.json()["devices"]
    assert [row["device_id"] for row in devices] == ["GEO-123"]
    assert devices[0]["provider"] == "geotab"


def test_the_device_list_needs_the_key_too(client, asset):
    response = client.get("/telematics/tracked-devices")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# --- choosing what to track -------------------------------------------------


def test_a_manager_can_enroll_a_unit_and_it_starts_reporting(client, db_session, unenrolled_asset):
    headers = login(client, "manager@ingest.com")

    enrolled = client.put(
        f"/assets/{unenrolled_asset.id}/tracking",
        headers=headers,
        json={"enabled": True, "device_id": "GEO-999", "provider": "geotab"},
    )

    assert enrolled.status_code == status.HTTP_200_OK, enrolled.text
    assert enrolled.json()["tracking_enabled"] is True
    assert post(client, [fix(device_id="GEO-999")]).json()["accepted"] == 1


def test_enrolling_without_a_device_id_is_refused(client, unenrolled_asset):
    headers = login(client, "manager@ingest.com")

    response = client.put(
        f"/assets/{unenrolled_asset.id}/tracking",
        headers=headers,
        json={"enabled": True, "device_id": "  "},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_one_device_cannot_be_pointed_at_two_units(client, asset, unenrolled_asset):
    """Otherwise every fix from it would be filed against whichever matched first."""
    headers = login(client, "manager@ingest.com")

    response = client.put(
        f"/assets/{unenrolled_asset.id}/tracking",
        headers=headers,
        json={"enabled": True, "device_id": "GEO-123"},
    )

    assert response.status_code == status.HTTP_409_CONFLICT


# --- geofence effects ------------------------------------------------------


def test_arriving_at_the_yard_checks_the_unit_in_and_reports_it(client, db_session, asset, org):
    response = post(client, [fix()])

    body = response.json()
    assert body["transitions"] == [
        {
            "asset": "INGEST0000001",
            "event": "entered",
            "zone": "Mohawk DC",
            "unexpected": False,
            "action": "Checked in to Mohawk DC",
        }
    ]
    db_session.refresh(asset)
    assert asset.current_custody_type == CustodyType.WAREHOUSE_DEPOT


def test_leaving_the_yard_unannounced_alerts_the_fleet_team(client, db_session, asset, org):
    post(client, [fix()])

    body = post(client, [fix(latitude=AWAY[0], longitude=AWAY[1])]).json()

    assert body["transitions"][0]["event"] == "exited"
    assert body["transitions"][0]["unexpected"] is True
    titles = [row.title for row in db_session.query(Notification).all()]
    assert any("left Mohawk DC" in title for title in titles)
