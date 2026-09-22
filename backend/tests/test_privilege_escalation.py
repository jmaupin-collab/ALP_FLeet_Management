"""Regression tests for role elevation, session invalidation, and seeding safety.

These cover the paths where a bug hands an attacker more authority than they
were granted, so each test asserts the denial rather than just a status family.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.database import get_db
from app.main import app
from app.models import Asset, AssetType, Organization, User, UserRole
from app.rbac import ROLE_PERMISSIONS, can_assign_role, canonical_role, user_has_permission


@pytest.fixture
def client(db_session):
    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def org_with_admin(db_session):
    org = Organization(name="Escalation Org", slug="escalation-org", org_type="internal")
    other = Organization(name="Other Org", slug="other-org", org_type="customer")
    db_session.add_all([org, other])
    db_session.flush()

    org_admin = User(
        organization_id=org.id,
        email="orgadmin@test.com",
        hashed_password=hash_password("password123"),
        full_name="Org Admin",
        role=UserRole.ORG_ADMIN,
        is_active=True,
    )
    fleet_manager = User(
        organization_id=org.id,
        email="fleet@test.com",
        hashed_password=hash_password("password123"),
        full_name="Fleet Manager",
        role=UserRole.FLEET_MANAGER,
        is_active=True,
    )
    system_admin = User(
        organization_id=org.id,
        email="sysadmin@test.com",
        hashed_password=hash_password("password123"),
        full_name="System Admin",
        role=UserRole.SYSTEM_ADMIN,
        is_active=True,
    )
    other_asset = Asset(
        organization_id=other.id,
        vin="OTHERORGVIN01",
        make_model="Other Org Trailer",
        initial_purchase_cost=1000,
        current_location="Elsewhere",
        asset_type=AssetType.ALPR_TRAILER,
    )
    db_session.add_all([org_admin, fleet_manager, system_admin, other_asset])
    db_session.commit()

    return {
        "org": org,
        "other": other,
        "org_admin": org_admin,
        "fleet_manager": fleet_manager,
        "system_admin": system_admin,
        "other_asset": other_asset,
    }


def token_for(client, email, password="password123"):
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestRoleElevation:
    def test_org_admin_cannot_create_system_admin(self, client, org_with_admin):
        token = token_for(client, "orgadmin@test.com")

        response = client.post(
            "/users",
            json={
                "email": "escalated@test.com",
                "password": "password123",
                "full_name": "Escalated",
                "role": UserRole.SYSTEM_ADMIN.value,
            },
            headers=auth(token),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "system_admin" in response.json()["detail"]

    def test_org_admin_cannot_promote_existing_user_to_system_admin(self, client, org_with_admin):
        token = token_for(client, "orgadmin@test.com")
        target = org_with_admin["fleet_manager"]

        response = client.patch(
            f"/users/{target.id}",
            json={"role": UserRole.SYSTEM_ADMIN.value},
            headers=auth(token),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_org_admin_cannot_modify_a_system_admin(self, client, org_with_admin):
        token = token_for(client, "orgadmin@test.com")
        target = org_with_admin["system_admin"]

        response = client.patch(
            f"/users/{target.id}",
            json={"full_name": "Downgraded"},
            headers=auth(token),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_org_admin_can_still_create_peers_and_below(self, client, org_with_admin):
        token = token_for(client, "orgadmin@test.com")

        for role in (UserRole.FLEET_MANAGER, UserRole.TECHNICIAN, UserRole.ORG_ADMIN):
            response = client.post(
                "/users",
                json={
                    "email": f"{role.value}-new@test.com",
                    "password": "password123",
                    "full_name": f"New {role.value}",
                    "role": role.value,
                },
                headers=auth(token),
            )
            assert response.status_code == status.HTTP_201_CREATED, response.text

    def test_system_admin_can_create_system_admin(self, client, org_with_admin):
        token = token_for(client, "sysadmin@test.com")

        response = client.post(
            "/users",
            json={
                "email": "second-sysadmin@test.com",
                "password": "password123",
                "full_name": "Second Sysadmin",
                "role": UserRole.SYSTEM_ADMIN.value,
            },
            headers=auth(token),
        )

        assert response.status_code == status.HTTP_201_CREATED

    def test_cannot_change_own_role(self, client, org_with_admin):
        token = token_for(client, "orgadmin@test.com")
        me = org_with_admin["org_admin"]

        response = client.patch(
            f"/users/{me.id}",
            json={"role": UserRole.FLEET_MANAGER.value},
            headers=auth(token),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestLegacyRoleHandling:
    def test_legacy_roles_resolve_to_canonical(self):
        assert canonical_role(UserRole.ADMIN) is UserRole.ORG_ADMIN
        assert canonical_role(UserRole.DISPATCHER) is UserRole.FLEET_MANAGER
        assert canonical_role(UserRole.PROGRAM_MANAGER) is UserRole.FLEET_MANAGER
        assert canonical_role(UserRole.VIEWER) is UserRole.READ_ONLY

    def test_every_role_resolves_to_a_permission_set(self):
        for role in UserRole:
            assert canonical_role(role) in ROLE_PERMISSIONS, f"{role} has no permissions"

    def test_legacy_admin_cannot_assign_system_admin(self):
        legacy_admin = User(role=UserRole.ADMIN)
        assert not can_assign_role(legacy_admin, UserRole.SYSTEM_ADMIN)
        assert can_assign_role(legacy_admin, UserRole.FLEET_MANAGER)

    def test_legacy_viewer_has_no_write_permissions(self):
        legacy_viewer = User(role=UserRole.VIEWER)
        assert not user_has_permission(legacy_viewer, "manage_assets")
        assert user_has_permission(legacy_viewer, "view_assets")


class TestSessionInvalidation:
    def test_password_change_invalidates_existing_token(self, client, org_with_admin):
        token = token_for(client, "orgadmin@test.com")
        assert client.get("/auth/me", headers=auth(token)).status_code == status.HTTP_200_OK

        changed = client.post(
            "/auth/change-password",
            json={"current_password": "password123", "new_password": "a-new-password"},
            headers=auth(token),
        )
        assert changed.status_code == status.HTTP_200_OK

        assert client.get("/auth/me", headers=auth(token)).status_code == status.HTTP_401_UNAUTHORIZED

    def test_admin_password_reset_invalidates_target_session(self, client, org_with_admin):
        victim_token = token_for(client, "fleet@test.com")
        admin_token = token_for(client, "orgadmin@test.com")
        target = org_with_admin["fleet_manager"]

        reset = client.patch(
            f"/users/{target.id}",
            json={"password": "rotated-password"},
            headers=auth(admin_token),
        )
        assert reset.status_code == status.HTTP_200_OK

        assert client.get("/auth/me", headers=auth(victim_token)).status_code == status.HTTP_401_UNAUTHORIZED

    def test_new_password_must_differ(self, client, org_with_admin):
        token = token_for(client, "orgadmin@test.com")

        response = client.post(
            "/auth/change-password",
            json={"current_password": "password123", "new_password": "password123"},
            headers=auth(token),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestCrossTenantAssetGrant:
    def test_cannot_grant_customer_access_to_another_orgs_asset(self, client, org_with_admin, db_session):
        customer = User(
            organization_id=org_with_admin["org"].id,
            email="customer@test.com",
            hashed_password=hash_password("password123"),
            full_name="Customer",
            role=UserRole.CUSTOMER,
            is_active=True,
        )
        db_session.add(customer)
        db_session.commit()

        token = token_for(client, "orgadmin@test.com")

        response = client.post(
            f"/users/{customer.id}/asset-authorizations",
            json={"asset_id": str(org_with_admin["other_asset"].id)},
            headers=auth(token),
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_malformed_asset_id_is_rejected_as_validation_error(self, client, org_with_admin, db_session):
        customer = User(
            organization_id=org_with_admin["org"].id,
            email="customer2@test.com",
            hashed_password=hash_password("password123"),
            full_name="Customer Two",
            role=UserRole.CUSTOMER,
            is_active=True,
        )
        db_session.add(customer)
        db_session.commit()

        token = token_for(client, "orgadmin@test.com")

        response = client.post(
            f"/users/{customer.id}/asset-authorizations",
            json={"asset_id": "not-a-uuid"},
            headers=auth(token),
        )

        # Previously raised KeyError/ValueError and surfaced as a 500.
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestSeedingSafety:
    def test_demo_seeding_is_off_by_default(self):
        assert Settings(_env_file=None, SKIP_SEED=False).should_seed_demo_data() is False

    def test_demo_seeding_rejected_in_production(self):
        with pytest.raises(ValueError, match="SEED_DEMO_DATA"):
            Settings(
                _env_file=None,
                ENVIRONMENT="production",
                SECRET_KEY="a-genuinely-long-production-secret-value",
                DATABASE_URL="postgresql+psycopg://user:pw@localhost:5432/fleet",
                SEED_DEMO_DATA=True,
            )

    def test_wildcard_cors_rejected_in_production(self):
        with pytest.raises(ValueError, match="CORS_ORIGINS"):
            Settings(
                _env_file=None,
                ENVIRONMENT="production",
                SECRET_KEY="a-genuinely-long-production-secret-value",
                DATABASE_URL="postgresql+psycopg://user:pw@localhost:5432/fleet",
                CORS_ORIGINS="*",
            )

    def test_development_defaults_remain_usable(self):
        settings = Settings(_env_file=None, SEED_DEMO_DATA=True, SKIP_SEED=False)
        assert settings.should_seed_demo_data() is True
