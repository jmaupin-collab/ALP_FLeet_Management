"""Asset creation via the site picker on the Add New Asset form.

The form no longer asks for a typed location: it sends a warehouse or agency
link chosen from the custody type, and the server derives the location from it.
These pin that contract, including the unused link being null rather than "".
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import Agency, Asset, CustodyType, Organization, User, UserRole, Warehouse


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
    organization = Organization(name="Site Org", slug="site-org", org_type="internal")
    db_session.add(organization)
    db_session.flush()

    manager = User(
        organization_id=organization.id,
        email="manager@sites.com",
        hashed_password=hash_password("password123"),
        full_name="Fleet Manager",
        role=UserRole.FLEET_MANAGER,
        is_active=True,
    )
    warehouse = Warehouse(organization_id=organization.id, name="Phoenix Depot")
    agency = Agency(organization_id=organization.id, name="Tempe PD")
    db_session.add_all([manager, warehouse, agency])
    db_session.commit()
    return {"org": organization, "warehouse": warehouse, "agency": agency}


def token_for(client):
    response = client.post("/auth/login", json={"email": "manager@sites.com", "password": "password123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def payload(**overrides):
    """Exactly the shape the create form posts."""
    body = {
        "vin": "SITE-0001",
        "make_model": "Flock Safety Falcon",
        "asset_type": "ALPR Trailer",
        "initial_purchase_cost": 18500,
        "current_location": "",
        "current_custody_type": "Warehouse Depot",
        "warehouse_id": None,
        "agency_id": None,
    }
    body.update(overrides)
    return body


def test_warehouse_custody_takes_its_location_from_the_warehouse(client, org, db_session):
    response = client.post(
        "/assets",
        json=payload(current_location="Phoenix Depot", warehouse_id=str(org["warehouse"].id)),
        headers=token_for(client),
    )

    assert response.status_code == status.HTTP_201_CREATED, response.text
    asset = db_session.query(Asset).one()
    assert asset.warehouse_id == org["warehouse"].id
    assert asset.agency_id is None
    assert asset.current_location == "Phoenix Depot"
    assert asset.operational_status.value == "available"


def test_agency_custody_links_the_agency_and_deploys(client, org, db_session):
    response = client.post(
        "/assets",
        json=payload(
            current_location="Tempe PD",
            current_custody_type="Customer / LE Agency",
            agency_id=str(org["agency"].id),
        ),
        headers=token_for(client),
    )

    assert response.status_code == status.HTTP_201_CREATED, response.text
    asset = db_session.query(Asset).one()
    assert asset.agency_id == org["agency"].id
    assert asset.warehouse_id is None
    assert asset.current_location == "Tempe PD"
    assert asset.current_custody_type == CustodyType.CUSTOMER_AGENCY
    assert asset.operational_status.value == "deployed"


def test_in_transit_custody_records_the_destination_warehouse(client, org, db_session):
    response = client.post(
        "/assets",
        json=payload(
            current_location="Phoenix Depot",
            current_custody_type="In Transit",
            warehouse_id=str(org["warehouse"].id),
        ),
        headers=token_for(client),
    )

    assert response.status_code == status.HTTP_201_CREATED, response.text
    asset = db_session.query(Asset).one()
    assert asset.warehouse_id == org["warehouse"].id
    assert asset.operational_status.value == "in_transit"


def test_blank_site_id_is_rejected_rather_than_silently_dropped(client, org):
    """The old form sent "" for the unused link, which is not a UUID."""
    response = client.post(
        "/assets",
        json=payload(current_location="Somewhere", warehouse_id=""),
        headers=token_for(client),
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_a_site_from_another_organization_is_refused(client, org, db_session):
    foreign = Organization(name="Foreign", slug="foreign-site-org", org_type="internal")
    db_session.add(foreign)
    db_session.flush()
    foreign_warehouse = Warehouse(organization_id=foreign.id, name="Foreign Depot")
    db_session.add(foreign_warehouse)
    db_session.commit()

    response = client.post(
        "/assets",
        json=payload(current_location="Foreign Depot", warehouse_id=str(foreign_warehouse.id)),
        headers=token_for(client),
    )

    assert response.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}
    assert db_session.query(Asset).count() == 0
