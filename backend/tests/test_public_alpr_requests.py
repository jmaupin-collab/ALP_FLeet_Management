"""Public ALPR request intake and the internal Request Center.

The security claim under test is narrow and load-bearing: an anonymous caller
can create an intake record and nothing else. No asset is assigned, no
deployment opens, no custody or status changes, and nothing moves on the map
until an authenticated Fleet Admin or Fleet Manager explicitly fulfils the
request through the existing lifecycle workflow.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.captcha import CaptchaVerifier, set_verifier
from app.config import get_settings
from app.database import get_db
from app.email import MemoryEmailSender, set_sender
from app.main import app
from app.models import (
    Agency,
    AlprRequest,
    AlprRequestStatus,
    Asset,
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    Deployment,
    Notification,
    Organization,
    User,
    UserRole,
    Warehouse,
)
from app.ratelimit import get_store


@pytest.fixture(autouse=True)
def isolated_side_effects(monkeypatch):
    """Every test starts with an empty outbox, no CAPTCHA, and a clean limiter."""
    outbox = MemoryEmailSender()
    set_sender(outbox)
    set_verifier(None)
    get_store().reset()
    # Pin the public tenant to the org these tests build. Left unset, intake
    # would resolve against whatever the developer happens to have in .env, and
    # the suite would pass or fail depending on the machine it runs on.
    monkeypatch.setattr(get_settings(), "public_request_org_slug", "fleet-co", raising=False)
    yield outbox
    set_sender(None)
    set_verifier(None)
    get_store().reset()


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
    organization = Organization(name="Fleet Co", slug="fleet-co", org_type="internal")
    db_session.add(organization)
    db_session.flush()

    warehouse = Warehouse(
        organization_id=organization.id,
        name="Phoenix Depot",
        latitude=Decimal("33.4484000"),
        longitude=Decimal("-112.0740000"),
    )
    agency = Agency(
        organization_id=organization.id,
        name="Houston PD",
        agency_type="Law Enforcement",
        latitude=Decimal("29.7604000"),
        longitude=Decimal("-95.3698000"),
    )
    db_session.add_all([warehouse, agency])

    people = {
        "manager": ("manager@fleet.co", UserRole.FLEET_MANAGER),
        "admin": ("admin@fleet.co", UserRole.ORG_ADMIN),
        "tech": ("tech@fleet.co", UserRole.TECHNICIAN),
        "customer": ("customer@fleet.co", UserRole.CUSTOMER),
    }
    users = {}
    for key, (email, role) in people.items():
        user = User(
            organization_id=organization.id,
            email=email,
            hashed_password=hash_password("password123"),
            full_name=key.title(),
            role=role,
            is_active=True,
        )
        db_session.add(user)
        users[key] = user
    db_session.flush()
    users["customer"].agency_id = agency.id

    trailer = Asset(
        organization_id=organization.id,
        vin="ALPRTRAILER0001",
        make_model="ALPR Trailer M1",
        asset_type=AssetType.ALPR_TRAILER,
        initial_purchase_cost=Decimal("40000"),
        current_location="Phoenix Depot",
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
        operational_status=AssetOperationalStatus.AVAILABLE,
        warehouse_id=warehouse.id,
    )
    truck = Asset(
        organization_id=organization.id,
        vin="SEMITRUCK000001",
        make_model="Peterbilt 579",
        asset_type=AssetType.SEMI_TRUCK,
        initial_purchase_cost=Decimal("120000"),
        current_location="Phoenix Depot",
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
        operational_status=AssetOperationalStatus.AVAILABLE,
        warehouse_id=warehouse.id,
    )
    db_session.add_all([trailer, truck])
    db_session.commit()
    return {
        "org": organization,
        "warehouse": warehouse,
        "agency": agency,
        "trailer": trailer,
        "truck": truck,
        "users": users,
    }


def login(client, email):
    token = client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def submission(**overrides):
    body = {
        "request_type": "deploy",
        "agency_name": "Houston PD",
        "requester_name": "Dana Ruiz",
        "requester_email": "dana@houstonpd.example",
        "requester_phone": "(555) 123-4567",
        "requested_date": str((datetime.now(UTC) + timedelta(days=14)).date()),
        "address": "1400 Lubbock St, Houston, TX 77002",
        "quantity": 2,
        "notes": "Gate code 4455.",
    }
    body.update(overrides)
    return body


def submit(client, **overrides):
    return client.post("/public/alpr-requests", json=submission(**overrides))


# --- Public submission ------------------------------------------------------


def test_anyone_can_submit_a_request_without_an_account(client, org, db_session):
    response = submit(client)

    assert response.status_code == status.HTTP_201_CREATED, response.text
    body = response.json()
    assert body["reference"].startswith("ALPR-")
    assert body["status"] == "New"

    stored = db_session.query(AlprRequest).one()
    assert stored.agency_name == "Houston PD"
    assert stored.quantity == 2
    assert stored.organization_id == org["org"].id


def test_the_reply_reveals_nothing_about_the_fleet(client, org):
    """A submitter gets a reference and a status. Never an id, asset, or count."""
    body = submit(client).json()

    assert set(body) == {"reference", "status", "message"}
    serialized = str(body).lower()
    assert "vin" not in serialized
    assert "alprtrailer0001" not in serialized
    assert "phoenix depot" not in serialized


def test_a_submission_creates_no_deployment(client, org, db_session):
    submit(client)

    assert db_session.query(Deployment).count() == 0


def test_a_submission_does_not_touch_any_asset(client, org, db_session):
    trailer = org["trailer"]
    before = (
        trailer.operational_status,
        trailer.current_custody_type,
        trailer.current_location,
        trailer.agency_id,
        trailer.warehouse_id,
    )

    submit(client)
    db_session.refresh(trailer)

    assert (
        trailer.operational_status,
        trailer.current_custody_type,
        trailer.current_location,
        trailer.agency_id,
        trailer.warehouse_id,
    ) == before


def test_a_submission_assigns_no_asset(client, org, db_session):
    submit(client)

    assert db_session.query(AlprRequest).one().assets == []


def test_an_invalid_submission_is_refused(client, org, db_session):
    too_many = submit(client, quantity=5000)
    assert too_many.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    bad_email = submit(client, requester_email="not-an-email")
    assert bad_email.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    no_address = submit(client, address="")
    assert no_address.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    bad_type = submit(client, request_type="steal")
    assert bad_type.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    assert db_session.query(AlprRequest).count() == 0


def test_a_submission_cannot_smuggle_extra_fields(client, org, db_session):
    """extra='forbid' is what stops a caller inventing status or asset_id."""
    response = client.post(
        "/public/alpr-requests",
        json=submission(status="Approved", asset_id="whatever", organization_id="x"),
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert db_session.query(AlprRequest).count() == 0


def test_whitespace_and_control_characters_are_normalised(client, org, db_session):
    submit(client, requester_name="  Dana\u0007   Ruiz  ", agency_name="Houston\tPD")

    stored = db_session.query(AlprRequest).one()
    assert stored.requester_name == "Dana Ruiz"
    assert stored.agency_name == "Houston PD"


def test_the_rate_limiter_refuses_a_flood(client, org, db_session, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "public_request_rate_limit", 3, raising=False)
    monkeypatch.setattr(settings, "public_request_rate_window_seconds", 3600, raising=False)

    accepted = [submit(client).status_code for _ in range(3)]
    blocked = submit(client)

    assert accepted == [status.HTTP_201_CREATED] * 3
    assert blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert blocked.headers["Retry-After"]
    # The refused attempt is not stored, so a flood cannot fill the table.
    assert db_session.query(AlprRequest).count() == 3


def test_a_failed_captcha_rejects_the_submission(client, org, db_session):
    class Refusing(CaptchaVerifier):
        enabled = True

        def verify(self, token, remote_ip=None):
            return False

    set_verifier(Refusing())

    response = submit(client, captcha_token="whatever")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert db_session.query(AlprRequest).count() == 0


def test_a_passing_captcha_is_recorded_on_the_request(client, org, db_session):
    class Accepting(CaptchaVerifier):
        enabled = True

        def verify(self, token, remote_ip=None):
            return token == "good-token"

    set_verifier(Accepting())

    response = submit(client, captcha_token="good-token")

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert db_session.query(AlprRequest).one().captcha_verified is True


def test_geocoding_failure_still_stores_the_request(client, org, db_session, monkeypatch):
    monkeypatch.setattr("app.geocoding.geocode_address", lambda address: None)
    # Both locator seams have to miss, or the city fallback answers instead.
    monkeypatch.setattr("app.geocoding.geocode_locality", lambda address: None)

    response = submit(client)

    assert response.status_code == status.HTTP_201_CREATED, response.text
    stored = db_session.query(AlprRequest).one()
    assert stored.address == "1400 Lubbock St, Houston, TX 77002"
    assert stored.latitude is None and stored.longitude is None


def test_a_successful_geocode_is_stored(client, org, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.geocoding.geocode_address",
        lambda address: (Decimal("29.7604000"), Decimal("-95.3698000")),
    )

    submit(client)

    stored = db_session.query(AlprRequest).one()
    assert stored.latitude == Decimal("29.7604000")


def test_audit_metadata_is_captured(client, org, db_session):
    submit(client)

    stored = db_session.query(AlprRequest).one()
    assert stored.source_ip
    assert stored.user_agent
    assert [event.event_type for event in stored.events] == ["submitted"]


def test_the_public_config_never_leaks_the_captcha_secret(client, org):
    response = client.get("/public/alpr-requests/config")

    assert response.status_code == status.HTTP_200_OK, response.text
    assert "secret" not in str(response.json()).lower()


# --- Notifications ----------------------------------------------------------


def test_managers_are_notified_about_a_new_request(client, org, db_session):
    submit(client)

    rows = db_session.query(Notification).all()
    recipients = {row.user_id for row in rows}
    assert org["users"]["manager"].id in recipients
    assert org["users"]["admin"].id in recipients
    # A technician and a customer are not fleet decision-makers.
    assert org["users"]["tech"].id not in recipients
    assert org["users"]["customer"].id not in recipients
    assert rows[0].link_path.startswith("/requests/")


def test_an_email_is_sent_to_managers(client, org, isolated_side_effects):
    submit(client)

    assert len(isolated_side_effects.outbox) == 1
    message = isolated_side_effects.outbox[0]
    assert set(message.to) == {"manager@fleet.co", "admin@fleet.co"}
    assert "Houston PD" in message.body
    # No VIN or internal id belongs in an outbound alert.
    assert "ALPRTRAILER0001" not in message.body


def test_an_email_failure_does_not_lose_the_request(client, org, db_session):
    class Exploding:
        name = "exploding"

        def send(self, message):
            raise RuntimeError("provider is down")

    set_sender(Exploding())

    response = submit(client)

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert db_session.query(AlprRequest).count() == 1
    # The in-app notification is the source of truth and still landed.
    assert db_session.query(Notification).count() > 0


def test_a_user_only_sees_their_own_notifications(client, org):
    submit(client)

    manager = client.get("/notifications", headers=login(client, "manager@fleet.co"))
    tech = client.get("/notifications", headers=login(client, "tech@fleet.co"))

    assert manager.json()["unread_count"] == 1
    assert tech.json()["unread_count"] == 0


def test_a_notification_can_be_marked_read(client, org):
    submit(client)
    headers = login(client, "manager@fleet.co")
    notification_id = client.get("/notifications", headers=headers).json()["items"][0]["id"]

    marked = client.post(f"/notifications/{notification_id}/read", headers=headers)

    assert marked.status_code == status.HTTP_200_OK, marked.text
    assert client.get("/notifications", headers=headers).json()["unread_count"] == 0


@pytest.mark.parametrize("decision", ["Approved", "Rejected", "Cancelled"])
def test_deciding_a_request_clears_it_from_every_bell(client, org, db_session, decision):
    """One manager acting settles it for the team, so nobody else has to dismiss it."""
    submit(client)
    request_id = db_session.query(AlprRequest).one().id
    assert db_session.query(Notification).count() == 2  # manager and admin

    response = client.post(
        f"/requests/{request_id}/status",
        headers=login(client, "manager@fleet.co"),
        json={"status": decision},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert db_session.query(Notification).count() == 0
    assert client.get("/notifications", headers=login(client, "admin@fleet.co")).json()[
        "unread_count"
    ] == 0


def test_picking_a_request_up_for_review_keeps_the_notification(client, org, db_session):
    """Under Review means someone is looking, not that a decision was made."""
    submit(client)
    request_id = db_session.query(AlprRequest).one().id

    client.post(
        f"/requests/{request_id}/status",
        headers=login(client, "manager@fleet.co"),
        json={"status": "Under Review"},
    )

    assert db_session.query(Notification).count() == 2


def test_deciding_one_request_leaves_other_notifications_alone(client, org, db_session):
    """Clearing is scoped to the request that was decided, not the whole feed."""
    submit(client)
    submit(client, agency_name="Dallas PD")
    decided, untouched = db_session.query(AlprRequest).order_by(AlprRequest.created_at).all()
    assert db_session.query(Notification).count() == 4

    client.post(
        f"/requests/{decided.id}/status",
        headers=login(client, "manager@fleet.co"),
        json={"status": "Approved"},
    )

    remaining = db_session.query(Notification).all()
    assert len(remaining) == 2
    assert {row.link_path for row in remaining} == {f"/requests/{untouched.id}"}


def test_another_users_notification_is_not_found(client, org):
    submit(client)
    manager_headers = login(client, "manager@fleet.co")
    notification_id = client.get("/notifications", headers=manager_headers).json()["items"][0]["id"]

    stolen = client.post(
        f"/notifications/{notification_id}/read", headers=login(client, "tech@fleet.co")
    )

    assert stolen.status_code == status.HTTP_404_NOT_FOUND


# --- Internal access control ------------------------------------------------


def test_the_request_center_rejects_anonymous_callers(client, org):
    assert client.get("/requests").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get("/requests/summary").status_code == status.HTTP_401_UNAUTHORIZED


def test_a_customer_cannot_reach_the_request_center(client, org):
    """The public feature grants a customer account no new visibility."""
    submit(client)

    response = client.get("/requests", headers=login(client, "customer@fleet.co"))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_a_technician_cannot_reach_the_request_center(client, org):
    response = client.get("/requests", headers=login(client, "tech@fleet.co"))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_a_fleet_manager_sees_the_request(client, org):
    submit(client)

    response = client.get("/requests", headers=login(client, "manager@fleet.co"))

    assert response.status_code == status.HTTP_200_OK, response.text
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["agency_name"] == "Houston PD"
    assert rows[0]["status"] == "New"


def test_a_request_from_another_organization_is_not_found(client, org, db_session):
    submit(client)
    request_id = db_session.query(AlprRequest).one().id

    other = Organization(name="Other Co", slug="other-co", org_type="internal")
    db_session.add(other)
    db_session.flush()
    db_session.add(
        User(
            organization_id=other.id,
            email="outsider@other.co",
            hashed_password=hash_password("password123"),
            full_name="Outsider",
            role=UserRole.ORG_ADMIN,
            is_active=True,
        )
    )
    db_session.commit()

    response = client.get(
        f"/requests/{request_id}", headers=login(client, "outsider@other.co")
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_a_customer_keeps_their_existing_agency_scoped_asset_access(client, org, db_session):
    """The public feature must not widen or narrow what a customer already sees."""
    submit(client)
    headers = login(client, "customer@fleet.co")

    assets = client.get("/assets", headers=headers)

    assert assets.status_code == status.HTTP_200_OK, assets.text
    # The customer's agency holds nothing, so they still see nothing.
    assert assets.json() == []


# --- Review workflow --------------------------------------------------------


@pytest.fixture
def new_request(client, org, db_session):
    submit(client)
    return db_session.query(AlprRequest).one()


def test_approving_deploys_nothing(client, org, new_request, db_session):
    headers = login(client, "manager@fleet.co")

    approved = client.post(
        f"/requests/{new_request.id}/status",
        headers=headers,
        json={"status": "Approved", "message": "Confirmed by phone."},
    )

    assert approved.status_code == status.HTTP_200_OK, approved.text
    assert approved.json()["status"] == "Approved"
    assert db_session.query(Deployment).count() == 0
    db_session.refresh(org["trailer"])
    assert org["trailer"].operational_status == AssetOperationalStatus.AVAILABLE
    assert org["trailer"].current_custody_type == CustodyType.WAREHOUSE_DEPOT


def test_an_illegal_status_jump_is_refused(client, org, new_request):
    headers = login(client, "manager@fleet.co")

    response = client.post(
        f"/requests/{new_request.id}/status", headers=headers, json={"status": "Completed"}
    )

    assert response.status_code == status.HTTP_409_CONFLICT


def test_a_rejected_request_is_terminal(client, org, new_request):
    headers = login(client, "manager@fleet.co")
    client.post(f"/requests/{new_request.id}/status", headers=headers, json={"status": "Rejected"})

    revived = client.post(
        f"/requests/{new_request.id}/status", headers=headers, json={"status": "Approved"}
    )

    assert revived.status_code == status.HTTP_409_CONFLICT


def test_staff_can_correct_operational_details(client, org, new_request, db_session):
    headers = login(client, "manager@fleet.co")

    response = client.patch(
        f"/requests/{new_request.id}",
        headers=headers,
        json={"quantity": 1, "agency_id": str(org["agency"].id), "review_notes": "One is enough."},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["quantity"] == 1
    assert body["agency_id"] == str(org["agency"].id)
    assert any(event["event_type"] == "edited" for event in body["events"])


def test_the_history_records_every_step(client, org, new_request):
    headers = login(client, "manager@fleet.co")
    client.post(f"/requests/{new_request.id}/status", headers=headers, json={"status": "Under Review"})
    client.post(f"/requests/{new_request.id}/status", headers=headers, json={"status": "Approved"})

    body = client.get(f"/requests/{new_request.id}", headers=headers).json()

    assert [event["event_type"] for event in body["events"]] == [
        "submitted",
        "status_changed",
        "status_changed",
    ]
    assert body["events"][-1]["actor_name"] == "Manager"


# --- Asset selection and fulfilment -----------------------------------------


def approve(client, request_id, headers):
    return client.post(f"/requests/{request_id}/status", headers=headers, json={"status": "Approved"})


def test_trailers_cannot_be_assigned_before_approval(client, org, new_request):
    headers = login(client, "manager@fleet.co")

    response = client.post(
        f"/requests/{new_request.id}/assets",
        headers=headers,
        json={"asset_ids": [str(org["trailer"].id)]},
    )

    assert response.status_code == status.HTTP_409_CONFLICT


def test_selecting_a_trailer_does_not_deploy_it(client, org, new_request, db_session):
    headers = login(client, "manager@fleet.co")
    approve(client, new_request.id, headers)

    response = client.post(
        f"/requests/{new_request.id}/assets",
        headers=headers,
        json={"asset_ids": [str(org["trailer"].id)]},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert len(response.json()["assets"]) == 1
    assert response.json()["assets"][0]["fulfilled_at"] is None
    # Selection is intent, not movement.
    assert db_session.query(Deployment).count() == 0
    db_session.refresh(org["trailer"])
    assert org["trailer"].operational_status == AssetOperationalStatus.AVAILABLE


def test_only_alpr_trailers_can_be_selected(client, org, new_request):
    headers = login(client, "manager@fleet.co")
    approve(client, new_request.id, headers)

    response = client.post(
        f"/requests/{new_request.id}/assets",
        headers=headers,
        json={"asset_ids": [str(org["truck"].id)]},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_an_asset_from_another_organization_cannot_be_selected(client, org, new_request, db_session):
    other = Organization(name="Other Co", slug="other-co", org_type="internal")
    db_session.add(other)
    db_session.flush()
    outsider_asset = Asset(
        organization_id=other.id,
        vin="OUTSIDER0000001",
        make_model="ALPR Trailer",
        asset_type=AssetType.ALPR_TRAILER,
        initial_purchase_cost=Decimal("40000"),
        current_location="Elsewhere",
        operational_status=AssetOperationalStatus.AVAILABLE,
    )
    db_session.add(outsider_asset)
    db_session.commit()

    headers = login(client, "manager@fleet.co")
    approve(client, new_request.id, headers)
    response = client.post(
        f"/requests/{new_request.id}/assets",
        headers=headers,
        json={"asset_ids": [str(outsider_asset.id)]},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_fulfilling_a_deploy_request_uses_the_deployment_workflow(
    client, org, new_request, db_session
):
    """The trailer must end up exactly where start_deployment would put it."""
    headers = login(client, "manager@fleet.co")
    approve(client, new_request.id, headers)
    client.patch(
        f"/requests/{new_request.id}", headers=headers, json={"agency_id": str(org["agency"].id)}
    )
    client.post(
        f"/requests/{new_request.id}/assets",
        headers=headers,
        json={"asset_ids": [str(org["trailer"].id)]},
    )

    response = client.post(f"/requests/{new_request.id}/fulfill", headers=headers, json={})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["status"] == "In Progress"
    assert response.json()["assets"][0]["fulfilled_at"] is not None

    db_session.expire_all()
    trailer = db_session.get(Asset, org["trailer"].id)
    assert trailer.operational_status == AssetOperationalStatus.DEPLOYED
    assert trailer.current_custody_type == CustodyType.CUSTOMER_AGENCY
    assert trailer.agency_id == org["agency"].id
    # Exactly one open deployment, created by the ordinary service.
    open_rows = db_session.query(Deployment).filter(Deployment.ended_at.is_(None)).all()
    assert len(open_rows) == 1
    assert open_rows[0].asset_id == trailer.id


def test_fulfilling_in_transit_needs_a_carrier(client, org, new_request, db_session):
    headers = login(client, "manager@fleet.co")
    approve(client, new_request.id, headers)
    client.patch(
        f"/requests/{new_request.id}", headers=headers, json={"agency_id": str(org["agency"].id)}
    )
    client.post(
        f"/requests/{new_request.id}/assets",
        headers=headers,
        json={"asset_ids": [str(org["trailer"].id)]},
    )

    response = client.post(
        f"/requests/{new_request.id}/fulfill", headers=headers, json={"in_transit": True}
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # Nothing moved, so a retry starts from a clean state.
    assert db_session.query(Deployment).count() == 0


def test_fulfilling_without_a_trailer_is_refused(client, org, new_request, db_session):
    headers = login(client, "manager@fleet.co")
    approve(client, new_request.id, headers)

    response = client.post(f"/requests/{new_request.id}/fulfill", headers=headers, json={})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert db_session.query(Deployment).count() == 0


def test_fulfilling_before_approval_is_refused(client, org, new_request, db_session):
    headers = login(client, "manager@fleet.co")

    response = client.post(f"/requests/{new_request.id}/fulfill", headers=headers, json={})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert db_session.query(Deployment).count() == 0


def test_a_pickup_request_uses_the_existing_return_workflow(client, org, db_session):
    """A pickup ends the open deployment and brings the unit back to a depot."""
    headers = login(client, "manager@fleet.co")
    trailer = org["trailer"]

    # Put the trailer out with an agency first, through the ordinary endpoint.
    deployed = client.post(
        f"/assets/{trailer.id}/deployments",
        headers=headers,
        json={"custody_type": "Customer / LE Agency", "agency_id": str(org["agency"].id)},
    )
    assert deployed.status_code == status.HTTP_200_OK, deployed.text

    submit(client, request_type="pickup")
    pickup = db_session.query(AlprRequest).one()
    approve(client, pickup.id, headers)
    client.post(
        f"/requests/{pickup.id}/assets", headers=headers, json={"asset_ids": [str(trailer.id)]}
    )

    response = client.post(
        f"/requests/{pickup.id}/fulfill",
        headers=headers,
        json={"warehouse_id": str(org["warehouse"].id)},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    db_session.expire_all()
    returned = db_session.get(Asset, trailer.id)
    assert returned.current_custody_type == CustodyType.WAREHOUSE_DEPOT
    assert returned.warehouse_id == org["warehouse"].id
    assert returned.agency_id is None
    assert db_session.query(Deployment).filter(Deployment.ended_at.is_(None)).count() == 0


def test_a_customer_cannot_fulfil_a_request(client, org, new_request):
    response = client.post(
        f"/requests/{new_request.id}/fulfill",
        headers=login(client, "customer@fleet.co"),
        json={},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_scheduling_requires_approval_first(client, org, new_request):
    headers = login(client, "manager@fleet.co")

    response = client.post(
        f"/requests/{new_request.id}/schedule",
        headers=headers,
        json={"scheduled_date": str((datetime.now(UTC) + timedelta(days=3)).date())},
    )

    assert response.status_code == status.HTTP_409_CONFLICT


def test_scheduling_an_approved_request_records_the_date(client, org, new_request):
    headers = login(client, "manager@fleet.co")
    approve(client, new_request.id, headers)
    when = str((datetime.now(UTC) + timedelta(days=3)).date())

    response = client.post(
        f"/requests/{new_request.id}/schedule", headers=headers, json={"scheduled_date": when}
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["status"] == "Scheduled"
    assert response.json()["scheduled_date"] == when


def test_the_summary_counts_by_status(client, org, db_session):
    submit(client)
    submit(client, request_type="pickup")
    headers = login(client, "manager@fleet.co")

    response = client.get("/requests/summary", headers=headers)

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["total"] == 2
    assert response.json()["counts"]["New"] == 2
