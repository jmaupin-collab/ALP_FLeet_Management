from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Asset, AssetType, Inspection, InspectionItem, InspectionResult, MaintenanceWorkOrder, RepairChannel
from app.schemas import (
    AnalyticsHub,
    EfficiencyRow,
    FailureBar,
    StockAlert,
)

def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


SPIKE_THRESHOLD = Decimal("0.20")


def _filter_type(asset_type: AssetType | None, row_type: AssetType) -> bool:
    return asset_type is None or row_type == asset_type


def _turnaround_hours(wo: MaintenanceWorkOrder) -> Decimal | None:
    if wo.closed_at is None:
        return None
    opened = _as_utc(wo.opened_at)
    closed = _as_utc(wo.closed_at)
    hours = (closed - opened).total_seconds() / 3600
    if hours < 0:
        hours = abs(hours)
    return Decimal(str(round(hours, 2)))


def build_analytics(db: Session, asset_type: AssetType | None = None, organization_id: UUID | None = None) -> AnalyticsHub:
    now = datetime.now(UTC)
    window_start = now - timedelta(days=30)
    prior_start = now - timedelta(days=60)

    # CRITICAL: Filter by organization for multi-tenant security
    work_orders_query = select(MaintenanceWorkOrder).options(selectinload(MaintenanceWorkOrder.asset))
    if organization_id is not None:
        work_orders_query = work_orders_query.where(MaintenanceWorkOrder.organization_id == organization_id)
    work_orders = db.scalars(work_orders_query).all()
    
    # Filter inspection items by organization through their inspection
    items_query = select(InspectionItem).options(selectinload(InspectionItem.inspection))
    items_query = items_query.join(Inspection, InspectionItem.inspection_id == Inspection.id)
    if organization_id is not None:
        items_query = items_query.where(Inspection.organization_id == organization_id)
    items = db.scalars(items_query).all()
    
    # Filter assets by organization
    assets_query = select(Asset)
    if organization_id is not None:
        assets_query = assets_query.where(Asset.organization_id == organization_id)
    asset_by_id = {asset.id: asset for asset in db.scalars(assets_query).all()}

    fail_events: list[tuple[str, AssetType, datetime]] = []
    inspect_counts: dict[str, int] = defaultdict(int)

    for item in items:
        inspection = item.inspection
        if inspection is None:
            continue
        asset = asset_by_id.get(inspection.asset_id)
        if asset is None or asset.is_archived or not _filter_type(asset_type, asset.asset_type):
            continue
        inspect_counts[item.component] += 1
        if item.result == InspectionResult.FAIL:
            fail_events.append((item.component, asset.asset_type, _as_utc(inspection.started_at)))

    for wo in work_orders:
        if wo.asset is None or wo.asset.is_archived or not _filter_type(asset_type, wo.asset.asset_type):
            continue
        if wo.failed_component:
            fail_events.append((wo.failed_component, wo.asset.asset_type, _as_utc(wo.opened_at)))

    totals: dict[str, int] = defaultdict(int)
    recent: dict[str, int] = defaultdict(int)
    prior: dict[str, int] = defaultdict(int)
    for component, _atype, when in fail_events:
        totals[component] += 1
        if when >= window_start:
            recent[component] += 1
        elif when >= prior_start:
            prior[component] += 1

    bars: list[FailureBar] = []
    for component, count in sorted(totals.items(), key=lambda pair: pair[1], reverse=True):
        inspected = inspect_counts.get(component, 0)
        rate = (Decimal(count) / Decimal(inspected) * 100) if inspected else Decimal(count)
        bars.append(
            FailureBar(
                component=component,
                fail_count=count,
                inspected_count=inspected,
                failure_rate_pct=rate.quantize(Decimal("0.1")),
            )
        )

    alerts: list[StockAlert] = []
    components = set(recent) | set(prior) | set(totals)
    for component in sorted(components):
        last = recent[component]
        before = prior[component]
        if before == 0:
            continue
        delta = (Decimal(last) - Decimal(before)) / Decimal(before)
        if delta > SPIKE_THRESHOLD and last > before:
            pct = (delta * 100).quantize(Decimal("0.1"))
            alerts.append(
                StockAlert(
                    component=component,
                    prior_30d_failures=before,
                    last_30d_failures=last,
                    spike_pct=pct,
                    recommendation=(
                        f"Failure rate for {component} rose {pct}% versus the prior 30 days "
                        f"({before} → {last}). Increase local warehouse safety stock for {component}."
                    ),
                )
            )

    internal_hours: list[Decimal] = []
    vendor_hours: list[Decimal] = []
    internal_invoices: list[Decimal] = []
    vendor_invoices: list[Decimal] = []
    internal_n = vendor_n = 0
    repair_costs: list[Decimal] = []
    turnaround: list[Decimal] = []
    downtimes: list[Decimal] = []
    root_tally: dict[str, int] = defaultdict(int)
    repeats_map: dict[tuple[str, str], int] = defaultdict(int)
    for wo in work_orders:
        if wo.asset is None or wo.asset.is_archived or not _filter_type(asset_type, wo.asset.asset_type):
            continue
        invoice = Decimal(wo.labor_cost or 0) + Decimal(wo.parts_cost or 0) + Decimal(getattr(wo, "vendor_invoice_cost", 0) or 0)
        hours = _turnaround_hours(wo)
        if wo.failed_component:
            repeats_map[(str(wo.asset_id), wo.failed_component)] += 1
        if wo.root_cause_category:
            category = wo.root_cause_category
            root_tally[category.value if hasattr(category, "value") else str(category)] += 1
        if wo.closed_at is not None:
            repair_costs.append(invoice)
            downtimes.append(Decimal(wo.downtime_hours or 0))
            if hours is not None:
                turnaround.append(hours)
        channel = wo.repair_channel or RepairChannel.INTERNAL
        if channel == RepairChannel.THIRD_PARTY:
            vendor_n += 1
            vendor_invoices.append(invoice)
            if hours is not None:
                vendor_hours.append(hours)
        else:
            internal_n += 1
            internal_invoices.append(invoice)
            if hours is not None:
                internal_hours.append(hours)

    def avg(values: list[Decimal]) -> Decimal:
        if not values:
            return Decimal("0")
        return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(Decimal("0.01"))

    efficiency = [
        EfficiencyRow(
            channel=RepairChannel.INTERNAL.value,
            work_order_count=internal_n,
            avg_turnaround_hours=avg(internal_hours),
            avg_invoice_total=avg(internal_invoices),
            closed_jobs=len(internal_hours),
        ),
        EfficiencyRow(
            channel=RepairChannel.THIRD_PARTY.value,
            work_order_count=vendor_n,
            avg_turnaround_hours=avg(vendor_hours),
            avg_invoice_total=avg(vendor_invoices),
            closed_jobs=len(vendor_hours),
        ),
    ]

    root_causes = [
        {"category": name, "count": count} for name, count in sorted(root_tally.items(), key=lambda pair: pair[1], reverse=True)
    ]
    repeats = [
        {"asset_id": asset_id, "component": component, "count": count}
        for (asset_id, component), count in repeats_map.items()
        if count > 1
    ]
    repeats.sort(key=lambda row: row["count"], reverse=True)

    # Calculate deployment statistics by month (last 12 months)
    from app.models import Deployment
    from app.schemas import DeploymentStats
    
    deployments = db.scalars(select(Deployment)).all()
    monthly_deployments: dict[str, int] = defaultdict(int)
    total_assets = len(asset_by_id)
    
    # Group deployments by month
    for dep in deployments:
        if dep.started_at:
            asset = asset_by_id.get(dep.asset_id)
            if asset and not asset.is_archived and _filter_type(asset_type, asset.asset_type):
                month_key = dep.started_at.strftime("%Y-%m")
                monthly_deployments[month_key] += 1
    
    # Build deployment stats for last 12 months
    deployment_stats = []
    for i in range(12):
        month_date = now - timedelta(days=30 * i)
        month_key = month_date.strftime("%Y-%m")
        count = monthly_deployments.get(month_key, 0)
        percentage = (Decimal(count) / Decimal(total_assets) * 100) if total_assets > 0 else Decimal("0")
        deployment_stats.append(
            DeploymentStats(
                month=month_date.strftime("%b %Y"),
                deployment_count=count,
                deployment_percentage=percentage.quantize(Decimal("0.1"))
            )
        )
    deployment_stats.reverse()  # Show oldest to newest

    # Calculate additional metrics
    total_downtime = sum(downtimes, Decimal("0"))
    
    # Calculate uptime (total operational time - downtime)
    # Assume 24/7 operation for simplicity, adjust based on tracking period
    days_tracked = 365  # Last year
    total_possible_hours = Decimal(days_tracked * 24 * len(asset_by_id)) if len(asset_by_id) > 0 else Decimal("1")
    total_uptime = total_possible_hours - total_downtime
    uptime_pct = (total_uptime / total_possible_hours * 100) if total_possible_hours > 0 else Decimal("0")
    
    # Calculate deployment metrics
    active_deps = sum(1 for d in deployments if d.ended_at is None)
    completed_deps = [d for d in deployments if d.ended_at is not None]
    deployment_durations = []
    for dep in completed_deps:
        if dep.started_at and dep.ended_at:
            duration_days = (dep.ended_at - dep.started_at).days
            deployment_durations.append(Decimal(duration_days))
    avg_deployment_days = avg(deployment_durations)
    
    # Calculate maintenance frequency (days between work orders)
    open_wos = sum(1 for wo in work_orders if wo.status.value == "open")
    if len(work_orders) > 1:
        sorted_wos = sorted([wo for wo in work_orders if wo.opened_at], key=lambda w: w.opened_at)
        if len(sorted_wos) > 1:
            time_span = (sorted_wos[-1].opened_at - sorted_wos[0].opened_at).days
            maintenance_freq = Decimal(time_span) / Decimal(len(sorted_wos)) if len(sorted_wos) > 0 else Decimal("0")
        else:
            maintenance_freq = Decimal("0")
    else:
        maintenance_freq = Decimal("0")

    return AnalyticsHub(
        asset_type=asset_type.value if asset_type else "All",
        failures=bars,
        stock_alerts=alerts,
        efficiency=efficiency,
        root_causes=root_causes,
        repeat_failures=repeats,
        deployment_stats=deployment_stats,
        avg_repair_cost=avg(repair_costs),
        avg_turnaround_hours=avg(turnaround),
        avg_downtime_hours=avg(downtimes),
        total_downtime_hours=total_downtime,
        total_uptime_hours=total_uptime,
        uptime_percentage=uptime_pct.quantize(Decimal("0.1")),
        completed_work_orders=len(repair_costs),
        open_work_orders=open_wos,
        total_deployments=len(deployments),
        active_deployments=active_deps,
        avg_deployment_duration_days=avg_deployment_days,
        maintenance_frequency_days=maintenance_freq.quantize(Decimal("0.1")),
    )
