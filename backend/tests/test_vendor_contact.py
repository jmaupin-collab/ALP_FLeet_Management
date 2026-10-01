"""Vendors carry a contact name and number.

A vendor record is only useful if someone can be reached, so both fields are
stored on the vendor itself rather than buried in a note. Both stay optional:
a shop often goes on file before anyone has a name for it.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import Organization, User, UserRole, Vendor


@pytest.fixture
def client(db_session):
    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def headers(client, db_session):
    organization = Organization(name="Vendor Org", slug="vendor-org", org_type="internal")
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        User(
            organization_id=organization.id,
            email="admin@vendor.com",
            hashed_password=hash_password("password123"),
            full_name="System Admin",
            role=UserRole.SYSTEM_ADMIN,
            is_active=True,
        )
    )
    db_session.commit()
    token = client.post(
        "/auth/login", json={"email": "admin@vendor.com", "password": "password123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_a_vendor_is_created_with_a_contact_name_and_number(client, headers, db_session):
    created = client.post(
        "/vendors",
        headers=headers,
        json={
            "name": "Acme Truck Repair",
            "specialty": "Brakes",
            "contact_name": "Dana Ruiz",
            "contact_phone": "(555) 123-4567",
        },
    )

    assert created.status_code == status.HTTP_201_CREATED, created.text
    assert created.json()["contact_name"] == "Dana Ruiz"
    assert created.json()["contact_phone"] == "(555) 123-4567"

    stored = db_session.query(Vendor).one()
    assert stored.contact_name == "Dana Ruiz"
    assert stored.contact_phone == "(555) 123-4567"


def test_the_contact_fields_are_optional(client, headers):
    created = client.post("/vendors", headers=headers, json={"name": "Nameless Towing"})

    assert created.status_code == status.HTTP_201_CREATED, created.text
    assert created.json()["contact_name"] is None
    assert created.json()["contact_phone"] is None


def test_a_phone_number_keeps_the_formatting_it_was_typed_with(client, headers):
    """Extensions and international formats have to round-trip unchanged."""
    created = client.post(
        "/vendors",
        headers=headers,
        json={"name": "Overseas Parts", "contact_phone": "+44 20 7946 0958 ext. 12"},
    )

    assert created.status_code == status.HTTP_201_CREATED, created.text
    assert created.json()["contact_phone"] == "+44 20 7946 0958 ext. 12"


def test_blank_contact_entries_are_stored_as_empty_rather_than_whitespace(client, headers):
    created = client.post(
        "/vendors",
        headers=headers,
        json={"name": "Blank Fields", "contact_name": "   ", "contact_phone": ""},
    )

    assert created.status_code == status.HTTP_201_CREATED, created.text
    assert created.json()["contact_name"] is None
    assert created.json()["contact_phone"] is None


def test_the_contact_can_be_edited_later(client, headers):
    vendor_id = client.post(
        "/vendors", headers=headers, json={"name": "Acme Truck Repair"}
    ).json()["id"]

    updated = client.patch(
        f"/vendors/{vendor_id}",
        headers=headers,
        json={"contact_name": "Dana Ruiz", "contact_phone": "555-0100"},
    )

    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["contact_name"] == "Dana Ruiz"
    assert updated.json()["contact_phone"] == "555-0100"


def test_clearing_a_contact_number_leaves_the_rest_of_the_record_alone(client, headers):
    vendor_id = client.post(
        "/vendors",
        headers=headers,
        json={"name": "Acme Truck Repair", "specialty": "Brakes", "contact_phone": "555-0100"},
    ).json()["id"]

    updated = client.patch(f"/vendors/{vendor_id}", headers=headers, json={"contact_phone": ""})

    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["contact_phone"] is None
    assert updated.json()["specialty"] == "Brakes"
    assert updated.json()["name"] == "Acme Truck Repair"


def test_a_number_longer_than_the_column_is_refused(client, headers):
    response = client.post(
        "/vendors",
        headers=headers,
        json={"name": "Long Number", "contact_phone": "5" * 65},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_the_vendor_list_carries_the_contact_details(client, headers):
    client.post(
        "/vendors",
        headers=headers,
        json={"name": "Acme Truck Repair", "contact_name": "Dana Ruiz", "contact_phone": "555-0100"},
    )

    listed = client.get("/vendors", headers=headers)

    assert listed.status_code == status.HTTP_200_OK, listed.text
    row = next(item for item in listed.json() if item["name"] == "Acme Truck Repair")
    assert row["contact_name"] == "Dana Ruiz"
    assert row["contact_phone"] == "555-0100"
