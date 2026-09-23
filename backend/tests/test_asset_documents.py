"""The per-vehicle document folder and its expiration reminders.

Registration, insurance, and title live against the asset. Each document sets
how far ahead of expiration it should start nagging, because a registration
renewal needs more lead time than a warranty.
"""

from datetime import date, timedelta
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
    organization = Organization(name="Doc Org", slug="doc-org", org_type="internal")
    db_session.add(organization)
    db_session.flush()
    db_session.add_all(
        [
            User(
                organization_id=organization.id,
                email="manager@docs.com",
                hashed_password=hash_password("password123"),
                full_name="Fleet Manager",
                role=UserRole.FLEET_MANAGER,
                is_active=True,
            ),
            User(
                organization_id=organization.id,
                email="tech@docs.com",
                hashed_password=hash_password("password123"),
                full_name="Technician",
                role=UserRole.TECHNICIAN,
                is_active=True,
            ),
        ]
    )
    truck = Asset(
        organization_id=organization.id,
        vin="DOCTRUCK00001",
        license_plate="ABC1234",
        make_model="Ford F-150",
        asset_type=AssetType.FLEET_VEHICLE,
        initial_purchase_cost=Decimal("42750"),
        current_location="Yard",
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
        operational_status=AssetOperationalStatus.AVAILABLE,
    )
    trailer = Asset(
        organization_id=organization.id,
        vin="DOCTRAILER001",
        make_model="Flock Falcon",
        asset_type=AssetType.ALPR_TRAILER,
        initial_purchase_cost=Decimal("18500"),
        current_location="Yard",
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
        operational_status=AssetOperationalStatus.AVAILABLE,
    )
    db_session.add_all([truck, trailer])
    db_session.commit()
    return {"org": organization, "truck": truck, "trailer": trailer}


