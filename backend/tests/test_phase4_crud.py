"""CRUD and historical-integrity tests for Phase 4. Isolated SQLite file, no seed dump."""

from __future__ import annotations

import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent / "_phase4_test.db"
PHASE4_DB_URL = f"sqlite:///{TEST_DB}"
os.environ["DATABASE_URL"] = PHASE4_DB_URL
os.environ["SECRET_KEY"] = "phase4-test-secret-key"
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
rebind_engine(PHASE4_DB_URL)
ensure_schema()

from app.main import app  # noqa: E402

client: TestClient | None = None


def setup_module() -> None:
    get_settings.cache_clear()
    rebind_engine(PHASE4_DB_URL)
    ensure_schema()
    db = SessionLocal()
    try:
        org = db.scalar(select(Organization).limit(1))
        if org is None:
            org = Organization(name="Phase 4 Test Org", slug="phase4-test", org_type="internal")
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
        if db.scalar(select(User).where(User.email == "viewer@example.com")) is None:
            db.add(
                User(
                    organization_id=org.id,
                    email="viewer@example.com",
                    hashed_password=hash_password("ChangeMe123!"),
                    full_name="Test Viewer",
                    role=UserRole.VIEWER,
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


def login(email: str = "admin@example.com") -> dict:
    response = client.post("/auth/login", json={"email": email, "password": "ChangeMe123!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_and_auth_required() -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/assets").status_code == 401


def test_viewer_cannot_mutate() -> None:
    headers = login("viewer@example.com")
    response = client.post(
        "/assets",
        headers=headers,
        json={
            "vin": "1TESTVIN00000001",
            "make_model": "Viewer Blocked",
            "initial_purchase_cost": 1000,
            "current_location": "Mesa Yard",
            "asset_type": "Fleet Vehicle",
        },
    )
    assert response.status_code == 403


def test_asset_crud_timeline_and_restricted_delete() -> None:
    headers = login()
    created = client.post(
        "/assets",
        headers=headers,
        json={
            "vin": "1TESTVIN00000002",
            "make_model": "Test Trailer Alpha",
            "initial_purchase_cost": 50000,
            "current_location": "Mesa Yard A — Warehouse Depot",
            "asset_type": "ALPR Trailer",
            "notes": "Intake record",
        },
    )
    assert created.status_code == 201, created.text
    asset_id = created.json()["id"]

    listed = client.get("/assets", headers=headers)
    assert listed.status_code == 200
    assert any(row["id"] == asset_id for row in listed.json())

    patched = client.patch(
        f"/assets/{asset_id}",
        headers=headers,
        json={"make_model": "Test Trailer Alpha Mk2"},
    )
    assert patched.status_code == 200
    assert patched.json()["make_model"] == "Test Trailer Alpha Mk2"

    timeline = client.get(f"/assets/{asset_id}/timeline", headers=headers).json()
    assert timeline["asset"]["current_location"]

    moved = client.post(
        f"/assets/{asset_id}/custody",
        headers=headers,
        json={
            "custody_type": "Customer / LE Agency",
            "location": "Phoenix PD — test site",
            "notes": "Field assignment",
        },
    )
    assert moved.status_code == 200
    timeline2 = client.get(f"/assets/{asset_id}/timeline", headers=headers).json()
    dep_events = [event for event in timeline2["events"] if event["event_type"] == "deployment"]
    assert len(dep_events) >= 1
    assert any(event["ended_at"] is None for event in dep_events)

    started = client.post(
        f"/assets/{asset_id}/deployments",
        headers=headers,
        json={"location": "AZ DPS — SR-51", "custody_type": "Customer / LE Agency", "notes": "Start"},
    )
    assert started.status_code == 200
    ended = client.post(
        f"/assets/{asset_id}/deployments/end",
        headers=headers,
        json={"location": "Mesa Yard A — Warehouse Depot", "notes": "Returned"},
    )
    assert ended.status_code == 200
    assert ended.json()["operational_status"] == "available"
    assert ended.json()["current_status"] is None
    listed_after_return = client.get("/deployments", headers=headers).json()
    assert all(row["asset_id"] != asset_id for row in listed_after_return)

    # Test archive/restore functionality before deletion
    archived = client.post(f"/assets/{asset_id}/archive", headers=headers)
    assert archived.status_code == 200
    assert archived.json()["is_archived"] is True
    hidden = client.get("/assets", headers=headers).json()
    assert all(row["id"] != asset_id for row in hidden)
    restored = client.post(f"/assets/{asset_id}/restore", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["is_archived"] is False

    # Admins can now force-delete assets with operational history
    deleted = client.delete(f"/assets/{asset_id}", headers=headers)
    assert deleted.status_code == 204

    disposable = client.post(
        "/assets",
        headers=headers,
        json={
            "vin": "1TESTVIN00000099",
            "make_model": "Accidental Duplicate",
            "initial_purchase_cost": 1000,
            "current_location": "Yard",
            "asset_type": "Fleet Vehicle",
        },
    )
    assert disposable.status_code == 201
    assert client.delete(f"/assets/{disposable.json()['id']}", headers=headers).status_code == 204


def test_inspection_fail_opens_work_order_and_cancel() -> None:
    headers = login()
    asset = client.post(
        "/assets",
        headers=headers,
        json={
            "vin": "1TESTVIN00000003",
            "make_model": "Inspect Me Trailer",
            "initial_purchase_cost": 12000,
            "current_location": "Mesa Yard",
            "asset_type": "ALPR Trailer",
        },
    ).json()
    inspection = client.post(f"/assets/{asset['id']}/inspections", headers=headers).json()
    fail_item = inspection["items"][0]
    updated = client.patch(
        f"/inspections/{inspection['id']}/items/{fail_item['id']}",
        headers=headers,
        json={"result": "fail", "notes": "cracked"},
    )
    assert updated.status_code == 200
    marked = next(item for item in updated.json()["items"] if item["id"] == fail_item["id"])
    assert marked["generated_work_order_id"]

    listed = client.get("/work-orders", headers=headers).json()
    assert any(row["id"] == marked["generated_work_order_id"] for row in listed)

    asset2 = client.post(
        "/assets",
        headers=headers,
        json={
            "vin": "1TESTVIN00000004",
            "make_model": "Cancel Inspect",
            "initial_purchase_cost": 9000,
            "current_location": "Yard",
            "asset_type": "Fleet Vehicle",
        },
    ).json()
    insp2 = client.post(f"/assets/{asset2['id']}/inspections", headers=headers).json()
    cancelled = client.post(f"/inspections/{insp2['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_work_order_update_close_cost_and_safe_delete() -> None:
    headers = login()
    asset = client.post(
        "/assets",
        headers=headers,
        json={
            "vin": "1TESTVIN00000005",
            "make_model": "WO Truck",
            "initial_purchase_cost": 80000,
            "current_location": "Tempe Shop",
            "asset_type": "Semi Truck",
        },
    ).json()
    wo = client.post(
        "/work-orders",
        headers=headers,
        json={"asset_id": asset["id"], "title": "Test ticket", "description": "empty ticket"},
    )
    assert wo.status_code == 201, wo.text
    wo_id = wo.json()["id"]

    costed = client.post(
        f"/work-orders/{wo_id}/costs",
        headers=headers,
        json={"labor_cost": "125.50", "parts_cost": "40.00", "notes": "shop supplies"},
    )
    assert costed.status_code == 200
    assert float(costed.json()["labor_cost"]) == 125.5

    closed = client.post(f"/work-orders/{wo_id}/close", headers=headers)
    assert closed.status_code == 200
    assert closed.json()["status"] == "completed"
    assert closed.json()["closed_at"]

    deny = client.delete(f"/work-orders/{wo_id}", headers=headers)
    assert deny.status_code == 409

    empty = client.post(
        "/work-orders",
        headers=headers,
        json={"asset_id": asset["id"], "title": "Accidental ticket"},
    ).json()
    removed = client.delete(f"/work-orders/{empty['id']}", headers=headers)
    assert removed.status_code == 204


def test_directories_and_kpis_from_database() -> None:
    headers = login()
    warehouse = client.post("/warehouses", headers=headers, json={"name": "Goodyear Overflow", "address": "Goodyear, AZ"})
    assert warehouse.status_code == 201, warehouse.text
    agency = client.post(
        "/agencies",
        headers=headers,
        json={"name": "Test PD", "agency_type": "Law Enforcement", "contact_name": "Sgt. Ruiz"},
    )
    assert agency.status_code == 201
    vendor = client.post("/vendors", headers=headers, json={"name": "Acme 3PL", "specialty": "Towing"})
    assert vendor.status_code == 201

    asset = client.post(
        "/assets",
        headers=headers,
        json={
            "vin": "1TESTVIN00000006",
            "make_model": "Directory Linked",
            "initial_purchase_cost": 15000,
            "current_location": "Goodyear Overflow",
            "asset_type": "Fleet Vehicle",
            "warehouse_id": warehouse.json()["id"],
        },
    )
    assert asset.status_code == 201

    archived_wh = client.delete(f"/warehouses/{warehouse.json()['id']}", headers=headers)
    assert archived_wh.status_code == 200
    assert archived_wh.json()["action"] == "archived"

    kpis = client.get("/dashboard/kpis", headers=headers)
    assert kpis.status_code == 200
    assert kpis.json()["fleet_size"] >= 1

    analytics = client.get("/analytics", headers=headers)
    assert analytics.status_code == 200
    assert "failures" in analytics.json()
