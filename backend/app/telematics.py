"""Provider-neutral ingest for GPS positions.

Geotab, a Cube tracker, or a five-line script can all POST here; nothing in this
module knows which. Vendor-specific adapters belong in front of this endpoint,
translating their payload into these fields, so adding a provider never means
touching geofencing or asset lifecycle code.

Authentication is a shared key in a header rather than a user session, because
the caller is a machine. Without a configured key the endpoint refuses every
request: silently accepting anonymous writes to asset location would be worse
than not having the feature.
"""

from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user, require_fleet_admin
from app.geofencing import evaluate_fix
from app.models import Asset, GeofenceEvent, GPSLocation, User

logger = logging.getLogger("fleet.telematics")

router = APIRouter(tags=["telematics"])

# A single call carries one device's recent history or one sweep across the
# fleet. Capped so a malformed poller cannot post a million rows in one request.
MAX_FIXES_PER_CALL = 500


class PositionFix(BaseModel):
    """One position report.

    The asset is addressed either by its Fleet Command id or by the device id
    recorded on the asset, so a provider that only knows its own serial numbers
    never has to be taught ours.
    """

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID | None = None
    device_id: str | None = Field(default=None, max_length=255)
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)
    recorded_at: datetime | None = None
    provider: str | None = Field(default=None, max_length=64)
    heading: Decimal | None = Field(default=None, ge=0, le=360)
    speed: Decimal | None = Field(default=None, ge=0)
    accuracy: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def needs_something_to_match_on(self):
        if self.asset_id is None and not (self.device_id or "").strip():
            raise ValueError("Each fix needs an asset_id or a device_id.")
        return self

    @field_validator("recorded_at")
    @classmethod
    def must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        # A naive timestamp is ambiguous and would land in the wrong hour; treat
        # it as UTC rather than guessing the sender's offset.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class PositionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixes: list[PositionFix] = Field(min_length=1, max_length=MAX_FIXES_PER_CALL)


def require_ingest_key(x_api_key: str | None = Header(default=None)) -> None:
    """Shared-key auth for machine callers."""
    settings = get_settings()
    if not settings.telematics_ingest_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telematics ingest is not configured.",
        )
    # Constant-time compare so a wrong key cannot be discovered a byte at a time.
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.telematics_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def _find_asset(db: Session, fix: PositionFix) -> Asset | None:
    if fix.asset_id is not None:
        return db.get(Asset, fix.asset_id)
    return db.scalar(
        select(Asset).where(Asset.telematics_device_id == fix.device_id.strip())
    )


