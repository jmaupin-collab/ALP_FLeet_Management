"""Agencies geocode like warehouses, and their pin is the deployment fallback.

An agency without coordinates puts its deployed assets nowhere on the map, so
creating or editing an agency resolves the address through the same helper
warehouses use. A geocode miss is never fatal, and a vague "City, ST" edit is
not allowed to drag an already precise pin to a city centroid.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.location import get_asset_location
from app.models import (
    Agency,
    Asset,
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    Deployment,
    DeploymentStatus,
    GPSLocation,
    LocationSource,
    Organization,
)
from app.ops import create_agency, patch_directory
from app.schemas import DirectoryCreate, DirectoryUpdate

PHOENIX = (Decimal("33.448376"), Decimal("-112.074036"))
TUCSON = (Decimal("32.221743"), Decimal("-110.926479"))


@pytest.fixture
def org(db_session):
    organization = Organization(name="Geo Org", slug="geo-org", org_type="internal")
    db_session.add(organization)
    db_session.commit()
    return organization


@pytest.fixture
def geocoder(monkeypatch):
    """Map known addresses to coordinates; anything else is a miss."""
    table = {
        "100 W Washington St, Phoenix, AZ 85003": PHOENIX,
        "255 W Alameda St, Tucson, AZ 85701": TUCSON,
    }
    calls = []

    def fake_geocode(address):
        calls.append(address)
        return table.get((address or "").strip())

    monkeypatch.setattr("app.geocoding.geocode_address", fake_geocode)
    return calls


def make_agency(db, org, name, address):
    agency = create_agency(db, DirectoryCreate(name=name, address=address), org.id, None)
    db.commit()
    return agency


def deployed_asset(db, org, agency, vin="GEOASSET00001"):
    asset = Asset(
        organization_id=org.id,
        vin=vin,
        make_model="Falcon",
        asset_type=AssetType.ALPR_TRAILER,
        initial_purchase_cost=Decimal(18000),
        current_location=agency.name,
        current_custody_type=CustodyType.CUSTOMER_AGENCY,
        operational_status=AssetOperationalStatus.DEPLOYED,
        agency_id=agency.id,
    )
    db.add(asset)
    db.flush()
    db.add(
        Deployment(
            asset_id=asset.id,
            organization_id=org.id,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            status=DeploymentStatus.ACTIVE,
            started_at=datetime.now(UTC),
            location=agency.name,
        )
    )
    db.commit()
    return asset


def test_creating_an_agency_geocodes_its_address(db_session, org, geocoder):
    agency = make_agency(db_session, org, "Phoenix PD", "100 W Washington St, Phoenix, AZ 85003")

    assert (agency.latitude, agency.longitude) == PHOENIX


def test_editing_the_address_moves_the_coordinates(db_session, org, geocoder):
    agency = make_agency(db_session, org, "Phoenix PD", "100 W Washington St, Phoenix, AZ 85003")

    patch_directory(
        db_session,
        agency,
        DirectoryUpdate(address="255 W Alameda St, Tucson, AZ 85701"),
        None,
    )
    db_session.commit()

    assert (agency.latitude, agency.longitude) == TUCSON
    assert agency.address == "255 W Alameda St, Tucson, AZ 85701"


def test_a_failed_geocode_keeps_the_address_and_does_not_raise(db_session, org, geocoder):
    agency = make_agency(db_session, org, "Nowhere PD", "9999 Nonexistent Rd, Nowhere, ZZ")

    assert agency.address == "9999 Nonexistent Rd, Nowhere, ZZ"
    assert agency.latitude is None and agency.longitude is None

    # An edit that also misses must leave the previous good pin alone.
    patch_directory(
        db_session,
        agency,
        DirectoryUpdate(address="100 W Washington St, Phoenix, AZ 85003"),
        None,
    )
    db_session.commit()
    assert (agency.latitude, agency.longitude) == PHOENIX

    patch_directory(db_session, agency, DirectoryUpdate(address="8888 Still Missing Rd, Nowhere, ZZ"), None)
    db_session.commit()
    assert (agency.latitude, agency.longitude) == PHOENIX


def test_a_generic_city_state_edit_does_not_override_a_precise_pin(db_session, org, geocoder):
    agency = make_agency(db_session, org, "Phoenix PD", "100 W Washington St, Phoenix, AZ 85003")

    patch_directory(db_session, agency, DirectoryUpdate(address="Tucson, AZ"), None)
    db_session.commit()

    assert (agency.latitude, agency.longitude) == PHOENIX
    assert agency.address == "Tucson, AZ"


def test_a_deployed_asset_falls_back_to_its_agency_coordinates(db_session, org, geocoder):
    agency = make_agency(db_session, org, "Phoenix PD", "100 W Washington St, Phoenix, AZ 85003")
    asset = deployed_asset(db_session, org, agency)

    location = get_asset_location(db_session, asset)

    assert (location.latitude, location.longitude) == PHOENIX
    assert location.location_source == LocationSource.CUSTOMER


def test_transferring_to_another_agency_moves_the_fallback_pin(db_session, org, geocoder):
    phoenix = make_agency(db_session, org, "Phoenix PD", "100 W Washington St, Phoenix, AZ 85003")
    tucson = make_agency(db_session, org, "Tucson PD", "255 W Alameda St, Tucson, AZ 85701")
    asset = deployed_asset(db_session, org, phoenix)

    assert get_asset_location(db_session, asset).latitude == PHOENIX[0]

    asset.agency_id = tucson.id
    asset.current_location = tucson.name
    db_session.commit()

    location = get_asset_location(db_session, asset)
    assert (location.latitude, location.longitude) == TUCSON


def test_real_telematics_gps_still_outranks_the_agency_pin(db_session, org, geocoder):
    agency = make_agency(db_session, org, "Phoenix PD", "100 W Washington St, Phoenix, AZ 85003")
    asset = deployed_asset(db_session, org, agency)

    db_session.add(
        GPSLocation(
            asset_id=asset.id,
            latitude=Decimal("34.500000"),
            longitude=Decimal("-111.500000"),
            location_timestamp=datetime.now(UTC),
            telematics_provider="Samsara",
            telematics_device_id="DEV-1",
        )
    )
    db_session.commit()

    location = get_asset_location(db_session, asset)

    assert location.latitude == Decimal("34.500000")
    assert location.location_source == LocationSource.GPS
