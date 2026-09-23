"""Deleting a part must not take its stock or repair-cost history with it.

A part nobody ever stocked is a typo and is removed outright. Once it has an
inventory ledger or sits on a work order, deleting the row would rewrite the
audit trail, so the part is retired out of the list instead.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import InventoryPart, InventoryTransaction, Organization, User, UserRole


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
    organization = Organization(name="Parts Org", slug="parts-org", org_type="internal")
    db_session.add(organization)
    db_session.flush()
    db_session.add_all(
        [
            User(
                organization_id=organization.id,
                email="manager@parts.com",
                hashed_password=hash_password("password123"),
                full_name="Fleet Manager",
                role=UserRole.FLEET_MANAGER,
                is_active=True,
            ),
            User(
                organization_id=organization.id,
                email="tech@parts.com",
                hashed_password=hash_password("password123"),
                full_name="Technician",
                role=UserRole.TECHNICIAN,
                is_active=True,
            ),
        ]
    )
    db_session.commit()
    return organization


def headers(client, email):
    response = client.post("/auth/login", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_part(client, sku, quantity=0):
    response = client.post(
        "/parts",
        json={"sku": sku, "name": f"Part {sku}", "quantity_on_hand": quantity, "unit_cost": 5},
        headers=headers(client, "manager@parts.com"),
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


def test_a_part_with_no_history_is_deleted_outright(client, org, db_session):
    part_id = create_part(client, "CLEAN-1")

    response = client.delete(f"/parts/{part_id}", headers=headers(client, "manager@parts.com"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["action"] == "deleted"
    assert db_session.query(InventoryPart).count() == 0


def test_a_part_with_stock_history_is_retired_and_keeps_its_ledger(client, org, db_session):
    part_id = create_part(client, "USED-1", quantity=10)

    response = client.delete(f"/parts/{part_id}", headers=headers(client, "manager@parts.com"))

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["action"] == "retired"
    assert "stock transaction" in body["detail"]

    part = db_session.query(InventoryPart).one()
    assert part.is_active is False
    assert db_session.query(InventoryTransaction).filter_by(part_id=part.id).count() == 1


def test_retiring_the_same_part_twice_is_refused(client, org):
    part_id = create_part(client, "USED-2", quantity=4)
    client.delete(f"/parts/{part_id}", headers=headers(client, "manager@parts.com"))

    response = client.delete(f"/parts/{part_id}", headers=headers(client, "manager@parts.com"))

    assert response.status_code == status.HTTP_409_CONFLICT


def test_a_technician_cannot_delete_a_part(client, org, db_session):
    part_id = create_part(client, "CLEAN-2")

    response = client.delete(f"/parts/{part_id}", headers=headers(client, "tech@parts.com"))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert db_session.query(InventoryPart).count() == 1


def test_a_part_from_another_organization_is_not_found(client, org, db_session):
    part_id = create_part(client, "CLEAN-3")

    other = Organization(name="Other Parts", slug="other-parts-org", org_type="internal")
    db_session.add(other)
    db_session.flush()
    db_session.add(
        User(
            organization_id=other.id,
            email="manager@other.com",
            hashed_password=hash_password("password123"),
            full_name="Other Manager",
            role=UserRole.FLEET_MANAGER,
            is_active=True,
        )
    )
    db_session.commit()

    response = client.delete(f"/parts/{part_id}", headers=headers(client, "manager@other.com"))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert db_session.query(InventoryPart).count() == 1
