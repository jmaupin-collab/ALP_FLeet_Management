"""Customer visibility is scoped to the single agency their account belongs to.

The flaw these cover: customer access used to rest on per-asset grants alone, so
a grant that outlived an agency transfer kept showing another agency's asset.
Agency is now an additional scope inside the organization, read live from
Asset.agency_id, and enforced in rbac.py where every feed already passes.

Scottsdale and Mesa are two agencies in one organization, so any leak here is a
leak between customers of the same tenant rather than across tenants.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import backfill_customer_agency_ids, count_unscoped_customers
from app.models import (
    Agency,
    Asset,
    AssetAuthorization,
    AssetType,
    CustodyType,
    Deployment,
    DeploymentStatus,
    Organization,
    User,
    UserRole,
    Warehouse,
)
from app.database import get_db
from app.main import app


@pytest.fixture
def client(db_session):
    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def fleet(db_session):
    """Two agencies, one customer each, plus an unscoped customer and an admin."""
    now = datetime.now(UTC)
    org = Organization(name="Agency Scope Org", slug="agency-scope-org", org_type="internal")
    db_session.add(org)
    db_session.flush()

    scottsdale = Agency(organization_id=org.id, name="Scottsdale PD", latitude=Decimal("33.49"), longitude=Decimal("-111.92"))
    mesa = Agency(organization_id=org.id, name="Mesa PD", latitude=Decimal("33.41"), longitude=Decimal("-111.83"))
    warehouse = Warehouse(organization_id=org.id, name="Central Depot", latitude=Decimal("33.44"), longitude=Decimal("-112.07"))
    db_session.add_all([scottsdale, mesa, warehouse])
    db_session.flush()

    admin = User(
        organization_id=org.id,
        email="admin@scope.com",
        hashed_password=hash_password("password123"),
        full_name="Org Admin",
        role=UserRole.ORG_ADMIN,
        is_active=True,
    )
    scottsdale_customer = User(
        organization_id=org.id,
        agency_id=scottsdale.id,
        email="scottsdale@scope.com",
        hashed_password=hash_password("password123"),
        full_name="Scottsdale Customer",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    mesa_customer = User(
        organization_id=org.id,
        agency_id=mesa.id,
        email="mesa@scope.com",
        hashed_password=hash_password("password123"),
        full_name="Mesa Customer",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    unscoped_customer = User(
        organization_id=org.id,
        agency_id=None,
        email="unscoped@scope.com",
        hashed_password=hash_password("password123"),
        full_name="Unscoped Customer",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    technician = User(
        organization_id=org.id,
        email="tech@scope.com",
        hashed_password=hash_password("password123"),
        full_name="Technician",
        role=UserRole.TECHNICIAN,
        is_active=True,
    )
    db_session.add_all([admin, scottsdale_customer, mesa_customer, unscoped_customer, technician])
    db_session.flush()

    def make_asset(vin, agency, cost):
        return Asset(
            organization_id=org.id,
            vin=vin,
            make_model=f"{vin} Trailer",
            initial_purchase_cost=Decimal(cost),
            current_location=agency.name if agency else "Central Depot",
            current_custody_type=CustodyType.CUSTOMER_AGENCY if agency else CustodyType.WAREHOUSE_DEPOT,
            asset_type=AssetType.ALPR_TRAILER,
            agency_id=agency.id if agency else None,
            warehouse_id=None if agency else warehouse.id,
        )

    scottsdale_asset = make_asset("SCOTTS-0001", scottsdale, "10000")
    mesa_asset = make_asset("MESA-0001", mesa, "20000")
    db_session.add_all([scottsdale_asset, mesa_asset])
    db_session.flush()

    # Every customer is granted BOTH assets. Only the agency scope should keep
    # them apart, which is precisely the rule under test.
    for customer in (scottsdale_customer, mesa_customer, unscoped_customer):
        for asset in (scottsdale_asset, mesa_asset):
            db_session.add(
                AssetAuthorization(user_id=customer.id, asset_id=asset.id, can_view=True, granted_by_id=admin.id)
            )

    db_session.add_all([
        Deployment(
            organization_id=org.id,
            asset_id=scottsdale_asset.id,
            location="Scottsdale PD",
            status=DeploymentStatus.ACTIVE,
            started_at=now,
        ),
        Deployment(
            organization_id=org.id,
            asset_id=mesa_asset.id,
            location="Mesa PD",
            status=DeploymentStatus.ACTIVE,
            started_at=now,
        ),
    ])
    db_session.commit()

    return {
        "org": org,
        "scottsdale": scottsdale,
        "mesa": mesa,
        "warehouse": warehouse,
        "admin": admin,
        "scottsdale_customer": scottsdale_customer,
        "mesa_customer": mesa_customer,
        "unscoped_customer": unscoped_customer,
        "technician": technician,
        "scottsdale_asset": scottsdale_asset,
        "mesa_asset": mesa_asset,
    }


def login(client, email):
    response = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert response.status_code == status.HTTP_200_OK, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def asset_ids(client, headers, path="/assets"):
    rows = client.get(path, headers=headers).json()
    return {row["id"] for row in rows}


class TestAgencyIsolation:
    def test_scottsdale_customer_sees_only_scottsdale_assets(self, client, fleet):
        headers = login(client, "scottsdale@scope.com")

        assert asset_ids(client, headers) == {str(fleet["scottsdale_asset"].id)}

    def test_mesa_customer_sees_only_mesa_assets(self, client, fleet):
        headers = login(client, "mesa@scope.com")

        assert asset_ids(client, headers) == {str(fleet["mesa_asset"].id)}

    def test_scottsdale_customer_cannot_get_mesa_asset_by_id(self, client, fleet):
        headers = login(client, "scottsdale@scope.com")

        response = client.get(f"/assets/{fleet['mesa_asset'].id}", headers=headers)

        # 404, not 403: the asset's existence must not be confirmed.
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_customer_without_an_agency_sees_no_assets(self, client, fleet):
        """Holds grants on both assets, but no agency means no visibility."""
        headers = login(client, "unscoped@scope.com")

        assert asset_ids(client, headers) == set()
        assert client.get(f"/assets/{fleet['scottsdale_asset'].id}", headers=headers).status_code == status.HTTP_404_NOT_FOUND
        assert client.get("/dashboard/kpis", headers=headers).json()["fleet_size"] == 0

    def test_admin_sees_both_agencies(self, client, fleet):
        headers = login(client, "admin@scope.com")

        assert asset_ids(client, headers) == {
            str(fleet["scottsdale_asset"].id),
            str(fleet["mesa_asset"].id),
        }

    def test_technician_scope_is_unchanged(self, client, fleet):
        """Technicians are org-scoped, not agency-scoped, and must stay that way."""
        headers = login(client, "tech@scope.com")

        assert asset_ids(client, headers) == {
            str(fleet["scottsdale_asset"].id),
            str(fleet["mesa_asset"].id),
        }


class TestEveryCustomerFacingFeed:
    """The rule has to hold on every feed that exposes asset information."""

    def test_map_endpoint_follows_the_same_restriction(self, client, fleet):
        scottsdale = login(client, "scottsdale@scope.com")
        mesa = login(client, "mesa@scope.com")

        assert asset_ids(client, scottsdale, "/map/assets") == {str(fleet["scottsdale_asset"].id)}
        assert asset_ids(client, mesa, "/map/assets") == {str(fleet["mesa_asset"].id)}

    def test_deployment_feed_follows_the_restriction(self, client, fleet):
        headers = login(client, "scottsdale@scope.com")

        rows = client.get("/deployments", headers=headers).json()

        assert {row["asset_id"] for row in rows} == {str(fleet["scottsdale_asset"].id)}

    def test_dashboard_counts_follow_the_restriction(self, client, fleet):
        headers = login(client, "scottsdale@scope.com")

        kpis = client.get("/dashboard/kpis", headers=headers).json()

        assert kpis["fleet_size"] == 1
        assert float(kpis["total_purchase_cost"]) == 10000

    def test_search_and_filter_cannot_widen_the_scope(self, client, fleet):
        """A filter is not an escape hatch: scoping is applied to the query itself."""
        headers = login(client, "scottsdale@scope.com")

        for query in ("?q=MESA", "?q=Trailer", "?asset_type=ALPR+Trailer", "?include_archived=true"):
            assert str(fleet["mesa_asset"].id) not in asset_ids(client, headers, f"/assets{query}")

    def test_location_and_custody_data_is_not_exposed(self, client, fleet):
        headers = login(client, "scottsdale@scope.com")

        for path in ("timeline", "checklist", "meters"):
            response = client.get(f"/assets/{fleet['mesa_asset'].id}/{path}", headers=headers)
            assert response.status_code == status.HTTP_404_NOT_FOUND, path


class TestVisibilityFollowsTransfers:
    def transfer(self, client, fleet, destination_agency):
        headers = login(client, "admin@scope.com")
        return client.post(
            f"/assets/{fleet['scottsdale_asset'].id}/deployments/end-workflow",
            json={
                "disposition": "transfer_to_agency",
                "next_agency_id": str(destination_agency.id),
                "completion_notes": "Reassigned",
            },
            headers=headers,
        )

    def test_transfer_moves_visibility_between_customers(self, client, fleet, db_session):
        response = self.transfer(client, fleet, fleet["mesa"])
        assert response.status_code == status.HTTP_200_OK, response.text

        scottsdale = login(client, "scottsdale@scope.com")
        mesa = login(client, "mesa@scope.com")
        asset_id = str(fleet["scottsdale_asset"].id)

        # The grant row still exists for Scottsdale; the agency scope overrides it.
        assert asset_id not in asset_ids(client, scottsdale)
        assert client.get(f"/assets/{asset_id}", headers=scottsdale).status_code == status.HTTP_404_NOT_FOUND
        assert asset_id in asset_ids(client, mesa)
        assert client.get(f"/assets/{asset_id}", headers=mesa).status_code == status.HTTP_200_OK

    def test_transfer_is_reflected_on_the_map_too(self, client, fleet):
        self.transfer(client, fleet, fleet["mesa"])

        scottsdale = login(client, "scottsdale@scope.com")
        assert asset_ids(client, scottsdale, "/map/assets") == set()

    def test_stale_grant_survives_internally_for_audit(self, client, fleet, db_session):
        """Visibility changes, but history is not deleted."""
        self.transfer(client, fleet, fleet["mesa"])

        grants = db_session.query(AssetAuthorization).filter(
            AssetAuthorization.user_id == fleet["scottsdale_customer"].id,
            AssetAuthorization.asset_id == fleet["scottsdale_asset"].id,
        ).count()
        deployments = db_session.query(Deployment).filter(
            Deployment.asset_id == fleet["scottsdale_asset"].id
        ).count()

        assert grants == 1
        assert deployments >= 2  # original plus the transfer record

    def test_return_to_warehouse_removes_customer_visibility(self, client, fleet):
        admin = login(client, "admin@scope.com")
        response = client.post(
            f"/assets/{fleet['scottsdale_asset'].id}/deployments/end-workflow",
            json={
                "disposition": "return_to_warehouse",
                "destination_warehouse_id": str(fleet["warehouse"].id),
                "completion_notes": "Back to depot",
            },
            headers=admin,
        )
        assert response.status_code == status.HTTP_200_OK, response.text

        scottsdale = login(client, "scottsdale@scope.com")
        assert asset_ids(client, scottsdale) == set()
        assert asset_ids(client, scottsdale, "/map/assets") == set()
        # Still fully visible to staff.
        assert str(fleet["scottsdale_asset"].id) in asset_ids(client, admin)


class TestTenantIsolationStillHolds:
    def test_matching_agency_in_another_organization_is_not_visible(self, client, fleet, db_session):
        """Org isolation is the outer scope; agency does not punch through it."""
        other_org = Organization(name="Other Tenant", slug="other-tenant-scope", org_type="internal")
        db_session.add(other_org)
        db_session.flush()
        foreign_asset = Asset(
            organization_id=other_org.id,
            vin="FOREIGN-0001",
            make_model="Foreign Trailer",
            initial_purchase_cost=Decimal("1000"),
            current_location="Elsewhere",
            asset_type=AssetType.ALPR_TRAILER,
            # Deliberately points at the Scottsdale agency id from the other org.
            agency_id=fleet["scottsdale"].id,
        )
        db_session.add(foreign_asset)
        db_session.flush()
        db_session.add(
            AssetAuthorization(
                user_id=fleet["scottsdale_customer"].id, asset_id=foreign_asset.id, can_view=True
            )
        )
        db_session.commit()

        headers = login(client, "scottsdale@scope.com")

        assert str(foreign_asset.id) not in asset_ids(client, headers)
        assert client.get(f"/assets/{foreign_asset.id}", headers=headers).status_code == status.HTTP_404_NOT_FOUND


class TestAgencyAssignmentRules:
    def test_creating_a_customer_without_an_agency_is_rejected(self, client, fleet):
        headers = login(client, "admin@scope.com")

        response = client.post(
            "/users",
            json={
                "email": "new.customer@scope.com",
                "password": "password123",
                "full_name": "New Customer",
                "role": "customer",
            },
            headers=headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "agency" in response.json()["detail"].lower()

    def test_creating_a_customer_with_an_agency_succeeds(self, client, fleet):
        headers = login(client, "admin@scope.com")

        response = client.post(
            "/users",
            json={
                "email": "new.customer@scope.com",
                "password": "password123",
                "full_name": "New Customer",
                "role": "customer",
                "agency_id": str(fleet["mesa"].id),
            },
            headers=headers,
        )

        assert response.status_code == status.HTTP_201_CREATED, response.text
        assert response.json()["agency_id"] == str(fleet["mesa"].id)

    def test_an_agency_from_another_organization_is_refused(self, client, fleet, db_session):
        other_org = Organization(name="Foreign Org", slug="foreign-agency-org", org_type="internal")
        db_session.add(other_org)
        db_session.flush()
        foreign_agency = Agency(organization_id=other_org.id, name="Foreign PD")
        db_session.add(foreign_agency)
        db_session.commit()
        headers = login(client, "admin@scope.com")

        response = client.post(
            "/users",
            json={
                "email": "cross.customer@scope.com",
                "password": "password123",
                "full_name": "Cross Customer",
                "role": "customer",
                "agency_id": str(foreign_agency.id),
            },
            headers=headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_reassigning_a_customer_moves_their_visibility(self, client, fleet):
        headers = login(client, "admin@scope.com")

        response = client.patch(
            f"/users/{fleet['scottsdale_customer'].id}",
            json={"agency_id": str(fleet["mesa"].id)},
            headers=headers,
        )
        assert response.status_code == status.HTTP_200_OK, response.text

        moved = login(client, "scottsdale@scope.com")
        assert asset_ids(client, moved) == {str(fleet["mesa_asset"].id)}

    def test_promoting_a_customer_to_staff_clears_the_agency(self, client, fleet):
        headers = login(client, "admin@scope.com")

        response = client.patch(
            f"/users/{fleet['scottsdale_customer'].id}",
            json={"role": "technician"},
            headers=headers,
        )

        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["agency_id"] is None

    def test_granting_an_out_of_agency_asset_is_refused(self, client, fleet):
        """Otherwise an admin creates a grant that silently shows nothing."""
        headers = login(client, "admin@scope.com")

        response = client.post(
            f"/users/{fleet['mesa_customer'].id}/asset-authorizations",
            json={"asset_id": str(fleet["scottsdale_asset"].id), "can_view": True},
            headers=headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "agency" in response.json()["detail"].lower()


class TestBackfill:
    def test_unambiguous_customers_are_scoped_to_their_existing_agency(self, db_session, fleet):
        customer = fleet["unscoped_customer"]
        # Leave only the Mesa grant, so the agency is unambiguous.
        db_session.query(AssetAuthorization).filter(
            AssetAuthorization.user_id == customer.id,
            AssetAuthorization.asset_id == fleet["scottsdale_asset"].id,
        ).delete()
        db_session.commit()

        assert backfill_customer_agency_ids(db_session) == 1
        db_session.refresh(customer)
        assert customer.agency_id == fleet["mesa"].id

    def test_ambiguous_customers_are_left_unassigned(self, db_session, fleet):
        """Grants spanning two agencies must not be guessed at."""
        assert backfill_customer_agency_ids(db_session) == 0
        db_session.refresh(fleet["unscoped_customer"])
        assert fleet["unscoped_customer"].agency_id is None

    def test_unscoped_customers_are_counted_for_the_startup_warning(self, db_session, fleet):
        assert count_unscoped_customers(db_session) == 1
