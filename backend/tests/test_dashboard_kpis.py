"""Dashboard KPIs are aggregated in SQL; these pin them to the per-asset rules.

dashboard_kpis() no longer walks each asset's relationships, so the invariant
worth protecting is that its counts still equal what effective_operational_status
reports asset by asset.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    Asset,
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    Deployment,
    DeploymentStatus,
    MaintenanceWorkOrder,
    Organization,
    WorkOrderStatus,
)
from app.services import dashboard_kpis, effective_operational_status


@pytest.fixture
def fleet(db_session):
    org = Organization(name="KPI Org", slug="kpi-org", org_type="internal")
    db_session.add(org)
    db_session.flush()

    def make_asset(vin, asset_type, status, custody, cost, archived=False):
        asset = Asset(
            organization_id=org.id,
            vin=vin,
            make_model=f"Model {vin}",
            asset_type=asset_type,
            initial_purchase_cost=Decimal(cost),
            current_location="Depot",
            current_custody_type=custody,
            operational_status=status,
            is_archived=archived,
        )
        db_session.add(asset)
        return asset

    deployed = make_asset(
        "KPIDEPLOYED01", AssetType.ALPR_TRAILER,
        AssetOperationalStatus.DEPLOYED, CustodyType.CUSTOMER_AGENCY, 25000,
    )
    # Marked deployed but with no open deployment: must count as available.
    stale = make_asset(
        "KPISTALE00001", AssetType.ALPR_TRAILER,
        AssetOperationalStatus.DEPLOYED, CustodyType.WAREHOUSE_DEPOT, 26000,
    )
    in_transit = make_asset(
        "KPITRANSIT001", AssetType.SEMI_TRUCK,
        AssetOperationalStatus.AVAILABLE, CustodyType.IN_TRANSIT, 90000,
    )
    maintenance = make_asset(
        "KPIMAINT00001", AssetType.FLEET_VEHICLE,
        AssetOperationalStatus.MAINTENANCE, CustodyType.WAREHOUSE_DEPOT, 30000,
    )
    archived = make_asset(
        "KPIARCHIVE001", AssetType.FLEET_VEHICLE,
        AssetOperationalStatus.AVAILABLE, CustodyType.WAREHOUSE_DEPOT, 31000,
        archived=True,
    )
    db_session.flush()

    db_session.add(
        Deployment(
            asset_id=deployed.id,
            organization_id=org.id,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            status=DeploymentStatus.ACTIVE,
            started_at=datetime.now(UTC),
            ended_at=None,
            location="Field",
        )
    )
    db_session.add(
        MaintenanceWorkOrder(
            asset_id=maintenance.id,
            organization_id=org.id,
            title="Brakes",
            status=WorkOrderStatus.COMPLETED,
            downtime_hours=Decimal("12.5"),
            opened_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    return {"org": org, "deployed": deployed, "stale": stale, "in_transit": in_transit,
            "maintenance": maintenance, "archived": archived}


def test_counts_match_per_asset_status(db_session, fleet):
    kpis = dashboard_kpis(db_session, fleet["org"].id)

    assets = db_session.scalars(
        select(Asset)
        .where(Asset.organization_id == fleet["org"].id)
        .where(Asset.is_archived.is_(False))
    ).all()

    expected = {}
    for asset in assets:
        status = effective_operational_status(asset)
        expected[status] = expected.get(status, 0) + 1

    assert kpis.fleet_size == len(assets)
    assert kpis.deployed == expected.get(AssetOperationalStatus.DEPLOYED, 0)
    assert kpis.available == expected.get(AssetOperationalStatus.AVAILABLE, 0)
    assert kpis.in_transit == expected.get(AssetOperationalStatus.IN_TRANSIT, 0)
    assert kpis.in_maintenance == expected.get(AssetOperationalStatus.MAINTENANCE, 0)


def test_stale_deployed_flag_counts_as_available(db_session, fleet):
    kpis = dashboard_kpis(db_session, fleet["org"].id)

    # One genuinely deployed asset; the stale one falls back to available.
    assert kpis.deployed == 1
    assert kpis.available >= 1


def test_archived_assets_are_excluded(db_session, fleet):
    kpis = dashboard_kpis(db_session, fleet["org"].id)

    assert kpis.fleet_size == 4
    assert Decimal(kpis.total_purchase_cost) == Decimal(25000 + 26000 + 90000 + 30000)


def test_downtime_is_summed_from_work_orders(db_session, fleet):
    kpis = dashboard_kpis(db_session, fleet["org"].id)

    assert Decimal(kpis.downtime_hours_ytd) == Decimal("12.5")
    vehicles = next(row for row in kpis.by_asset_type if row.asset_type == AssetType.FLEET_VEHICLE)
    assert Decimal(vehicles.downtime_hours_ytd) == Decimal("12.5")


def test_totals_reconcile_across_asset_types(db_session, fleet):
    kpis = dashboard_kpis(db_session, fleet["org"].id)

    assert sum(row.total_assets for row in kpis.by_asset_type) == kpis.fleet_size
    assert sum(row.deployed for row in kpis.by_asset_type) == kpis.deployed
    assert sum(Decimal(row.total_purchase_cost) for row in kpis.by_asset_type) == Decimal(
        kpis.total_purchase_cost
    )
