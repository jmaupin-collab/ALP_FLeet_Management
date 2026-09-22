"""Asset location determination service with priority logic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Agency,
    Asset,
    AssetOperationalStatus,
    CustodyType,
    Deployment,
    GPSLocation,
    LocationSource,
    MaintenanceWorkOrder,
    Warehouse,
    WorkOrderStatus,
)


class AssetLocation(NamedTuple):
    """Resolved location for an asset with source transparency."""
    latitude: Decimal | None
    longitude: Decimal | None
    location_name: str
    location_source: LocationSource
    location_timestamp: datetime
    is_stale: bool  # For GPS locations older than threshold
    # Additional context
    address: str | None = None
    city: str | None = None
    state: str | None = None
    # In-transit specific
    origin: str | None = None
    destination: str | None = None
    carrier: str | None = None
    tracking_code: str | None = None


# Configuration
GPS_STALE_THRESHOLD_HOURS = 24  # GPS older than this is considered stale


def is_telematics_fix(row: GPSLocation | None) -> bool:
    """Workflow-copied warehouse/agency pins are not telematics."""
    if row is None:
        return False
    provider = (row.telematics_provider or "").strip()
    device = (row.telematics_device_id or "").strip()
    return bool(provider or device)


def get_latest_telematics_gps(db: Session, asset_id: UUID) -> GPSLocation | None:
    rows = db.scalars(
        select(GPSLocation)
        .where(GPSLocation.asset_id == asset_id)
        .order_by(GPSLocation.location_timestamp.desc())
    ).all()
    for row in rows:
        if is_telematics_fix(row):
            return row
    return None


def _party_label(agency: Agency | None = None, warehouse: Warehouse | None = None) -> list[str]:
    labels: list[str] = []
    if agency:
        labels.append(agency.name)
        if agency.site_name:
            labels.append(agency.site_name)
            labels.append(f"{agency.name} - {agency.site_name}")
    if warehouse:
        labels.append(warehouse.name)
    return [label.strip().lower() for label in labels if label and label.strip()]


def resolve_deployment_snapshot(
    db: Session,
    organization_id: UUID | None,
    agency_id: UUID | None,
    latitude,
    longitude,
    address: str | None,
    location: str | None = None,
    warehouse_id: UUID | None = None,
):
    """Explicit coords and street addresses win. Generic city/state does not beat HQ pins."""
    if latitude is not None and longitude is not None:
        return latitude, longitude, address
    from app.geocoding import geocode_address, looks_like_street_address

    agency = db.get(Agency, agency_id) if agency_id else None
    if agency is None and organization_id:
        agency = resolve_agency_by_location(db, organization_id, location)
    warehouse = db.get(Warehouse, warehouse_id) if warehouse_id else None
    party_coords = (
        (agency is not None and agency.latitude is not None and agency.longitude is not None)
        or (warehouse is not None and warehouse.latitude is not None and warehouse.longitude is not None)
    )
    labels = _party_label(agency, warehouse)
    location_text = (location or "").strip()
    address_text = (address or "").strip()

    if address_text and (looks_like_street_address(address_text) or not party_coords):
        coords = geocode_address(address_text)
        if coords:
            return coords[0], coords[1], address_text

    if location_text and location_text.lower() not in labels:
        if looks_like_street_address(location_text) or not party_coords:
            coords = geocode_address(location_text)
            if coords:
                return coords[0], coords[1], address_text or location_text

    if agency and agency.latitude is not None and agency.longitude is not None:
        return agency.latitude, agency.longitude, agency.address
    if warehouse and warehouse.latitude is not None and warehouse.longitude is not None:
        return warehouse.latitude, warehouse.longitude, warehouse.address
    return None, None, address


def apply_location_snapshot(db: Session, deployment: Deployment, asset: Asset, **kwargs) -> None:
    lat, lng, addr = resolve_deployment_snapshot(
        db,
        asset.organization_id,
        kwargs.get("agency_id"),
        kwargs.get("latitude"),
        kwargs.get("longitude"),
        kwargs.get("address"),
        kwargs.get("location"),
        kwargs.get("warehouse_id"),
    )
    deployment.latitude = lat
    deployment.longitude = lng
    deployment.address = addr


def resolve_agency_by_location(db: Session, organization_id: UUID, location: str | None) -> Agency | None:
    if not location or not organization_id:
        return None
    name = location.strip()
    if not name:
        return None
    agencies = db.scalars(
        select(Agency)
        .where(Agency.organization_id == organization_id)
        .where(Agency.is_archived.is_(False))
    ).all()
    lowered = name.lower()
    for agency in agencies:
        labels = [agency.name, agency.site_name]
        if agency.site_name:
            labels.append(f"{agency.name} - {agency.site_name}")
        if any(label and label.strip().lower() == lowered for label in labels):
            return agency
    return None


def get_asset_location(db: Session, asset: Asset) -> AssetLocation:
    """
    Determine the best available location for an asset using priority logic.
    
    Priority order:
    1. Genuine recent telematics GPS
    2. Latest geocoded address / deploy snapshot from the current status update
    3. In-transit last-known pin when no new address was stored
    4. Agency, then warehouse, then location string
    """
    now = datetime.now(UTC)
    in_transit = (
        asset.operational_status == AssetOperationalStatus.IN_TRANSIT
        or asset.current_custody_type == CustodyType.IN_TRANSIT
    )

    # Priority 1: genuine recent telematics GPS only — never workflow-copied pins
    gps_location = get_latest_telematics_gps(db, asset.id)
    if gps_location:
        timestamp = gps_location.location_timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        age = now - timestamp
        is_stale = age.total_seconds() / 3600 > GPS_STALE_THRESHOLD_HOURS
        if not is_stale:
            source = LocationSource.IN_TRANSIT if in_transit else LocationSource.GPS
            name = (
                f"In Transit via {asset.carrier_name}" if in_transit and asset.carrier_name
                else f"GPS - {gps_location.telematics_provider or 'Unknown Provider'}"
            )
            return AssetLocation(
                latitude=gps_location.latitude,
                longitude=gps_location.longitude,
                location_name=name,
                location_source=source,
                location_timestamp=gps_location.location_timestamp,
                is_stale=False,
                carrier=asset.carrier_name,
                tracking_code=asset.tracking_code,
            )

    active_deployment = get_active_deployment(db, asset.id)

    # In transit: new destination address if stored, otherwise last-known pin
    if in_transit:
        if (
            active_deployment
            and active_deployment.latitude is not None
            and active_deployment.longitude is not None
        ):
            pin = (active_deployment.latitude, active_deployment.longitude)
        else:
            pin = _last_known_coordinates(db, asset)
        return AssetLocation(
            latitude=pin[0],
            longitude=pin[1],
            location_name=f"In Transit via {asset.carrier_name}" if asset.carrier_name else (asset.current_location or "In Transit"),
            location_source=LocationSource.IN_TRANSIT,
            location_timestamp=asset.updated_at,
            is_stale=False,
            origin=None,
            destination=asset.current_location,
            carrier=asset.carrier_name,
            tracking_code=asset.tracking_code,
        )
    
    # Priority 2: latest status-update snapshot, then assigned agency
    deployed = (
        asset.operational_status == AssetOperationalStatus.DEPLOYED
        or asset.current_custody_type == CustodyType.CUSTOMER_AGENCY
        or (
            active_deployment is not None
            and active_deployment.custody_type == CustodyType.CUSTOMER_AGENCY
        )
    )
    if deployed:
        agency = db.get(Agency, asset.agency_id) if asset.agency_id else None
        if agency is None:
            agency = resolve_agency_by_location(
                db,
                asset.organization_id,
                (active_deployment.location if active_deployment else None) or asset.current_location,
            )
        snapshot = (
            active_deployment
            and active_deployment.latitude is not None
            and active_deployment.longitude is not None
        )
        agency_coords = agency and agency.latitude is not None and agency.longitude is not None
        from app.geocoding import looks_like_street_address

        field_site = looks_like_street_address(
            (active_deployment.address if active_deployment else None)
            or (active_deployment.location if active_deployment else None)
        )
        explicit_coords_only = snapshot and not (active_deployment.address or "").strip()
        snapshot_overrides_agency = snapshot and (
            not agency_coords
            or field_site
            or explicit_coords_only
        ) and (
            not agency_coords
            or abs(float(active_deployment.latitude) - float(agency.latitude)) > 1e-5
            or abs(float(active_deployment.longitude) - float(agency.longitude)) > 1e-5
        )
        if snapshot_overrides_agency:
            return AssetLocation(
                latitude=active_deployment.latitude,
                longitude=active_deployment.longitude,
                location_name=active_deployment.location,
                location_source=LocationSource.DEPLOYMENT,
                location_timestamp=active_deployment.started_at,
                is_stale=False,
                address=active_deployment.address,
            )
        if agency_coords:
            return AssetLocation(
                latitude=agency.latitude,
                longitude=agency.longitude,
                location_name=f"{agency.name}{f' - {agency.site_name}' if agency.site_name else ''}",
                location_source=LocationSource.CUSTOMER,
                location_timestamp=active_deployment.started_at if active_deployment else asset.updated_at,
                is_stale=False,
                address=agency.address,
                city=agency.city,
                state=agency.state,
            )
        if snapshot:
            return AssetLocation(
                latitude=active_deployment.latitude,
                longitude=active_deployment.longitude,
                location_name=active_deployment.location,
                location_source=LocationSource.DEPLOYMENT,
                location_timestamp=active_deployment.started_at,
                is_stale=False,
                address=active_deployment.address,
            )
        if active_deployment:
            return AssetLocation(
                latitude=None,
                longitude=None,
                location_name=active_deployment.location,
                location_source=LocationSource.DEPLOYMENT,
                location_timestamp=active_deployment.started_at,
                is_stale=False,
            )
    
    if (
        active_deployment
        and active_deployment.latitude is not None
        and active_deployment.longitude is not None
    ):
        return AssetLocation(
            latitude=active_deployment.latitude,
            longitude=active_deployment.longitude,
            location_name=active_deployment.location,
            location_source=LocationSource.DEPLOYMENT,
            location_timestamp=active_deployment.started_at,
            is_stale=False,
            address=active_deployment.address,
        )

    # Priority 3: Warehouse location
    if asset.warehouse_id:
        warehouse = db.get(Warehouse, asset.warehouse_id)
        if warehouse and warehouse.latitude and warehouse.longitude:
            return AssetLocation(
                latitude=warehouse.latitude,
                longitude=warehouse.longitude,
                location_name=warehouse.name,
                location_source=LocationSource.WAREHOUSE,
                location_timestamp=asset.updated_at,
                is_stale=False,
                address=warehouse.address,
                city=warehouse.city,
                state=warehouse.state,
            )
        elif warehouse:
            # Warehouse exists but no coordinates
            return AssetLocation(
                latitude=None,
                longitude=None,
                location_name=warehouse.name,
                location_source=LocationSource.WAREHOUSE,
                location_timestamp=asset.updated_at,
                is_stale=False,
                address=warehouse.address,
            )
    
    # Priority 4: Customer/Agency (if not handled by deployment)
    if asset.agency_id:
        agency = db.get(Agency, asset.agency_id)
        if agency and agency.latitude and agency.longitude:
            return AssetLocation(
                latitude=agency.latitude,
                longitude=agency.longitude,
                location_name=f"{agency.name}{f' - {agency.site_name}' if agency.site_name else ''}",
                location_source=LocationSource.CUSTOMER,
                location_timestamp=asset.updated_at,
                is_stale=False,
                address=agency.address,
                city=agency.city,
                state=agency.state,
            )
        elif agency:
            return AssetLocation(
                latitude=None,
                longitude=None,
                location_name=agency.name,
                location_source=LocationSource.CUSTOMER,
                location_timestamp=asset.updated_at,
                is_stale=False,
                address=agency.address,
            )
    
    # Priority 5: In-transit
    if asset.carrier_name:  # Simple in-transit indicator
        return AssetLocation(
            latitude=None,
            longitude=None,
            location_name=f"In Transit via {asset.carrier_name}",
            location_source=LocationSource.IN_TRANSIT,
            location_timestamp=asset.updated_at,
            is_stale=False,
            carrier=asset.carrier_name,
            tracking_code=asset.tracking_code,
        )
    
    # Priority 6: Current location string (fallback)
    if asset.current_location:
        return AssetLocation(
            latitude=None,
            longitude=None,
            location_name=asset.current_location,
            location_source=LocationSource.MANUAL,
            location_timestamp=asset.updated_at,
            is_stale=False,
        )
    
    # Priority 7: Unknown
    return AssetLocation(
        latitude=None,
        longitude=None,
        location_name="Unknown Location",
        location_source=LocationSource.UNKNOWN,
        location_timestamp=asset.updated_at,
        is_stale=False,
    )


def _last_known_coordinates(db: Session, asset: Asset) -> tuple[Decimal | None, Decimal | None]:
    gps = get_latest_gps_location(db, asset.id)
    if gps and gps.latitude is not None and gps.longitude is not None:
        return gps.latitude, gps.longitude
    if asset.agency_id:
        agency = db.get(Agency, asset.agency_id)
        if agency and agency.latitude and agency.longitude:
            return agency.latitude, agency.longitude
    if asset.warehouse_id:
        warehouse = db.get(Warehouse, asset.warehouse_id)
        if warehouse and warehouse.latitude and warehouse.longitude:
            return warehouse.latitude, warehouse.longitude
    return None, None


def get_latest_gps_location(db: Session, asset_id: UUID) -> GPSLocation | None:
    """Get the most recent GPS location for an asset."""
    return db.scalar(
        select(GPSLocation)
        .where(GPSLocation.asset_id == asset_id)
        .order_by(GPSLocation.location_timestamp.desc())
        .limit(1)
    )


def get_active_deployment(db: Session, asset_id: UUID) -> Deployment | None:
    """Get the currently active (not ended) deployment for an asset."""
    return db.scalar(
        select(Deployment)
        .where(Deployment.asset_id == asset_id)
        .where(Deployment.ended_at.is_(None))
        .order_by(Deployment.started_at.desc())
        .limit(1)
    )


def has_better_operational_location(asset: Asset) -> bool:
    """Check if asset has a better operational location than stale GPS."""
    # If asset has warehouse or agency assignment, prefer that over stale GPS
    return asset.warehouse_id is not None or asset.agency_id is not None


def get_asset_maintenance_summary(db: Session, asset_id: UUID) -> dict:
    """Get maintenance summary for asset marker popup."""
    open_wo_count = db.scalar(
        select(func.count(MaintenanceWorkOrder.id))
        .where(MaintenanceWorkOrder.asset_id == asset_id)
        .where(
            MaintenanceWorkOrder.status.in_([
                WorkOrderStatus.OPEN,
                WorkOrderStatus.INVESTIGATING,
                WorkOrderStatus.IN_PROGRESS,
                WorkOrderStatus.WAITING_PARTS,
                WorkOrderStatus.WAITING_VENDOR,
            ])
        )
    ) or 0
    
    return {
        "open_work_orders": open_wo_count,
    }