def headers(client, email="manager@docs.com"):
    response = client.post("/auth/login", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def upload(client, asset_id, *, document_type="registration", title="2026 Registration", expires=None, reminder=None, email="manager@docs.com"):
    data = {"document_type": document_type, "title": title}
    if expires is not None:
        data["expiration_date"] = expires.isoformat()
    if reminder is not None:
        data["reminder_days"] = str(reminder)
    return client.post(
        f"/assets/{asset_id}/documents",
        data=data,
        files={"file": ("reg.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=headers(client, email),
    )


def test_a_document_is_filed_against_the_vehicle(client, org):
    response = upload(client, org["truck"].id, expires=date.today() + timedelta(days=200))

    assert response.status_code == status.HTTP_201_CREATED, response.text
    body = response.json()
    assert body["document_type_label"] == "Registration"
    assert body["expiration_state"] == "ok"

    listed = client.get(f"/assets/{org['truck'].id}/documents", headers=headers(client)).json()
    assert [row["title"] for row in listed] == ["2026 Registration"]


def test_the_reminder_window_decides_when_a_document_reads_as_due(client, org):
    """Same expiration date, different lead times, different state."""
    in_45_days = date.today() + timedelta(days=45)

    default_lead = upload(client, org["truck"].id, title="Default", expires=in_45_days).json()
    long_lead = upload(
        client, org["truck"].id, title="Early warning", expires=in_45_days, reminder=90
    ).json()

    assert default_lead["reminder_days"] == 30
    assert default_lead["expiration_state"] == "ok"
    assert long_lead["reminder_days"] == 90
    assert long_lead["expiration_state"] == "soon"


def test_a_lapsed_document_reads_as_expired(client, org):
    body = upload(client, org["truck"].id, expires=date.today() - timedelta(days=3)).json()

    assert body["expiration_state"] == "expired"
    assert body["days_to_expire"] == -3


def test_the_reminder_window_can_be_changed_after_filing(client, org):
    created = upload(client, org["truck"].id, expires=date.today() + timedelta(days=45)).json()
    assert created["expiration_state"] == "ok"

    updated = client.patch(
        f"/documents/{created['id']}", json={"reminder_days": 60}, headers=headers(client)
    )

    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["reminder_days"] == 60
    assert updated.json()["expiration_state"] == "soon"


def test_an_absurd_reminder_window_is_clamped_not_rejected(client, org):
    """A bad lead time should never stop someone filing a title."""
    response = upload(client, org["truck"].id, expires=date.today() + timedelta(days=10), reminder=99999)

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()["reminder_days"] == 365


def test_an_expiring_document_raises_an_attention_item(client, org):
    upload(client, org["truck"].id, title="Insurance card", document_type="insurance",
           expires=date.today() + timedelta(days=10))

    items = client.get("/attention", headers=headers(client)).json()

    doc_items = [row for row in items if row["kind"].startswith("doc_expiring")]
    assert len(doc_items) == 1
    assert doc_items[0]["severity"] == "warning"
    assert doc_items[0]["asset_id"] == str(org["truck"].id)


def test_a_long_reminder_window_raises_the_attention_item_early(client, org):
    """A 200-day-out registration with a 365-day window should already warn."""
    upload(client, org["truck"].id, expires=date.today() + timedelta(days=200), reminder=365)

    items = client.get("/attention", headers=headers(client)).json()

    assert any(row["kind"].startswith("doc_expiring") for row in items)


def test_an_expired_document_is_critical(client, org):
    upload(client, org["truck"].id, expires=date.today() - timedelta(days=1))

    items = client.get("/attention", headers=headers(client)).json()

    expired = [row for row in items if row["kind"] == "doc_expired"]
    assert len(expired) == 1
    assert expired[0]["severity"] == "critical"


class TestFleetFolder:
    def test_every_document_is_listed_with_its_vehicle(self, client, org):
        upload(client, org["truck"].id, title="Truck reg")
        upload(client, org["trailer"].id, title="Trailer title", document_type="title")

        rows = client.get("/documents", headers=headers(client)).json()

        assert {row["title"] for row in rows} == {"Truck reg", "Trailer title"}
        truck_row = next(row for row in rows if row["title"] == "Truck reg")
        assert truck_row["asset"]["vin"] == "DOCTRUCK00001"
        assert truck_row["asset"]["license_plate"] == "ABC1234"

    def test_the_list_can_be_filtered_to_what_needs_attention(self, client, org):
        upload(client, org["truck"].id, title="Current", expires=date.today() + timedelta(days=300))
        upload(client, org["truck"].id, title="Lapsed", expires=date.today() - timedelta(days=5))
        upload(client, org["truck"].id, title="Due soon", expires=date.today() + timedelta(days=5))
        upload(client, org["truck"].id, title="No date")

        expired = client.get("/documents?state=expired", headers=headers(client)).json()
        expiring = client.get("/documents?state=expiring", headers=headers(client)).json()
        undated = client.get("/documents?state=no_expiry", headers=headers(client)).json()

        assert [row["title"] for row in expired] == ["Lapsed"]
        assert [row["title"] for row in expiring] == ["Due soon"]
        assert [row["title"] for row in undated] == ["No date"]

    def test_the_list_can_be_filtered_by_type(self, client, org):
        upload(client, org["truck"].id, title="Reg", document_type="registration")
        upload(client, org["truck"].id, title="Ins", document_type="insurance")

        rows = client.get("/documents?document_type=insurance", headers=headers(client)).json()

        assert [row["title"] for row in rows] == ["Ins"]

    def test_the_summary_reports_vehicles_missing_a_core_document(self, client, org):
        upload(client, org["truck"].id, document_type="registration")
        upload(client, org["truck"].id, document_type="insurance")
        upload(client, org["truck"].id, document_type="title")

        summary = client.get("/documents/summary", headers=headers(client)).json()

        assert summary["counts"]["total"] == 3
        gaps = {row["vin"]: row["missing"] for row in summary["missing_core_documents"]}
        assert "DOCTRUCK00001" not in gaps
        assert set(gaps["DOCTRAILER001"]) == {"registration", "insurance", "title"}

    def test_a_technician_cannot_open_the_folder(self, client, org):
        upload(client, org["truck"].id)

        response = client.get("/documents", headers=headers(client, "tech@docs.com"))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_another_organizations_documents_are_not_listed(self, client, org, db_session):
        upload(client, org["truck"].id, title="Ours")

        other = Organization(name="Other Docs", slug="other-docs-org", org_type="internal")
        db_session.add(other)
        db_session.flush()
        db_session.add(
            User(
                organization_id=other.id,
                email="manager@otherdocs.com",
                hashed_password=hash_password("password123"),
                full_name="Other Manager",
                role=UserRole.FLEET_MANAGER,
                is_active=True,
            )
        )
        db_session.commit()

        rows = client.get("/documents", headers=headers(client, "manager@otherdocs.com")).json()

        assert rows == []
