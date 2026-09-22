import csv
import io
from uuid import uuid4

from fastapi import Response

from app.schemas import AnalyticsHub


def analytics_csv(hub: AnalyticsHub, table: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    name = table.strip().lower()

    if name == "failures":
        writer.writerow(["asset_type_filter", "component", "fail_count", "inspected_count", "failure_rate_pct"])
        for row in hub.failures:
            writer.writerow([hub.asset_type, row.component, row.fail_count, row.inspected_count, row.failure_rate_pct])
    elif name in {"stock", "stock-alerts", "stock_alerts"}:
        writer.writerow(
            ["asset_type_filter", "component", "prior_30d_failures", "last_30d_failures", "spike_pct", "recommendation"]
        )
        for row in hub.stock_alerts:
            writer.writerow(
                [
                    hub.asset_type,
                    row.component,
                    row.prior_30d_failures,
                    row.last_30d_failures,
                    row.spike_pct,
                    row.recommendation,
                ]
            )
    elif name in {"efficiency", "vendor-efficiency"}:
        writer.writerow(
            ["asset_type_filter", "channel", "work_order_count", "closed_jobs", "avg_turnaround_hours", "avg_invoice_total"]
        )
        for row in hub.efficiency:
            writer.writerow(
                [
                    hub.asset_type,
                    row.channel,
                    row.work_order_count,
                    row.closed_jobs,
                    row.avg_turnaround_hours,
                    row.avg_invoice_total,
                ]
            )
    elif name in {"root-causes", "root_causes"}:
        writer.writerow(["asset_type_filter", "category", "count"])
        for row in hub.root_causes:
            writer.writerow([hub.asset_type, row.get("category"), row.get("count")])
    elif name in {"repeats", "repeat-failures"}:
        writer.writerow(["asset_type_filter", "asset_id", "component", "count"])
        for row in hub.repeat_failures:
            writer.writerow([hub.asset_type, row.get("asset_id"), row.get("component"), row.get("count")])
    else:
        writer.writerow(["error"])
        writer.writerow([f"Unknown table '{table}'. Use failures, stock-alerts, or efficiency."])

    filename = f"fleet-{name}-{hub.asset_type.replace(' ', '-').lower()}-{uuid4().hex[:8]}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
