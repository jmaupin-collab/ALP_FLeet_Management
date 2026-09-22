"""Phase 5 work-order workflow: auto-create, uniqueness, history, analytics."""

from __future__ import annotations

import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent / "_phase5_test.db"
PHASE5_DB_URL = f"sqlite:///{TEST_DB}"
os.environ["DATABASE_URL"] = PHASE5_DB_URL
os.environ["SECRET_KEY"] = "phase5-test-secret-key"
os.environ["SKIP_SEED"] = "1"
os.environ["ENVIRONMENT"] = "development"

if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import SessionLocal, dispose_engine, ensure_schema, get_db, rebind_engine  # noqa: E402
from app.models import Organization, User, UserRole  # noqa: E402

get_settings.cache_clear()
rebind_engine(PHASE5_DB_URL)
ensure_schema()

from app.main import app  # noqa: E402

client: TestClient | None = None


def setup_module() -> None:
    get_settings.cache_clear()
    rebind_engine(PHASE5_DB_URL)
    ensure_schema()
    db = SessionLocal()
    try:
        org = db.scalar(select(Organization).limit(1))
        if org is None:
            org = Organization(name="Phase 5 Test Org", slug="phase5-test", org_type="internal")
            db.add(org)
            db.flush()
        if db.scalar(select(User).where(User.email == "admin@example.com")) is None:
            db.add(
                User(
                    organization_id=org.id,
                    email="admin@example.com",
                    hashed_password=hash_password("ChangeMe123!"),
                    full_name="Test Admin",
                    role=UserRole.ADMIN,
                    is_active=True,
                )
            )
        db.commit()
    finally:
        db.close()

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    global client
    client = TestClient(app)


def teardown_module() -> None:
    global client
    if client is not None:
        client.close()
    app.dependency_overrides.clear()
    dispose_engine()
    TEST_DB.unlink(missing_ok=True)


def login() -> dict:
    response = client.post("/auth/login", json={"email": "admin@example.com", "password": "ChangeMe123!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _asset(headers: dict, vin: str = "1PHASE5VIN000001") -> dict:
    created = client.post(
        "/assets",
        headers=headers,
        json={
            "vin": vin,
            "make_model": "Phase 5 Trailer",
            "initial_purchase_cost": 22000,
            "current_location": "Mesa Yard",
            "asset_type": "ALPR Trailer",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_fail_item_creates_open_work_order_once() -> None:
    headers = login()
    asset = _asset(headers)
    inspection = client.post(f"/assets/{asset['id']}/inspections", headers=headers).json()
    item = inspection["items"][0]

    first = client.patch(
        f"/inspections/{inspection['id']}/items/{item['id']}",
        headers=headers,
        json={"result": "fail", "notes": "camera housing cracked"},
    )
    assert first.status_code == 200, first.text
    marked = next(row for row in first.json()["items"] if row["id"] == item["id"])
    wo_id = marked["generated_work_order_id"]
    assert wo_id

    listed = client.get("/work-orders", headers=headers).json()
    created = next(row for row in listed if row["id"] == wo_id)
    me = client.get("/auth/me", headers=headers).json()
    assert created["status"] == "open"
    assert created["asset_id"] == asset["id"]
    assert created["failed_component"] == item["component"]
    assert created["inspection_id"] == inspection["id"]
    assert created["inspection_item_id"] == item["id"]
    assert "camera housing cracked" in (created["description"] or "")
    assert created["issue_source"] == "Inspection"
    assert created["inspector"] == me["full_name"]

    duplicate = client.patch(
        f"/inspections/{inspection['id']}/items/{item['id']}",
        headers=headers,
        json={"result": "fail", "notes": "camera housing cracked again"},
    )
    assert duplicate.status_code == 200
    rematch = next(row for row in duplicate.json()["items"] if row["id"] == item["id"])
    assert rematch["generated_work_order_id"] == wo_id
    after = client.get("/work-orders", headers=headers).json()
    assert len([row for row in after if row["inspection_item_id"] == item["id"]]) == 1


def test_work_order_workflow_history_asset_and_analytics() -> None:
    headers = login()
    asset = _asset(headers, vin="1PHASE5VIN000002")
    inspection = client.post(f"/assets/{asset['id']}/inspections", headers=headers).json()
    item = inspection["items"][0]
    failed = client.patch(
        f"/inspections/{inspection['id']}/items/{item['id']}",
        headers=headers,
        json={"result": "fail", "notes": "radar offline"},
    )
    wo_id = next(row for row in failed.json()["items"] if row["id"] == item["id"])["generated_work_order_id"]

    investigating = client.patch(f"/work-orders/{wo_id}", headers=headers, json={"status": "investigating"})
    assert investigating.status_code == 200
    assert investigating.json()["status"] == "investigating"

    in_progress = client.patch(
        f"/work-orders/{wo_id}",
        headers=headers,
        json={
            "status": "in_progress",
            "repair_actions": "Replaced radar harness",
            "parts_used": "Harness kit",
            "labor_hours": "2.5",
            "labor_cost": "180.00",
            "parts_cost": "95.00",
            "downtime_hours": "6",
            "repair_channel": "Internal",
        },
    )
    assert in_progress.status_code == 200
    assert in_progress.json()["status"] == "in_progress"

    complete = client.patch(
        f"/work-orders/{wo_id}",
        headers=headers,
        json={
            "status": "completed",
            "root_cause_category": "Electrical Failure",
            "root_cause_description": "Corroded connector",
            "corrective_action": "Replaced harness and sealed connector",
            "preventive_action": "Inspect connectors at each PM",
            "completion_notes": "Returned to service",
        },
    )
    assert complete.status_code == 200, complete.text
    body = complete.json()
    assert body["status"] == "completed"
    assert body["root_cause_category"] == "Electrical Failure"
    assert float(body["total_repair_cost"]) == 275.0

    detail = client.get(f"/work-orders/{wo_id}", headers=headers)
    assert detail.status_code == 200
    types = [event["event_type"] for event in detail.json()["events"]]
    assert "work_order_created" in types
    assert "status_changed" in types
    assert "root_cause_entered" in types
    assert "work_order_completed" in types
    assert any(event["previous_value"] == "open" and event["new_value"] == "investigating" for event in detail.json()["events"])

    historical = client.get(f"/work-orders?asset_id={asset['id']}", headers=headers).json()
    assert any(row["id"] == wo_id and row["status"] == "completed" for row in historical)

    deny = client.delete(f"/work-orders/{wo_id}", headers=headers)
    assert deny.status_code == 409

    analytics = client.get("/analytics", headers=headers)
    assert analytics.status_code == 200
    hub = analytics.json()
    assert hub["completed_work_orders"] >= 1
    assert any(row["category"] == "Electrical Failure" for row in hub["root_causes"])
    assert float(hub["avg_repair_cost"]) > 0

    filtered = client.get(
        f"/work-orders?status=completed&asset_type=ALPR Trailer&priority=high&component={item['component']}",
        headers=headers,
    )
    assert filtered.status_code == 200
    assert any(row["id"] == wo_id for row in filtered.json())
