"""Documents, utilization, parts inventory, attention, and replacement-warning tests."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import (
    Agency,
    Asset,
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    Deployment,
    DeploymentStatus,
    MaintenanceWorkOrder,
    Organization,
    User,
    UserRole,
    Warehouse,
    WorkOrderStatus,
)
from app.utilization import compute_asset_utilization


@pytest.fixture
def client(db_session):
    def _override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def org_users(db_session):
    org = Organization(name="Ops Org", slug="ops-org", org_type="internal")
    other = Organization(name="Other Org", slug="other-org", org_type="internal")
    db_session.add_all([org, other])
    db_session.flush()

    def make_user(email, role, organization_id):
        user = User(
            organization_id=organization_id,
            email=email,
            hashed_password=hash_password("password123"),
            full_name=email,
            role=role,
            is_active=True,
        )
        db_session.add(user)
        return user

    admin = make_user("ops.admin@test.com", UserRole.ORG_ADMIN, org.id)
    manager = make_user("ops.manager@test.com", UserRole.FLEET_MANAGER, org.id)
    tech = make_user("ops.tech@test.com", UserRole.TECHNICIAN, org.id)
    customer = make_user("ops.customer@test.com", UserRole.CUSTOMER, org.id)
    other_admin = make_user("other.admin@test.com", UserRole.ORG_ADMIN, other.id)
    db_session.flush()

    asset = Asset(
        organization_id=org.id,
        vin="OPSASSET0001",
        make_model="Ops Trailer",
        initial_purchase_cost=Decimal("1000"),
        current_location="Yard",
        asset_type=AssetType.ALPR_TRAILER,
        operational_status=AssetOperationalStatus.AVAILABLE,
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
    )
    other_asset = Asset(
        organization_id=other.id,
        vin="OTHERASSET001",
        make_model="Other Trailer",
        initial_purchase_cost=Decimal("5000"),
        current_location="Elsewhere",
        asset_type=AssetType.ALPR_TRAILER,
        operational_status=AssetOperationalStatus.AVAILABLE,
    )
    db_session.add_all([asset, other_asset])
    db_session.flush()
    return {
        "org": org,
        "other": other,
        "admin": admin,
        "manager": manager,
        "tech": tech,
        "customer": customer,
        "other_admin": other_admin,
        "asset": asset,
        "other_asset": other_asset,
    }


def login(client, email):
    response = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_document_management_and_denials(client, org_users, tmp_path, monkeypatch):
    from app import storage as storage_mod

    monkeypatch.setattr(storage_mod.storage, "root", tmp_path)

    manager = login(client, "ops.manager@test.com")
    tech = login(client, "ops.tech@test.com")
    customer = login(client, "ops.customer@test.com")
    other = login(client, "other.admin@test.com")
    asset_id = str(org_users["asset"].id)
    other_id = str(org_users["other_asset"].id)

    upload = client.post(
        f"/assets/{asset_id}/documents",
        headers=manager,
        data={
            "document_type": "registration",
            "title": "2026 Registration",
            "issue_date": "2026-01-01",
            "expiration_date": str(date.today() - timedelta(days=1)),
            "notes": "cab card",
        },
        files={"file": ("reg.pdf", BytesIO(b"%PDF-1.4 test"), "application/pdf")},
    )
    assert upload.status_code == status.HTTP_201_CREATED, upload.text
    doc_id = upload.json()["id"]
    assert upload.json()["expiration_state"] == "expired"

    listed = client.get(f"/assets/{asset_id}/documents", headers=manager)
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "2026 Registration"

    downloaded = client.get(f"/documents/{doc_id}/file", headers=manager)
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF")

    assert client.get(f"/assets/{asset_id}/documents", headers=tech).status_code == 403
    assert client.get(f"/assets/{asset_id}/documents", headers=customer).status_code == 403
    assert client.get(f"/documents/{doc_id}", headers=tech).status_code == 403
    assert client.get(f"/documents/{doc_id}/file", headers=customer).status_code == 403
    assert client.delete(f"/documents/{doc_id}", headers=tech).status_code == 403
    assert client.get(f"/assets/{other_id}/documents", headers=manager).status_code == 404
    assert client.get(f"/documents/{doc_id}", headers=other).status_code == 404

    deleted = client.delete(f"/documents/{doc_id}", headers=manager)
    assert deleted.status_code == 204
    assert client.get(f"/documents/{doc_id}", headers=manager).status_code == 404


def test_utilization_from_deployment_history(db_session, org_users):
    now = datetime.now(UTC)
    asset = org_users["asset"]
    db_session.add(
        Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location="Site",
            status=DeploymentStatus.DEPLOYED,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            started_at=now - timedelta(days=20),
            ended_at=now - timedelta(days=10),
        )
    )
    db_session.add(
        MaintenanceWorkOrder(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            title="Brake job",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(days=8),
            closed_at=now - timedelta(days=6),
            downtime_start=now - timedelta(days=8),
            downtime_end=now - timedelta(days=6),
            downtime_hours=Decimal("48"),
            labor_cost=Decimal("0"),
            parts_cost=Decimal("0"),
        )
    )
    db_session.flush()
    result = compute_asset_utilization(asset, now)
    assert result.windows[30].deployed_days >= 9
    assert result.windows[30].maintenance_days >= 1.5
    assert result.windows[30].utilization_pct > 0


def test_high_downtime_ignores_work_order_calendar_without_hours(db_session, org_users):
    now = datetime.now(UTC)
    asset = org_users["asset"]
    asset.created_at = now - timedelta(days=90)
    asset.operational_status = AssetOperationalStatus.AVAILABLE
    db_session.add(
        MaintenanceWorkOrder(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            title="Paper ticket",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(days=40),
            closed_at=now - timedelta(days=2),
            downtime_start=now - timedelta(days=40),
            downtime_end=now - timedelta(days=2),
            downtime_hours=Decimal("0"),
            labor_cost=Decimal("0"),
            parts_cost=Decimal("0"),
        )
    )
    db_session.flush()
    db_session.refresh(asset)
    result = compute_asset_utilization(asset, now)
    assert result.windows[90].maintenance_days < 1
    assert "High Downtime" not in result.flags


def test_high_downtime_uses_recorded_hours_not_open_span(db_session, org_users):
    now = datetime.now(UTC)
    asset = org_users["asset"]
    asset.created_at = now - timedelta(days=90)
    asset.operational_status = AssetOperationalStatus.AVAILABLE
    db_session.add(
        MaintenanceWorkOrder(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            title="Short repair",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(days=20),
            closed_at=now - timedelta(days=14),
            downtime_start=now - timedelta(days=20),
            downtime_end=now - timedelta(days=14),
            downtime_hours=Decimal("4"),
            labor_cost=Decimal("0"),
            parts_cost=Decimal("0"),
        )
    )
    db_session.flush()
    db_session.refresh(asset)
    result = compute_asset_utilization(asset, now)
    assert result.windows[90].maintenance_days == pytest.approx(4 / 24, abs=0.05)
    assert "High Downtime" not in result.flags


def _available_asset_with_history(db_session, asset, now, days=90):
    asset.created_at = now - timedelta(days=days)
    asset.operational_status = AssetOperationalStatus.AVAILABLE
    return asset


def test_high_downtime_tiny_sample_does_not_flag(db_session, org_users):
    now = datetime.now(UTC)
    asset = _available_asset_with_history(db_session, org_users["asset"], now)
    db_session.add(
        MaintenanceWorkOrder(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            title="Hours only",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(hours=6),
            closed_at=now - timedelta(hours=4),
            downtime_hours=Decimal("2"),
            labor_cost=Decimal("0"),
            parts_cost=Decimal("0"),
        )
    )
    db_session.flush()
    db_session.refresh(asset)
    assert "High Downtime" not in compute_asset_utilization(asset, now).flags


def test_high_downtime_two_days_of_eight_does_not_flag(db_session, org_users):
    now = datetime.now(UTC)
    asset = _available_asset_with_history(db_session, org_users["asset"], now, days=8)
    db_session.add(
        MaintenanceWorkOrder(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            title="Two days",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(days=2),
            closed_at=now,
            downtime_hours=Decimal("48"),
            labor_cost=Decimal("0"),
            parts_cost=Decimal("0"),
        )
    )
    db_session.flush()
    db_session.refresh(asset)
    result = compute_asset_utilization(asset, now)
    assert result.windows[90].maintenance_days == pytest.approx(2, abs=0.05)
    assert result.windows[90].eligible_days >= 7
    assert "High Downtime" not in result.flags


def test_high_downtime_four_days_and_over_20_percent_flags(db_session, org_users):
    now = datetime.now(UTC)
    asset = _available_asset_with_history(db_session, org_users["asset"], now, days=10)
    db_session.add(
        MaintenanceWorkOrder(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            title="Four days",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(days=4),
            closed_at=now,
            downtime_hours=Decimal("96"),
            labor_cost=Decimal("0"),
            parts_cost=Decimal("0"),
        )
    )
    db_session.flush()
    db_session.refresh(asset)
    result = compute_asset_utilization(asset, now)
    assert result.windows[90].maintenance_days == pytest.approx(4, abs=0.05)
    assert result.windows[90].maintenance_days / result.windows[90].eligible_days >= 0.2
    assert "High Downtime" in result.flags


def test_high_downtime_four_days_under_20_percent_does_not_flag(db_session, org_users):
    now = datetime.now(UTC)
    asset = _available_asset_with_history(db_session, org_users["asset"], now, days=30)
    db_session.add(
        MaintenanceWorkOrder(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            title="Four of thirty",
            status=WorkOrderStatus.COMPLETED,
            opened_at=now - timedelta(days=4),
            closed_at=now,
            downtime_hours=Decimal("96"),
            labor_cost=Decimal("0"),
            parts_cost=Decimal("0"),
        )
    )
    db_session.flush()
    db_session.refresh(asset)
    result = compute_asset_utilization(asset, now)
    assert result.windows[90].maintenance_days == pytest.approx(4, abs=0.05)
    assert result.windows[90].maintenance_days / result.windows[90].eligible_days < 0.2
    assert "High Downtime" not in result.flags


def test_parts_transactions_and_work_order_usage(client, org_users, db_session):
    manager = login(client, "ops.manager@test.com")
    tech = login(client, "ops.tech@test.com")
    other = login(client, "other.admin@test.com")
    asset = org_users["asset"]
    wo = MaintenanceWorkOrder(
        organization_id=asset.organization_id,
        asset_id=asset.id,
        title="Replace camera",
        status=WorkOrderStatus.IN_PROGRESS,
        opened_at=datetime.now(UTC),
        labor_cost=Decimal("0"),
        parts_cost=Decimal("0"),
    )
    db_session.add(wo)
    db_session.commit()

    created = client.post(
        "/parts",
        headers=manager,
        json={
            "sku": "CAM-100",
            "name": "ALPR Camera",
            "quantity_on_hand": 4,
            "reorder_point": 2,
            "unit_cost": 250,
        },
    )
    assert created.status_code == 201, created.text
    part_id = created.json()["id"]
    assert created.json()["quantity_on_hand"] == 4
    assert created.json()["stock_state"] == "ok"

    used = client.post(
        f"/work-orders/{wo.id}/parts",
        headers=tech,
        json={"part_id": part_id, "quantity": 2},
    )
    assert used.status_code == 201, used.text
    assert float(used.json()["line"]["unit_cost"]) == 250
    assert float(used.json()["work_order"]["parts_cost"]) == 500

    part = client.get(f"/parts/{part_id}", headers=manager).json()
    assert part["quantity_on_hand"] == 2
    assert part["stock_state"] == "low_stock"

    blocked = client.post(
        f"/work-orders/{wo.id}/parts",
        headers=tech,
        json={"part_id": part_id, "quantity": 5},
    )
    assert blocked.status_code == 409

    override_denied = client.post(
        f"/work-orders/{wo.id}/parts",
        headers=tech,
        json={"part_id": part_id, "quantity": 5, "allow_negative": True},
    )
    assert override_denied.status_code == 403

    override = client.post(
        f"/work-orders/{wo.id}/parts",
        headers=manager,
        json={"part_id": part_id, "quantity": 5, "allow_negative": True},
    )
    assert override.status_code == 201
    assert client.get(f"/parts/{part_id}", headers=manager).json()["quantity_on_hand"] == -3
    assert client.get(f"/parts/{part_id}", headers=other).status_code == 404

    summary = client.get("/parts/summary", headers=manager).json()
    assert summary["out_of_stock_count"] >= 1
    assert any(row["sku"] == "CAM-100" for row in summary["reorder_needed"])


def test_install_part_on_asset_from_parts_tab(client, org_users):
    manager = login(client, "ops.manager@test.com")
    asset = org_users["asset"]
    created = client.post(
        "/parts",
        headers=manager,
        json={"sku": "LENS-9", "name": "Lens", "quantity_on_hand": 3, "reorder_point": 1, "unit_cost": 40},
    )
    assert created.status_code == 201, created.text
    part_id = created.json()["id"]
    installed = client.post(
        f"/parts/{part_id}/install",
        headers=manager,
        json={"asset_id": str(asset.id), "quantity": 1, "notes": "Installed to replace cracked lens"},
    )
    assert installed.status_code == 201, installed.text
    body = installed.json()
    assert body["part"]["quantity_on_hand"] == 2
    assert body["transaction"]["txn_type"] == "work_order_usage"
    assert body["transaction"]["asset_id"] == str(asset.id)
    assert body["transaction"]["asset_vin"] == asset.vin
    history = client.get(f"/parts/{part_id}/transactions", headers=manager).json()
    used = [row for row in history if row["txn_type"] == "work_order_usage"]
    assert used
    assert used[0]["asset_vin"] == asset.vin


def test_technician_cannot_create_part_but_can_install(client, org_users):
    manager = login(client, "ops.manager@test.com")
    tech = login(client, "ops.tech@test.com")
    created = client.post(
        "/parts",
        headers=manager,
        json={"sku": "BOLT-TECH", "name": "Bolt", "quantity_on_hand": 5, "unit_cost": 2},
    )
    assert created.status_code == 201, created.text
    part_id = created.json()["id"]

    denied = client.post(
        "/parts",
        headers=tech,
        json={"sku": "NEW-SKU", "name": "Should fail", "quantity_on_hand": 1},
    )
    assert denied.status_code == 403

    txn_denied = client.post(
        f"/parts/{part_id}/transactions",
        headers=tech,
        json={"txn_type": "receipt", "quantity": 3},
    )
    assert txn_denied.status_code == 403

    installed = client.post(
        f"/parts/{part_id}/install",
        headers=tech,
        json={"asset_id": str(org_users["asset"].id), "quantity": 1},
    )
    assert installed.status_code == 201, installed.text
    assert installed.json()["part"]["quantity_on_hand"] == 4


def test_attention_dedup_and_resolution(client, org_users):
    manager = login(client, "ops.manager@test.com")
    created = client.post(
        "/parts",
        headers=manager,
        json={"sku": "BOLT-1", "name": "Bolt", "quantity_on_hand": 0, "reorder_point": 1, "unit_cost": 1},
    )
    assert created.status_code == 201
    first = client.get("/attention", headers=manager)
    second = client.get("/attention", headers=manager)
    assert first.status_code == 200
    open_items = [row for row in first.json() if row["kind"] == "part_out_of_stock"]
    again = [row for row in second.json() if row["kind"] == "part_out_of_stock"]
    assert len(open_items) == 1
    assert len(again) == 1
    assert open_items[0]["id"] == again[0]["id"]

    dismissed = client.post(f"/attention/{open_items[0]['id']}/dismiss", headers=manager)
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    after = client.get("/attention", headers=manager).json()
    assert not any(row["kind"] == "part_out_of_stock" and row["status"] == "open" for row in after)

    resolved = client.post(f"/attention/{open_items[0]['id']}/resolve", headers=manager)
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"


def test_repair_cost_replacement_warning_still_works(client, org_users, db_session):
    asset = org_users["asset"]
    db_session.add(
        MaintenanceWorkOrder(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            title="Major repair",
            status=WorkOrderStatus.COMPLETED,
            opened_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
            labor_cost=Decimal("600"),
            parts_cost=Decimal("500"),
        )
    )
    db_session.commit()
    headers = login(client, "ops.admin@test.com")
    response = client.get(f"/assets/{asset.id}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["lemon_flag"] is True
    assert "CRITICAL COST WARNING" in (body["lemon_warning"] or "")
    assert float(body["maintenance_cost_pct"]) >= 100
    assert float(body["total_repair_cost"]) == 1100


def test_in_transit_updates_map_and_dashboard_counts(client, org_users, db_session):
    asset = org_users["asset"]
    warehouse = Warehouse(
        organization_id=asset.organization_id,
        name="Transit Yard",
        latitude=Decimal("33.4484"),
        longitude=Decimal("-112.0740"),
    )
    db_session.add(warehouse)
    db_session.flush()
    asset.warehouse_id = warehouse.id
    asset.operational_status = AssetOperationalStatus.AVAILABLE
    db_session.commit()

    headers = login(client, "ops.manager@test.com")
    before = client.get("/dashboard/kpis", headers=headers).json()
    started = client.post(
        f"/assets/{asset.id}/deployments",
        headers=headers,
        json={
            "location": "To agency yard",
            "custody_type": "In Transit",
            "carrier_name": "FedEx",
            "tracking_code": "1Z999",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["operational_status"] == "in_transit"

    after = client.get("/dashboard/kpis", headers=headers).json()
    assert after["available"] == before["available"] - 1
    assert after["in_transit"] == before.get("in_transit", 0) + 1

    mapped = client.get("/map/assets", headers=headers).json()
    pin = next(row for row in mapped if row["id"] == str(asset.id))
    assert pin["operational_status"] == "in_transit"
    assert pin["location_source"] == "in_transit"
    assert pin["latitude"]
    assert pin["longitude"]


def test_end_workflow_in_transit_does_not_reset_available(client, org_users, db_session):
    asset = org_users["asset"]
    now = datetime.now(UTC)
    db_session.add(
        Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location="Agency site",
            status=DeploymentStatus.ACTIVE,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            started_at=now - timedelta(days=2),
        )
    )
    asset.operational_status = AssetOperationalStatus.DEPLOYED
    db_session.commit()

    headers = login(client, "ops.manager@test.com")
    ended = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={
            "disposition": "in_transit",
            "transit_origin": "Phoenix",
            "transit_destination": "Houston",
            "carrier_name": "FedEx",
            "tracking_code": "1Z123",
        },
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["operational_status"] == "in_transit"
    assert ended.json()["current_custody_type"] == "In Transit"

    kpis = client.get("/dashboard/kpis", headers=headers).json()
    assert kpis["in_transit"] >= 1


def test_end_workflow_transfer_keeps_asset_on_deployments_list(client, org_users, db_session):
    asset = org_users["asset"]
    agency = Agency(
        organization_id=asset.organization_id,
        name="Scottsdale PD",
        latitude=Decimal("33.4942000"),
        longitude=Decimal("-111.9261000"),
    )
    db_session.add(agency)
    db_session.add(
        Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location="Springfield, MO",
            status=DeploymentStatus.DEPLOYED,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            started_at=datetime.now(UTC),
        )
    )
    asset.operational_status = AssetOperationalStatus.DEPLOYED
    asset.current_custody_type = CustodyType.CUSTOMER_AGENCY
    db_session.commit()

    headers = login(client, "ops.manager@test.com")
    ended = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={
            "disposition": "transfer_to_agency",
            "next_agency_id": str(agency.id),
        },
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["operational_status"] == "deployed"

    listed = client.get("/deployments", headers=headers).json()
    open_for_asset = [row for row in listed if row["asset_id"] == str(asset.id)]
    assert len(open_for_asset) == 1
    assert open_for_asset[0]["ended_at"] is None
    assert open_for_asset[0]["location"] == "Scottsdale PD"


def test_end_workflow_return_to_warehouse_drops_active_deployment(client, org_users, db_session):
    asset = org_users["asset"]
    warehouse = Warehouse(
        organization_id=asset.organization_id,
        name="Return Yard",
        latitude=Decimal("33.4484000"),
        longitude=Decimal("-112.0740000"),
    )
    db_session.add(warehouse)
    db_session.add(
        Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location="Springfield, MO",
            status=DeploymentStatus.DEPLOYED,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            started_at=datetime.now(UTC),
        )
    )
    asset.operational_status = AssetOperationalStatus.DEPLOYED
    db_session.commit()

    headers = login(client, "ops.manager@test.com")
    ended = client.post(
        f"/assets/{asset.id}/deployments/end-workflow",
        headers=headers,
        json={
            "disposition": "return_to_warehouse",
            "destination_warehouse_id": str(warehouse.id),
        },
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["operational_status"] == "available"
    listed = client.get("/deployments", headers=headers).json()
    assert all(row["asset_id"] != str(asset.id) for row in listed)


def test_partial_transfer_is_rejected(client, org_users):
    manager = login(client, "ops.manager@test.com")
    created = client.post(
        "/parts",
        headers=manager,
        json={
            "sku": "CAM-XFER",
            "name": "Camera",
            "quantity_on_hand": 10,
            "warehouse_location": "Phoenix",
            "unit_cost": 10,
        },
    )
    assert created.status_code == 201
    part_id = created.json()["id"]
    transferred = client.post(
        f"/parts/{part_id}/transactions",
        headers=manager,
        json={"txn_type": "transfer", "quantity": 2, "transfer_location": "Mesa"},
    )
    assert transferred.status_code == 409
    part = client.get(f"/parts/{part_id}", headers=manager).json()
    assert part["quantity_on_hand"] == 10
    assert part["warehouse_location"] == "Phoenix"


def test_usage_reversal_restores_stock_and_wo_cost(client, org_users, db_session):
    manager = login(client, "ops.manager@test.com")
    asset = org_users["asset"]
    wo = MaintenanceWorkOrder(
        organization_id=asset.organization_id,
        asset_id=asset.id,
        title="Reverse me",
        status=WorkOrderStatus.IN_PROGRESS,
        opened_at=datetime.now(UTC),
        labor_cost=Decimal("0"),
        parts_cost=Decimal("0"),
    )
    db_session.add(wo)
    db_session.commit()
    created = client.post(
        "/parts",
        headers=manager,
        json={"sku": "CAM-REV", "name": "Reversible", "quantity_on_hand": 5, "unit_cost": 20},
    )
    part_id = created.json()["id"]
    used = client.post(
        f"/work-orders/{wo.id}/parts",
        headers=manager,
        json={"part_id": part_id, "quantity": 2},
    )
    assert used.status_code == 201
    history = client.get(f"/parts/{part_id}/transactions", headers=manager).json()
    usage = next(row for row in history if row["txn_type"] == "work_order_usage")
    reversed_txn = client.post(
        f"/parts/transactions/{usage['id']}/reverse",
        headers=manager,
        json={"reason": "Used the wrong SKU"},
    )
    assert reversed_txn.status_code == 201, reversed_txn.text
    assert reversed_txn.json()["part"]["quantity_on_hand"] == 5
    assert float(reversed_txn.json()["work_order"]["parts_cost"]) == 0
    history = client.get(f"/parts/{part_id}/transactions", headers=manager).json()
    assert any(row["txn_type"] == "work_order_usage" for row in history)
    assert any(row["txn_type"] == "reversal" and row["reversal_reason"] == "Used the wrong SKU" for row in history)
    again = client.post(
        f"/parts/transactions/{usage['id']}/reverse",
        headers=manager,
        json={"reason": "second try"},
    )
    assert again.status_code == 409


def test_attention_reopens_when_condition_returns(client, org_users):
    manager = login(client, "ops.manager@test.com")
    created = client.post(
        "/parts",
        headers=manager,
        json={"sku": "REOPEN-1", "name": "Reopen", "quantity_on_hand": 0, "reorder_point": 1, "unit_cost": 1},
    )
    part_id = created.json()["id"]
    items = [row for row in client.get("/attention", headers=manager).json() if row["kind"] == "part_out_of_stock" and row["part_id"] == part_id]
    assert len(items) == 1
    item_id = items[0]["id"]
    client.post(
        f"/parts/{part_id}/transactions",
        headers=manager,
        json={"txn_type": "receipt", "quantity": 4},
    )
    after = client.get("/attention?include_resolved=true", headers=manager).json()
    resolved = next(row for row in after if row["id"] == item_id)
    assert resolved["status"] == "resolved"
    used = client.post(
        f"/parts/{part_id}/install",
        headers=manager,
        json={"asset_id": str(org_users["asset"].id), "quantity": 4},
    )
    assert used.status_code == 201, used.text
    reopened = [row for row in client.get("/attention", headers=manager).json() if row["id"] == item_id]
    assert reopened
    assert reopened[0]["status"] == "open"