class TrackingUpdate(BaseModel):
    """Enroll one asset in position tracking, or take it back out."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    device_id: str | None = Field(default=None, max_length=255)
    provider: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def enrolling_needs_a_device(self):
        # Without a device id there is nothing for the provider to be asked
        # about, so enabling would quietly do nothing.
        if self.enabled and not (self.device_id or "").strip():
            raise ValueError("Turning on tracking needs the device id from the provider.")
        return self


@router.post("/telematics/locations", dependencies=[Depends(require_ingest_key)])
def ingest_positions(payload: PositionBatch, db: Session = Depends(get_db)) -> dict:
    """Record positions and act on any geofence crossings they reveal.

    Only assets somebody enrolled are stored. A provider account covers a whole
    company, and this fleet is a slice of it, so a position for anything else is
    discarded here rather than kept on the chance it is wanted later.

    One unmatched or archived asset does not fail the batch: a tracker moved to
    a different unit would otherwise block every other position in the same
    call. Unmatched device ids come back in the response so they can be fixed.
    """
    accepted = 0
    unmatched: list[str] = []
    not_enrolled: list[str] = []
    transitions: list[dict] = []

    for fix in payload.fixes:
        asset = _find_asset(db, fix)
        if asset is None or asset.is_archived:
            unmatched.append(fix.device_id or str(fix.asset_id))
            continue
        if not asset.telematics_tracking_enabled:
            # Reported separately from unmatched: this device is known, it was
            # simply never asked for, which is a choice rather than a mistake.
            not_enrolled.append(fix.device_id or str(fix.asset_id))
            continue

        recorded_at = fix.recorded_at or datetime.now(UTC)
        db.add(
            GPSLocation(
                asset_id=asset.id,
                telematics_provider=fix.provider or asset.telematics_provider or "unknown",
                telematics_device_id=fix.device_id or asset.telematics_device_id,
                latitude=fix.latitude,
                longitude=fix.longitude,
                heading=fix.heading,
                speed=fix.speed,
                accuracy=fix.accuracy,
                location_timestamp=recorded_at,
            )
        )
        accepted += 1

        for event in evaluate_fix(db, asset, fix.latitude, fix.longitude, recorded_at):
            transitions.append(
                {
                    "asset": asset.vin,
                    "event": event.event_type.value,
                    "zone": event.zone_name,
                    "unexpected": event.was_unexpected,
                    "action": event.action_taken,
                }
            )

    db.commit()
    if unmatched:
        logger.warning("Telematics ingest could not match %d device(s): %s", len(unmatched), unmatched)
    if not_enrolled:
        # Info, not a warning: a poller sweeping the whole account is expected to
        # send some of these, and nothing needs fixing.
        logger.info("Telematics ingest skipped %d device(s) not enrolled.", len(not_enrolled))
    return {
        "accepted": accepted,
        "unmatched": unmatched,
        "not_enrolled": not_enrolled,
        "transitions": transitions,
    }


@router.get("/telematics/tracked-devices", dependencies=[Depends(require_ingest_key)])
def list_tracked_devices(db: Session = Depends(get_db)) -> dict:
    """The device ids a poller should ask the provider about, and nothing else.

    Lets the pull stay narrow at the source: rather than fetching every vehicle
    on the Geotab or Cube account and throwing most of it away, the poller reads
    this list first and requests only what is on it. The ingest endpoint
    enforces the same rule regardless, so a stale poller cannot widen the scope.
    """
    rows = db.execute(
        select(Asset.id, Asset.vin, Asset.telematics_device_id, Asset.telematics_provider)
        .where(Asset.telematics_tracking_enabled.is_(True))
        .where(Asset.is_archived.is_(False))
        .where(Asset.telematics_device_id.is_not(None))
        .order_by(Asset.vin)
    ).all()
    return {
        "devices": [
            {
                "asset_id": row.id,
                "vin": row.vin,
                "device_id": row.telematics_device_id,
                "provider": row.telematics_provider,
            }
            for row in rows
        ]
    }


@router.put("/assets/{asset_id}/tracking")
def set_asset_tracking(
    asset_id: UUID,
    payload: TrackingUpdate,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Choose whether one asset is tracked, and under which device id."""
    from app.rbac import require_asset_access

    asset = require_asset_access(db, current_user, asset_id)
    device_id = (payload.device_id or "").strip() or None

    if device_id:
        # Two assets sharing a device id would make every fix ambiguous, and the
        # ingest lookup would silently pick one of them.
        clash = db.scalar(
            select(Asset.vin)
            .where(Asset.telematics_device_id == device_id)
            .where(Asset.id != asset.id)
        )
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Device {device_id} is already assigned to asset {clash}.",
            )

    asset.telematics_device_id = device_id
    if payload.provider is not None:
        asset.telematics_provider = payload.provider.strip() or None
    asset.telematics_tracking_enabled = payload.enabled
    asset.updated_by_id = current_user.id
    db.commit()
    db.refresh(asset)
    return {
        "asset_id": asset.id,
        "tracking_enabled": asset.telematics_tracking_enabled,
        "device_id": asset.telematics_device_id,
        "provider": asset.telematics_provider,
    }


@router.get("/assets/{asset_id}/tracking")
def get_asset_tracking(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Current tracking enrollment for one asset."""
    from app.rbac import require_asset_access

    asset = require_asset_access(db, current_user, asset_id)
    return {
        "asset_id": asset_id,
        "tracking_enabled": asset.telematics_tracking_enabled,
        "device_id": asset.telematics_device_id,
        "provider": asset.telematics_provider,
    }


@router.get("/assets/{asset_id}/geofence-events")
def list_geofence_events(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Arrival and departure history for one asset."""
    from app.rbac import require_asset_access

    require_asset_access(db, current_user, asset_id)
    rows = db.scalars(
        select(GeofenceEvent)
        .where(GeofenceEvent.asset_id == asset_id)
        .order_by(GeofenceEvent.occurred_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "id": row.id,
            "event_type": row.event_type.value,
            "zone_kind": row.zone_kind.value,
            "zone_name": row.zone_name,
            "occurred_at": row.occurred_at,
            "was_unexpected": row.was_unexpected,
            "action_taken": row.action_taken,
            "latitude": row.latitude,
            "longitude": row.longitude,
        }
        for row in rows
    ]


@router.get("/geofences")
def list_geofences(
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Every fenced site, so the map can draw them and gaps are visible."""
    from app.geofencing import zones_for_organization

    return [
        {
            "kind": zone.kind.value,
            "id": zone.id,
            "name": zone.name,
            "latitude": zone.latitude,
            "longitude": zone.longitude,
            "radius_m": zone.radius_m,
        }
        for zone in zones_for_organization(db, current_user.organization_id)
    ]
