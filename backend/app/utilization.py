"""Lifecycle-based asset utilization. Telematics hooks are reserved, not faked."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Asset,
    AssetOperationalStatus,
    CustodyType,
    Deployment,
    DeploymentStatus,
    MaintenanceWorkOrder,
    MeterReading,
    WorkOrderStatus,
)

DEPLOYED_DEP_STATUSES = {
    DeploymentStatus.DEPLOYED,
    DeploymentStatus.ACTIVE,
}
TRANSIT_DEP_STATUSES = {DeploymentStatus.IN_TRANSIT}
AVAILABLE_DEP_STATUSES = {
    DeploymentStatus.STORED,
    DeploymentStatus.IDLE,
    DeploymentStatus.RETURNED,
    DeploymentStatus.COMPLETED,
    DeploymentStatus.CANCELLED,
    DeploymentStatus.STAGED,
    DeploymentStatus.SCHEDULED,
}
OPEN_WO = {
    WorkOrderStatus.OPEN,
    WorkOrderStatus.INVESTIGATING,
    WorkOrderStatus.IN_PROGRESS,
    WorkOrderStatus.WAITING_PARTS,
    WorkOrderStatus.WAITING_VENDOR,
}

LOW_UTILIZATION_PCT = Decimal("25")
HIGH_DOWNTIME_PCT = Decimal("20")
HIGH_DOWNTIME_MIN_DAYS = 3
UNUSED_DAYS_FLAG = 30


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _state_from_deployment(dep: Deployment) -> str:
    if dep.custody_type == CustodyType.IN_TRANSIT or dep.status in TRANSIT_DEP_STATUSES:
        return "in_transit"
    if dep.custody_type == CustodyType.CUSTOMER_AGENCY or dep.status in DEPLOYED_DEP_STATUSES:
        return "deployed"
    if dep.custody_type == CustodyType.WAREHOUSE_DEPOT or dep.status in AVAILABLE_DEP_STATUSES:
        return "available"
    return "available"


def _state_from_operational(status: AssetOperationalStatus | None) -> str:
    if status == AssetOperationalStatus.DEPLOYED:
        return "deployed"
    if status == AssetOperationalStatus.IN_TRANSIT:
        return "in_transit"
    if status == AssetOperationalStatus.MAINTENANCE:
        return "maintenance"
    if status in {AssetOperationalStatus.RETIRED, AssetOperationalStatus.OUT_OF_SERVICE}:
        return "retired"
    return "available"


@dataclass
class WindowUtilization:
    window_days: int
    eligible_days: float
    deployed_days: float
    available_days: float
    maintenance_days: float
    in_transit_days: float
    retired_days: float
    utilization_pct: Decimal
    flags: list[str] = field(default_factory=list)


@dataclass
class AssetUtilization:
    asset_id: UUID
    vin: str
    make_model: str
    source: str
    consecutive_unused_days: int
    current_state: str
    windows: dict[int, WindowUtilization]
    telematics: dict
    flags: list[str]


def _clip(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> tuple[datetime, datetime] | None:
    lo = max(start, window_start)
    hi = min(end, window_end)
    if hi <= lo:
        return None
    return lo, hi


def _add_seconds(bucket: dict[str, float], state: str, seconds: float) -> None:
    bucket[state] = bucket.get(state, 0.0) + max(0.0, seconds)


def _maintenance_span(wo: MaintenanceWorkOrder, now: datetime) -> tuple[datetime, datetime] | None:
    """Count recorded downtime hours only — ignore work-order calendar spans."""
    hours = float(wo.downtime_hours or 0)
    if hours <= 0:
        return None
    start = _as_utc(wo.downtime_start) or _as_utc(wo.opened_at)
    if start is None:
        return None
    end = start + timedelta(hours=hours)
    explicit_end = _as_utc(wo.downtime_end)
    if explicit_end and explicit_end > start:
        end = min(end, explicit_end)
    if end > now:
        end = now
    if end <= start:
        return None
    return (start, end)


def _build_segments(asset: Asset, now: datetime) -> list[tuple[datetime, datetime, str]]:
    """Build non-overlapping status segments. Maintenance overlays deployments."""
    created = _as_utc(asset.created_at) or now
    retired_at = None
    if asset.operational_status in {AssetOperationalStatus.RETIRED, AssetOperationalStatus.OUT_OF_SERVICE} or asset.is_archived:
        retired_at = _as_utc(asset.archived_at) or _as_utc(asset.updated_at) or now

    deployments = sorted(asset.deployments, key=lambda row: _as_utc(row.started_at) or created)
    earliest = created
    if deployments:
        first_start = _as_utc(deployments[0].started_at)
        if first_start:
            earliest = min(created, first_start)
    raw: list[tuple[datetime, datetime, str]] = []
    cursor = earliest
    for dep in deployments:
        start = _as_utc(dep.started_at) or cursor
        end = _as_utc(dep.ended_at) or now
        if start > cursor:
            raw.append((cursor, start, "available"))
        raw.append((start, end, _state_from_deployment(dep)))
        cursor = max(cursor, end)
    if cursor < now:
        raw.append((cursor, now, _state_from_operational(asset.operational_status)))
    if not raw:
        raw.append((earliest, now, _state_from_operational(asset.operational_status)))

    maintenance_spans: list[tuple[datetime, datetime]] = []
    for wo in asset.work_orders:
        span = _maintenance_span(wo, now)
        if span:
            maintenance_spans.append(span)

    painted: list[tuple[datetime, datetime, str]] = []
    for start, end, state in raw:
        if end <= start:
            continue
        cuts = {start, end}
        for m0, m1 in maintenance_spans:
            if m1 <= start or m0 >= end:
                continue
            cuts.add(max(m0, start))
            cuts.add(min(m1, end))
        points = sorted(cuts)
        for i in range(len(points) - 1):
            a, b = points[i], points[i + 1]
            if b <= a:
                continue
            overlay = any(m0 < b and m1 > a for m0, m1 in maintenance_spans)
            painted.append((a, b, "maintenance" if overlay else state))

    if retired_at:
        clipped: list[tuple[datetime, datetime, str]] = []
        for start, end, state in painted:
            if end <= retired_at:
                clipped.append((start, end, state))
            elif start >= retired_at:
                clipped.append((start, end, "retired"))
            else:
                clipped.append((start, retired_at, state))
                clipped.append((retired_at, end, "retired"))
        painted = clipped
    return painted


def _window_stats(segments: list[tuple[datetime, datetime, str]], window_days: int, now: datetime) -> WindowUtilization:
    window_start = now - timedelta(days=window_days)
    bucket = {"deployed": 0.0, "available": 0.0, "maintenance": 0.0, "in_transit": 0.0, "retired": 0.0}
    for start, end, state in segments:
        clipped = _clip(start, end, window_start, now)
        if not clipped:
            continue
        a, b = clipped
        _add_seconds(bucket, state if state in bucket else "available", (b - a).total_seconds())

    day = 86400.0
    deployed = bucket["deployed"] / day
    available = bucket["available"] / day
    maintenance = bucket["maintenance"] / day
    transit = bucket["in_transit"] / day
    retired = bucket["retired"] / day
    eligible = deployed + available + maintenance + transit
    pct = Decimal("0")
    if eligible > 0:
        pct = (Decimal(str(round(deployed / eligible * 100, 1)))).quantize(Decimal("0.1"))
    flags: list[str] = []
    if window_days == 90 and eligible >= 7 and pct < LOW_UTILIZATION_PCT:
        flags.append("Low Utilization")
    downtime_pct = Decimal(str(round(maintenance / eligible * 100, 1))) if eligible > 0 else Decimal("0")
    if (
        window_days == 90
        and eligible >= 7
        and downtime_pct >= HIGH_DOWNTIME_PCT
        and maintenance >= HIGH_DOWNTIME_MIN_DAYS
    ):
        flags.append("High Downtime")
    return WindowUtilization(
        window_days=window_days,
        eligible_days=round(eligible, 2),
        deployed_days=round(deployed, 2),
        available_days=round(available, 2),
        maintenance_days=round(maintenance, 2),
        in_transit_days=round(transit, 2),
        retired_days=round(retired, 2),
        utilization_pct=pct,
        flags=flags,
    )


def _consecutive_unused(segments: list[tuple[datetime, datetime, str]], now: datetime) -> tuple[int, str]:
    current = segments[-1][2] if segments else "available"
    if current in {"deployed", "in_transit", "retired"}:
        return 0, current
    unused = 0.0
    for start, end, state in reversed(segments):
        if state in {"available", "maintenance"}:
            unused += (end - start).total_seconds()
        else:
            break
    return int(unused / 86400), current


def compute_asset_utilization(asset: Asset, now: datetime | None = None) -> AssetUtilization:
    now = _as_utc(now) or datetime.now(UTC)
    segments = _build_segments(asset, now)
    unused, current = _consecutive_unused(segments, now)
    windows = {days: _window_stats(segments, days, now) for days in (30, 90, 365)}
    flags: list[str] = []
    for window in windows.values():
        flags.extend(window.flags)
    if unused >= UNUSED_DAYS_FLAG and current == "available":
        flags.append("Available > 30 Days")
    # de-dupe preserving order
    seen: set[str] = set()
    unique_flags: list[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            unique_flags.append(flag)
    latest_meter = None
    readings = list(getattr(asset, "meter_readings", None) or [])
    if readings:
        latest_meter = max(readings, key=lambda row: _as_utc(row.reading_date) or now)
    return AssetUtilization(
        asset_id=asset.id,
        vin=asset.vin,
        make_model=asset.make_model,
        source="lifecycle_history",
        consecutive_unused_days=unused,
        current_state=current,
        windows=windows,
        telematics={
            "provider": asset.telematics_provider,
            "odometer_miles": str(latest_meter.odometer_miles) if latest_meter and latest_meter.odometer_miles is not None else None,
            "engine_hours": str(latest_meter.engine_hours) if latest_meter and latest_meter.engine_hours is not None else None,
            "trips": None,
            "gps_movement": None,
        },
        flags=unique_flags,
    )


def utilization_to_dict(result: AssetUtilization) -> dict:
    windows = {}
    for days, window in result.windows.items():
        windows[str(days)] = {
            "window_days": window.window_days,
            "eligible_days": window.eligible_days,
            "deployed_days": window.deployed_days,
            "available_days": window.available_days,
            "maintenance_days": window.maintenance_days,
            "in_transit_days": window.in_transit_days,
            "retired_days": window.retired_days,
            "utilization_pct": window.utilization_pct,
            "flags": window.flags,
        }
    return {
        "asset_id": result.asset_id,
        "vin": result.vin,
        "make_model": result.make_model,
        "source": result.source,
        "consecutive_unused_days": result.consecutive_unused_days,
        "current_state": result.current_state,
        "windows": windows,
        "telematics": result.telematics,
        "flags": result.flags,
        "utilization_30d": result.windows[30].utilization_pct,
        "utilization_90d": result.windows[90].utilization_pct,
        "utilization_365d": result.windows[365].utilization_pct,
        "deployed_days_90d": result.windows[90].deployed_days,
        "available_days_90d": result.windows[90].available_days,
        "maintenance_days_90d": result.windows[90].maintenance_days,
        "in_transit_days_90d": result.windows[90].in_transit_days,
    }


def load_asset_for_utilization(db: Session, asset_id: UUID) -> Asset | None:
    return db.scalar(
        select(Asset)
        .options(
            selectinload(Asset.deployments),
            selectinload(Asset.work_orders),
            selectinload(Asset.meter_readings),
        )
        .where(Asset.id == asset_id)
    )


from fastapi import APIRouter, Depends
from app.deps import get_current_user, require_reporter
from app.database import get_db
from app.models import User
from app.rbac import require_asset_access

router = APIRouter(tags=["utilization"])


@router.get("/assets/{asset_id}/utilization")
def get_asset_utilization(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_asset_access(db, current_user, asset_id)
    asset = load_asset_for_utilization(db, asset_id)
    return utilization_to_dict(compute_asset_utilization(asset))


@router.get("/analytics/utilization")
def get_fleet_utilization(
    current_user: User = Depends(require_reporter),
    db: Session = Depends(get_db),
) -> list[dict]:
    return fleet_utilization(db, current_user.organization_id)


def fleet_utilization(db: Session, organization_id: UUID) -> list[dict]:
    assets = db.scalars(
        select(Asset)
        .options(
            selectinload(Asset.deployments),
            selectinload(Asset.work_orders),
            selectinload(Asset.meter_readings),
        )
        .where(Asset.organization_id == organization_id)
        .where(Asset.is_archived.is_(False))
    ).all()
    rows = []
    for asset in assets:
        if asset.operational_status == AssetOperationalStatus.RETIRED:
            continue
        rows.append(utilization_to_dict(compute_asset_utilization(asset)))
    return rows
