"""Turn a stream of GPS fixes into arrivals, departures, and alerts.

Geofences are not separate records to maintain: every warehouse and agency that
has coordinates is a circle around those coordinates, so a site is fenced the
moment it is geocoded and moving a site moves its fence.

Three things happen when a fix lands:

* arriving at a warehouse checks the unit in, through the same custody workflow
  a person uses, so the ledger cannot tell automatic moves from manual ones;
* arriving at an agency is recorded and announced but changes nothing, because
  being parked at a customer site is not the same as being handed over;
* leaving anywhere is recorded, and raises an alert when nothing on the asset
  explains the departure.

Every transition is written to GeofenceEvent whether or not it notifies, so the
history survives a missed alert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Agency,
    Asset,
    AssetZoneState,
    CustodyType,
    Deployment,
    DeploymentStatus,
    GeofenceEvent,
    GeofenceEventType,
    GeofenceZoneKind,
    Warehouse,
)
from app.notifications import notify_roles

logger = logging.getLogger("fleet.geofencing")

EARTH_RADIUS_M = 6_371_000


@dataclass(frozen=True)
class Zone:
    kind: GeofenceZoneKind
    id: UUID
    name: str
    latitude: Decimal
    longitude: Decimal
    radius_m: int


def distance_meters(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance. Exact enough at the scale of a yard fence."""
    lat1, lon1, lat2, lon2 = (radians(float(value)) for value in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def zones_for_organization(db: Session, organization_id: UUID) -> list[Zone]:
    """Every fenced site in one tenant.

    A site without coordinates has no fence rather than a fence at (0, 0), which
    is in the Atlantic and would swallow every unit that lost signal.
    """
    default_radius = get_settings().geofence_radius_meters
    zones: list[Zone] = []
    for model, kind in ((Warehouse, GeofenceZoneKind.WAREHOUSE), (Agency, GeofenceZoneKind.AGENCY)):
        rows = db.scalars(
            select(model)
            .where(model.organization_id == organization_id)
            .where(model.is_archived.is_(False))
        ).all()
        for row in rows:
            if row.latitude is None or row.longitude is None:
                continue
            zones.append(
                Zone(
                    kind=kind,
                    id=row.id,
                    name=row.name,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    radius_m=row.geofence_radius_m or default_radius,
                )
            )
    return zones


def containing_zone(zones: list[Zone], latitude, longitude) -> Zone | None:
    """The site a position is inside, nearest first when fences overlap."""
    inside = [
        (distance_meters(latitude, longitude, zone.latitude, zone.longitude), zone) for zone in zones
    ]
    inside = [(distance, zone) for distance, zone in inside if distance <= zone.radius_m]
    if not inside:
        return None
    return min(inside, key=lambda pair: pair[0])[1]


def departure_was_expected(db: Session, asset: Asset) -> bool:
    """True when something on the asset already accounts for it being on the move.

    Two signals: somebody recorded the unit as in transit, or a deployment is
    scheduled and waiting to start. Anything else means it moved without anyone
    writing it down, which is exactly the case worth an alert.
    """
    if asset.current_custody_type == CustodyType.IN_TRANSIT:
        return True
    scheduled = db.scalar(
        select(Deployment.id)
        .where(Deployment.asset_id == asset.id)
        .where(Deployment.ended_at.is_(None))
        .where(Deployment.status.in_([DeploymentStatus.SCHEDULED, DeploymentStatus.IN_TRANSIT]))
    )
    return scheduled is not None


def _state_for(db: Session, asset: Asset) -> AssetZoneState:
    state = db.get(AssetZoneState, asset.id)
    if state is None:
        state = AssetZoneState(asset_id=asset.id, organization_id=asset.organization_id)
        db.add(state)
    return state


def _record(
    db: Session,
    asset: Asset,
    zone_kind: GeofenceZoneKind,
    zone_id: UUID,
    zone_name: str,
    event_type: GeofenceEventType,
    latitude,
    longitude,
    occurred_at: datetime,
    *,
    was_unexpected: bool = False,
    action_taken: str | None = None,
) -> GeofenceEvent:
    event = GeofenceEvent(
        organization_id=asset.organization_id,
        asset_id=asset.id,
        event_type=event_type,
        zone_kind=zone_kind,
        zone_id=zone_id,
        zone_name=zone_name,
        latitude=Decimal(str(latitude)),
        longitude=Decimal(str(longitude)),
        occurred_at=occurred_at,
        was_unexpected=was_unexpected,
        action_taken=action_taken,
    )
    db.add(event)
    return event


def _check_in_at_warehouse(db: Session, asset: Asset, zone: Zone) -> str | None:
    """Hand the arrival to the ordinary custody workflow.

    Deliberately calls apply_custody rather than writing the asset row directly:
    it closes the open deployment, opens the new one, and reconciles status. A
    second implementation here would drift from the one people use.
    """
    settings = get_settings()
    if not settings.geofence_auto_checkin:
        return None
    if asset.current_custody_type == CustodyType.WAREHOUSE_DEPOT and asset.warehouse_id == zone.id:
        return None  # Already checked in here; nothing to change.

    from app.schemas import CustodyUpdate
    from app.services import apply_custody

    try:
        apply_custody(
            db,
            asset,
            CustodyUpdate(
                custody_type=CustodyType.WAREHOUSE_DEPOT,
                location=zone.name,
                warehouse_id=zone.id,
                notes=f"Automatic check-in: tracker reported arrival at {zone.name}.",
            ),
            None,
        )
    except Exception:
        # An arrival that cannot be applied is still an arrival worth recording
        # and announcing, so the alert is not lost with the status change.
        logger.exception("Automatic check-in failed for asset %s at %s", asset.vin, zone.name)
        return None
    return f"Checked in to {zone.name}"


def evaluate_fix(
    db: Session,
    asset: Asset,
    latitude,
    longitude,
    occurred_at: datetime | None = None,
) -> list[GeofenceEvent]:
    """Compare one position against the asset's last known zone.

    Returns the transitions this fix caused, which is usually none: a unit
    parked in the same yard reports its position all day without anything
    happening. The caller commits.
    """
    occurred_at = occurred_at or datetime.now(UTC)
    zones = zones_for_organization(db, asset.organization_id)
    now_zone = containing_zone(zones, latitude, longitude)
    state = _state_for(db, asset)
    state.last_fix_at = occurred_at

    same_zone = (
        now_zone is not None
        and state.zone_id == now_zone.id
        and state.zone_kind == now_zone.kind
    )
    if same_zone or (now_zone is None and state.zone_id is None):
        return []

    events: list[GeofenceEvent] = []

    if state.zone_id is not None and state.zone_kind is not None:
        unexpected = not departure_was_expected(db, asset)
        events.append(
            _record(
                db,
                asset,
                state.zone_kind,
                state.zone_id,
                state.zone_name or "a site",
                GeofenceEventType.EXITED,
                latitude,
                longitude,
                occurred_at,
                was_unexpected=unexpected,
            )
        )
        if unexpected:
            _announce_departure(db, asset, state.zone_name or "a site")

    if now_zone is not None:
        action = (
            _check_in_at_warehouse(db, asset, now_zone)
            if now_zone.kind is GeofenceZoneKind.WAREHOUSE
            else None
        )
        events.append(
            _record(
                db,
                asset,
                now_zone.kind,
                now_zone.id,
                now_zone.name,
                GeofenceEventType.ENTERED,
                latitude,
                longitude,
                occurred_at,
                action_taken=action,
            )
        )
        _announce_arrival(db, asset, now_zone, action)

    state.zone_kind = now_zone.kind if now_zone else None
    state.zone_id = now_zone.id if now_zone else None
    state.zone_name = now_zone.name if now_zone else None
    state.entered_at = occurred_at if now_zone else None
    return events


def _announce_departure(db: Session, asset: Asset, zone_name: str) -> None:
    notify_roles(
        db,
        asset.organization_id,
        kind="geofence_exit",
        title=f"{asset.vin} left {zone_name}",
        body=(
            f"The tracker on {asset.vin} reported it leaving {zone_name}, and nothing scheduled "
            "accounts for the move. Check whether this was expected."
        ),
        link_path=f"/assets/{asset.id}",
    )


def _announce_arrival(db: Session, asset: Asset, zone: Zone, action: str | None) -> None:
    body = f"The tracker on {asset.vin} reported it arriving at {zone.name}."
    if action:
        body += f" {action} automatically."
    elif zone.kind is GeofenceZoneKind.AGENCY:
        # Said out loud because it is the surprising part: the unit is sitting at
        # a customer site without anyone having handed it over.
        body += " Custody was not changed; confirm the handover if this is a deployment."
    notify_roles(
        db,
        asset.organization_id,
        kind="geofence_entry",
        title=f"{asset.vin} arrived at {zone.name}",
        body=body,
        link_path=f"/assets/{asset.id}",
    )
