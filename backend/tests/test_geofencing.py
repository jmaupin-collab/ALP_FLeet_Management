"""Geofence crossings: automatic check-in, and alerts for moves nobody logged.

Every warehouse and agency with coordinates is a fence. Arriving at a warehouse
checks a unit in through the ordinary custody workflow; arriving at an agency is
announced but changes nothing; leaving anywhere raises an alert unless something
on the asset already said the unit was on the move.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.geofencing import containing_zone, distance_meters, evaluate_fix, zones_for_organization
from app.models import (
    Agency,
    Asset,
    AssetOperationalStatus,
    AssetType,
    AssetZoneState,
    CustodyType,
    Deployment,
    DeploymentStatus,
    GeofenceEvent,
    GeofenceEventType,
    Notification,
    Organization,
    User,
    UserRole,
    Warehouse,
)
from app.auth import hash_password

# Mohawk DC and a point about 5 km away, well outside any fence.
YARD = (Decimal("33.6846"), Decimal("-112.0491"))
DOWN_THE_ROAD = (Decimal("33.7300"), Decimal("-112.0491"))
AGENCY_SITE = (Decimal("33.4484"), Decimal("-112.0740"))


@pytest.fixture
def org(db_session):
    organization = Organization(name="Fence Org", slug="fence-org", org_type="internal")
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        User(
            organization_id=organization.id,
            email="manager@fence.com",
            hashed_password=hash_password("password123"),
            full_name="Fleet Manager",
            role=UserRole.FLEET_MANAGER,
            is_active=True,
        )
    )
    db_session.commit()
    return organization


@pytest.fixture
def warehouse(db_session, org):
    row = Warehouse(
        organization_id=org.id,
        name="Mohawk DC",
        latitude=YARD[0],
        longitude=YARD[1],
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def agency(db_session, org):
    row = Agency(
        organization_id=org.id,
        name="Phoenix PD",
        latitude=AGENCY_SITE[0],
        longitude=AGENCY_SITE[1],
    )
    db_session.add(row)
    db_session.commit()
    return row


def make_asset(db, org, vin="FENCE00000001", custody=CustodyType.WAREHOUSE_DEPOT, warehouse_id=None):
    asset = Asset(
        organization_id=org.id,
        vin=vin,
        make_model="Falcon",
        asset_type=AssetType.ALPR_TRAILER,
        initial_purchase_cost=Decimal(18000),
        current_location="Mohawk DC",
        current_custody_type=custody,
        operational_status=AssetOperationalStatus.AVAILABLE,
        warehouse_id=warehouse_id,
        telematics_provider="geotab",
        telematics_device_id=f"DEV-{vin}",
    )
    db.add(asset)
    db.commit()
    return asset


def alerts(db, org):
    return db.query(Notification).filter(Notification.organization_id == org.id).all()


# --- geometry --------------------------------------------------------------


def test_distance_is_accurate_enough_to_fence_a_yard():
    # One degree of latitude is close to 111 km anywhere on Earth.
    metres = distance_meters(33.0, -112.0, 34.0, -112.0)
    assert 110_000 < metres < 112_000


def test_a_site_without_coordinates_has_no_fence(db_session, org):
    """Not a fence at (0, 0), which would swallow every unit that lost signal."""
    db_session.add(Warehouse(organization_id=org.id, name="Unmapped DC"))
    db_session.commit()

    assert zones_for_organization(db_session, org.id) == []


def test_overlapping_fences_resolve_to_the_nearer_site(db_session, org, warehouse):
    near = Agency(
        organization_id=org.id,
        name="Next Door",
        latitude=Decimal("33.6848"),
        longitude=Decimal("-112.0491"),
    )
    db_session.add(near)
    db_session.commit()

    zone = containing_zone(zones_for_organization(db_session, org.id), YARD[0], YARD[1])

    assert zone.name == "Mohawk DC"


# --- arriving --------------------------------------------------------------


def test_arriving_at_a_warehouse_checks_the_unit_in(db_session, org, warehouse):
    asset = make_asset(db_session, org, custody=CustodyType.CUSTOMER_AGENCY)

    events = evaluate_fix(db_session, asset, YARD[0], YARD[1])
    db_session.commit()

    assert [e.event_type for e in events] == [GeofenceEventType.ENTERED]
    assert asset.current_custody_type == CustodyType.WAREHOUSE_DEPOT
    assert asset.warehouse_id == warehouse.id
    assert events[0].action_taken == "Checked in to Mohawk DC"


def test_the_check_in_goes_through_the_normal_custody_ledger(db_session, org, warehouse):
    """Not a direct write: the deployment history has to show the arrival."""
    asset = make_asset(db_session, org, custody=CustodyType.CUSTOMER_AGENCY)

    evaluate_fix(db_session, asset, YARD[0], YARD[1])
    db_session.commit()

    # A return to the depot closes out rather than opening a deployment, so the
    # arrival shows up as a completed ledger row.
    rows = db_session.query(Deployment).filter(Deployment.asset_id == asset.id).all()
    returned = [row for row in rows if row.custody_type == CustodyType.WAREHOUSE_DEPOT]
    assert len(returned) == 1
    assert returned[0].ended_at is not None
    assert "Automatic check-in" in returned[0].notes
    assert asset.operational_status == AssetOperationalStatus.AVAILABLE


def test_arriving_at_an_agency_notifies_but_changes_nothing(db_session, org, agency):
    """Parked at a customer site is not the same as handed over."""
    asset = make_asset(db_session, org)

    events = evaluate_fix(db_session, asset, AGENCY_SITE[0], AGENCY_SITE[1])
    db_session.commit()

    assert events[0].zone_name == "Phoenix PD"
    assert events[0].action_taken is None
    assert asset.current_custody_type == CustodyType.WAREHOUSE_DEPOT
    assert asset.agency_id is None
    assert any("arrived at Phoenix PD" in row.title for row in alerts(db_session, org))


def test_auto_checkin_can_be_turned_off(db_session, org, warehouse, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "geofence_auto_checkin", False, raising=False)
    asset = make_asset(db_session, org, custody=CustodyType.CUSTOMER_AGENCY)

    events = evaluate_fix(db_session, asset, YARD[0], YARD[1])
    db_session.commit()

    assert events[0].action_taken is None
    assert asset.current_custody_type == CustodyType.CUSTOMER_AGENCY
    # The alert still fires; only the status change is suppressed.
    assert any("arrived at Mohawk DC" in row.title for row in alerts(db_session, org))


# --- staying put -----------------------------------------------------------


def test_a_unit_parked_in_the_same_yard_reports_all_day_without_events(db_session, org, warehouse):
    asset = make_asset(db_session, org)
    evaluate_fix(db_session, asset, YARD[0], YARD[1])
    db_session.commit()
    before = len(alerts(db_session, org))

    for _ in range(5):
        assert evaluate_fix(db_session, asset, YARD[0], YARD[1]) == []
    db_session.commit()

    assert len(alerts(db_session, org)) == before


# --- leaving ---------------------------------------------------------------


def test_leaving_without_a_logged_reason_raises_an_alert(db_session, org, warehouse):
    asset = make_asset(db_session, org)
    evaluate_fix(db_session, asset, YARD[0], YARD[1])
    db_session.commit()

    events = evaluate_fix(db_session, asset, DOWN_THE_ROAD[0], DOWN_THE_ROAD[1])
    db_session.commit()

    assert [e.event_type for e in events] == [GeofenceEventType.EXITED]
    assert events[0].was_unexpected is True
    assert any("left Mohawk DC" in row.title for row in alerts(db_session, org))


def test_leaving_while_marked_in_transit_is_expected_and_stays_quiet(db_session, org, warehouse):
    asset = make_asset(db_session, org)
    evaluate_fix(db_session, asset, YARD[0], YARD[1])
    db_session.commit()
    asset.current_custody_type = CustodyType.IN_TRANSIT
    db_session.commit()
    before = len(alerts(db_session, org))

    events = evaluate_fix(db_session, asset, DOWN_THE_ROAD[0], DOWN_THE_ROAD[1])
    db_session.commit()

    assert events[0].was_unexpected is False
    # Recorded either way: the history must not depend on whether it alerted.
    assert len(alerts(db_session, org)) == before


def test_a_scheduled_deployment_also_explains_the_departure(db_session, org, warehouse):
    asset = make_asset(db_session, org)
    evaluate_fix(db_session, asset, YARD[0], YARD[1])
    db_session.commit()
    db_session.add(
        Deployment(
            organization_id=org.id,
            asset_id=asset.id,
            location="Phoenix PD",
            status=DeploymentStatus.SCHEDULED,
            started_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db_session.commit()

    events = evaluate_fix(db_session, asset, DOWN_THE_ROAD[0], DOWN_THE_ROAD[1])
    db_session.commit()

    assert events[0].was_unexpected is False


def test_moving_between_two_sites_records_both_halves(db_session, org, warehouse, agency):
    asset = make_asset(db_session, org)
    evaluate_fix(db_session, asset, YARD[0], YARD[1])
    db_session.commit()

    events = evaluate_fix(db_session, asset, AGENCY_SITE[0], AGENCY_SITE[1])
    db_session.commit()

    assert [e.event_type for e in events] == [GeofenceEventType.EXITED, GeofenceEventType.ENTERED]
    assert [e.zone_name for e in events] == ["Mohawk DC", "Phoenix PD"]


def test_the_history_survives_whether_or_not_it_alerted(db_session, org, warehouse):
    asset = make_asset(db_session, org, custody=CustodyType.IN_TRANSIT)
    evaluate_fix(db_session, asset, YARD[0], YARD[1])
    evaluate_fix(db_session, asset, DOWN_THE_ROAD[0], DOWN_THE_ROAD[1])
    db_session.commit()

    recorded = db_session.query(GeofenceEvent).filter(GeofenceEvent.asset_id == asset.id).all()
    assert {row.event_type for row in recorded} == {
        GeofenceEventType.ENTERED,
        GeofenceEventType.EXITED,
    }


# --- per-site radius -------------------------------------------------------


def test_a_radius_set_on_the_record_is_what_the_fence_uses(db_session, org):
    """Set through the ordinary directory create path, not by hand."""
    from app.ops import create_warehouse
    from app.schemas import DirectoryCreate

    row = create_warehouse(
        db_session,
        DirectoryCreate(name="Wide Yard", geofence_radius_m=900),
        org.id,
        None,
    )
    row.latitude, row.longitude = YARD
    db_session.commit()

    zone = next(z for z in zones_for_organization(db_session, org.id) if z.name == "Wide Yard")
    assert zone.radius_m == 900


def test_a_site_can_widen_its_own_fence(db_session, org, warehouse):
    """A point outside the default radius but inside this yard's override."""
    asset = make_asset(db_session, org)
    edge = (Decimal("33.6886"), Decimal("-112.0491"))  # ~445 m north

    assert evaluate_fix(db_session, asset, edge[0], edge[1]) == []

    warehouse.geofence_radius_m = 1000
    db_session.commit()
    events = evaluate_fix(db_session, asset, edge[0], edge[1])
    db_session.commit()

    assert [e.zone_name for e in events] == ["Mohawk DC"]


# --- tenancy ---------------------------------------------------------------


def test_an_asset_is_only_fenced_by_its_own_tenants_sites(db_session, org, warehouse):
    other = Organization(name="Other", slug="other-fence-org", org_type="internal")
    db_session.add(other)
    db_session.flush()
    theirs = make_asset(db_session, other, vin="OTHER000000001")

    assert evaluate_fix(db_session, theirs, YARD[0], YARD[1]) == []
    db_session.commit()
    assert db_session.get(AssetZoneState, theirs.id).zone_id is None
