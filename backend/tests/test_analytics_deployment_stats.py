"""The deployment chart counts deployments, not custody-ledger rows.

Deployment is an append-only history of where an asset has been, so a single
trailer going warehouse -> transit -> agency writes three rows. Counting rows
reported more deployments in a month than there were trailers to deploy.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.analytics import build_analytics
from app.models import (
    Asset,
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    Deployment,
    DeploymentStatus,
    Organization,
)


@pytest.fixture
def org(db_session):
    organization = Organization(name="Chart Org", slug="chart-org", org_type="internal")
    db_session.add(organization)
    db_session.commit()
    return organization


def make_asset(db, org, vin, asset_type=AssetType.ALPR_TRAILER):
    asset = Asset(
        organization_id=org.id,
        vin=vin,
        make_model="Falcon",
        asset_type=asset_type,
        initial_purchase_cost=Decimal(18000),
        current_location="Depot",
        current_custody_type=CustodyType.WAREHOUSE_DEPOT,
        operational_status=AssetOperationalStatus.AVAILABLE,
    )
    db.add(asset)
    db.commit()
    return asset


def add_ledger_row(db, org, asset, custody_type, started_at, status=DeploymentStatus.DEPLOYED):
    db.add(
        Deployment(
            organization_id=org.id,
            asset_id=asset.id,
            location="Somewhere",
            status=status,
            custody_type=custody_type,
            started_at=started_at,
        )
    )
    db.commit()


def this_month(hub):
    label = datetime.now(UTC).strftime("%b %Y")
    return next(row for row in hub.deployment_stats if row.month == label)


def test_a_warehouse_put_away_is_not_a_deployment(db_session, org):
    asset = make_asset(db_session, org, "CHART00000001")
    now = datetime.now(UTC)
    add_ledger_row(db_session, org, asset, CustodyType.WAREHOUSE_DEPOT, now)
    add_ledger_row(db_session, org, asset, CustodyType.IN_TRANSIT, now)
    add_ledger_row(db_session, org, asset, CustodyType.CUSTOMER_AGENCY, now)

    # One trailer went out once, however many rows the ledger wrote getting there.
    assert this_month(build_analytics(db_session, None, org.id)).deployment_count == 1


def test_the_percentage_is_the_share_of_the_fleet_that_went_out(db_session, org):
    now = datetime.now(UTC)
    for index in range(4):
        asset = make_asset(db_session, org, f"CHART0000000{index}")
        if index < 1:
            add_ledger_row(db_session, org, asset, CustodyType.CUSTOMER_AGENCY, now)

    assert this_month(build_analytics(db_session, None, org.id)).deployment_percentage == Decimal("25.0")


def test_redeploying_one_unit_cannot_push_the_share_past_a_full_fleet(db_session, org):
    """Count rises with each deployment; the percentage tracks distinct assets."""
    now = datetime.now(UTC)
    asset = make_asset(db_session, org, "CHART00000001")
    add_ledger_row(db_session, org, asset, CustodyType.CUSTOMER_AGENCY, now - timedelta(days=5))
    add_ledger_row(db_session, org, asset, CustodyType.CUSTOMER_AGENCY, now)

    row = this_month(build_analytics(db_session, None, org.id))
    assert row.deployment_count == 2
    assert row.deployment_percentage == Decimal("100.0")


def test_the_percentage_measures_the_type_being_asked_about(db_session, org):
    """Filtered deployments over an unfiltered fleet was a ratio of nothing."""
    now = datetime.now(UTC)
    trailer = make_asset(db_session, org, "CHART00000001", AssetType.ALPR_TRAILER)
    make_asset(db_session, org, "CHART00000002", AssetType.SEMI_TRUCK)
    make_asset(db_session, org, "CHART00000003", AssetType.SEMI_TRUCK)
    add_ledger_row(db_session, org, trailer, CustodyType.CUSTOMER_AGENCY, now)

    hub = build_analytics(db_session, AssetType.ALPR_TRAILER, org.id)

    # One trailer of one trailer, not one of three assets.
    assert this_month(hub).deployment_percentage == Decimal("100.0")


def test_the_last_twelve_months_are_twelve_distinct_months(db_session, org):
    """Stepping back 30 days at a time duplicated months and skipped others."""
    months = [row.month for row in build_analytics(db_session, None, org.id).deployment_stats]

    assert len(months) == 12
    assert len(set(months)) == 12
    assert months[-1] == datetime.now(UTC).strftime("%b %Y")


def test_another_tenant_cannot_show_up_in_the_chart(db_session, org):
    other = Organization(name="Other Org", slug="other-chart-org", org_type="internal")
    db_session.add(other)
    db_session.commit()
    theirs = make_asset(db_session, other, "OTHER000000001")
    add_ledger_row(db_session, other, theirs, CustodyType.CUSTOMER_AGENCY, datetime.now(UTC))

    assert this_month(build_analytics(db_session, None, org.id)).deployment_count == 0
