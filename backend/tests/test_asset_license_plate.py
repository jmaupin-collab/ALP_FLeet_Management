"""License plates on assets.

The plate is optional because trailers and yard equipment often have none, and
it is not unique because plates get reissued between vehicles. The VIN stays the
identifier; the plate is there so people can search the way they read a unit.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import Asset, Organization, User, UserRole


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
    organization = Organization(name="Plate Org", slug="plate-org", org_type="internal")
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        User(
            organization_id=organization.id,
            email="manager@plates.com",
            hashed_password=hash_password("password123"),
            full_name="Fleet Manager",
            role=UserRole.FLEET_MANAGER,
            is_active=True,
        )
    )
    db_session.commit()
    return organization


def headers(client):
    response = client.post(
        "/auth/login", json={"email": "manager@plates.com", "password": "password123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def payload(**overrides):
    body = {
        "vin": "PLATE-0001",
        "make_model": "Ford F-150",
        "asset_type": "Fleet Vehicle",
        "initial_purchase_cost": 42750,
        "current_location": "Main Yard",
        "current_custody_type": "Warehouse Depot",
    }
    body.update(overrides)
    return body


def test_an_asset_can_be_created_with_a_plate(client, org, db_session):
    response = client.post(
        "/assets", json=payload(license_plate="ABC1234", license_plate_state="AZ"), headers=headers(client)
    )

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()["license_plate"] == "ABC1234"
    assert response.json()["license_plate_state"] == "AZ"
    assert db_session.query(Asset).one().license_plate == "ABC1234"


def test_the_plate_is_stored_in_one_casing(client, org):
    """Typed lowercase, read back by an ALPR camera in uppercase."""
    response = client.post(
        "/assets", json=payload(license_plate="  abc 1234 ", license_plate_state="az"), headers=headers(client)
    )

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()["license_plate"] == "ABC 1234"
    assert response.json()["license_plate_state"] == "AZ"


def test_a_plate_is_optional(client, org):
    """Trailers usually have no plate; the field must not block creation."""
    response = client.post("/assets", json=payload(), headers=headers(client))

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()["license_plate"] is None


def test_two_assets_may_share_a_plate(client, org):
    """Plates get reissued, so uniqueness belongs to the VIN, not the plate."""
    first = client.post("/assets", json=payload(license_plate="ABC1234"), headers=headers(client))
    second = client.post(
        "/assets", json=payload(vin="PLATE-0002", license_plate="ABC1234"), headers=headers(client)
    )

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED, second.text


def test_editing_an_asset_updates_and_clears_the_plate(client, org):
    created = client.post(
        "/assets", json=payload(license_plate="ABC1234", license_plate_state="AZ"), headers=headers(client)
    ).json()

    updated = client.patch(
        f"/assets/{created['id']}",
        json={"license_plate": "XYZ9876", "license_plate_state": "NV"},
        headers=headers(client),
    )
    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["license_plate"] == "XYZ9876"
    assert updated.json()["license_plate_state"] == "NV"

    cleared = client.patch(
        f"/assets/{created['id']}", json={"license_plate": None}, headers=headers(client)
    )
    assert cleared.status_code == status.HTTP_200_OK, cleared.text
    assert cleared.json()["license_plate"] is None


def test_an_edit_that_omits_the_plate_leaves_it_alone(client, org):
    """Forms that predate the field must not wipe a plate they never sent."""
    created = client.post(
        "/assets", json=payload(license_plate="ABC1234"), headers=headers(client)
    ).json()

    updated = client.patch(
        f"/assets/{created['id']}", json={"make_model": "Ford F-250"}, headers=headers(client)
    )

    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["license_plate"] == "ABC1234"


def test_assets_can_be_searched_by_plate(client, org):
    client.post("/assets", json=payload(license_plate="ABC1234"), headers=headers(client))
    client.post(
        "/assets", json=payload(vin="PLATE-0002", license_plate="ZZZ9999"), headers=headers(client)
    )

    found = client.get("/assets?q=ABC12", headers=headers(client))

    assert found.status_code == status.HTTP_200_OK, found.text
    assert [row["vin"] for row in found.json()] == ["PLATE-0001"]


def test_a_plate_longer_than_the_column_is_refused(client, org, db_session):
    response = client.post(
        "/assets", json=payload(license_plate="A" * 17), headers=headers(client)
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert db_session.query(Asset).count() == 0
