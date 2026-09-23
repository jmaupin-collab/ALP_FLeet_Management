import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { DataTable } from "../components/DataTable";
import { Notice } from "../components/Modal.jsx";
import { api, displayApiError } from "../lib/api.js";
import { Badge, hours, money } from "../lib/format.jsx";

const emptyKpis = {
  fleet_size: 0,
  deployed: 0,
  available: 0,
  in_transit: 0,
  in_maintenance: 0,
  out_of_service: 0,
  retired: 0,
  total_purchase_cost: 0,
  downtime_hours_ytd: 0,
  asset_type_scope: "ALPR Trailer",
  by_asset_type: [],
  non_alpr_inventory: [],
};

export default function Dashboard() {
  const [kpis, setKpis] = useState(emptyKpis);
  const [failures, setFailures] = useState([]);
  const [utilization, setUtilization] = useState([]);
  const [attention, setAttention] = useState({ critical: 0, warning: 0, info: 0, total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadData = () => {
    Promise.all([
      api("/dashboard/kpis"),
      api("/analytics"),
      api("/attention/summary").catch(() => null),
      api("/analytics/utilization").catch(() => []),
    ])
      .then(([kpiData, analytics, counts, util]) => {
        setKpis(kpiData);
        setFailures(analytics.failures?.slice(0, 6) || []);
        if (counts) setAttention(counts);
        setUtilization(util || []);
        setError("");
      })
      .catch((err) => setError(displayApiError(err)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
    
    // Auto-refresh every 30 seconds
    const interval = setInterval(loadData, 30000);
    
    return () => clearInterval(interval);
  }, []);

  const cards = [
    { label: "Fleet size", value: kpis.fleet_size },
    { label: "Currently deployed", value: kpis.deployed },
    { label: "Available", value: kpis.available },
    { label: "In transit", value: kpis.in_transit },
    { label: "In maintenance", value: kpis.in_maintenance },
    { label: "Out of service", value: kpis.out_of_service },
    { label: "Retired", value: kpis.retired },
    { label: "Asset book value", value: money(kpis.total_purchase_cost) },
    { label: "Recorded WO downtime", value: hours(kpis.downtime_hours_ytd) },
  ];

  const scope = kpis.asset_type_scope || "ALPR Trailer";
  const nonAlpr = kpis.non_alpr_inventory || [];

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">Dashboard</h2>
        <p className="mt-1 text-sm text-slate-600">
          Readiness counts cover {scope}s only. Other asset types are listed under Other fleet inventory below.
        </p>
      </div>
      {loading ? <p className="text-sm text-slate-600">Loading dashboard…</p> : null}
      <Notice error={error} />
      <Link to="/attention" className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm hover:border-teal-300">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Attention Center</p>
            <p className="mt-1 text-sm text-slate-700">{attention.total} open items</p>
          </div>
          <div className="flex flex-wrap gap-3 text-sm">
            <span className="font-semibold text-red-700">{attention.critical} critical</span>
            <span className="font-semibold text-amber-700">{attention.warning} warning</span>
            <span className="font-semibold text-sky-700">{attention.info} info</span>
          </div>
        </div>
      </Link>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {cards.map((card) => (
          <div key={card.label} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{card.label}</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{card.value}</p>
          </div>
        ))}
      </div>
      {failures.length > 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-800">Failure mix (top subsystems)</h3>
          <div className="mt-4 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={failures}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="component" tick={{ fontSize: 12 }} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="fail_count" name="Failures" fill="#0f766e" radius={[6, 6, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}
      <div>
        <h3 className="mb-3 text-sm font-semibold text-slate-800">{scope} metrics by asset type</h3>
        <DataTable
          columns={[
            { key: "asset_type", header: "Asset type", render: (r) => <Badge value={r.asset_type} tone="type" /> },
            { key: "total_assets", header: "Units" },
            { key: "deployed", header: "Deployed / in transit" },
            { key: "in_maintenance", header: "In maintenance" },
            { key: "idle_or_stored", header: "Idle / stored" },
            { key: "total_purchase_cost", header: "Purchase cost", render: (r) => money(r.total_purchase_cost) },
            { key: "downtime_hours_ytd", header: "Recorded WO downtime", render: (r) => hours(r.downtime_hours_ytd) },
          ]}
          rows={kpis.by_asset_type.map((row) => ({ ...row, id: row.asset_type }))}
          empty="No KPI rows yet."
        />
      </div>
      <div>
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-slate-800">Other fleet inventory</h3>
          <p className="mt-1 text-xs text-slate-500">
            Semi Trucks and Fleet Vehicles are tracked as inventory and are deliberately left out of the {scope}
            {" "}readiness counts above.
          </p>
        </div>
        <DataTable
          columns={[
            { key: "asset_type", header: "Asset type", render: (r) => <Badge value={r.asset_type} tone="type" /> },
            { key: "total_assets", header: "Units" },
            { key: "in_service", header: "In service" },
            { key: "out_of_service", header: "Out of service" },
            { key: "retired", header: "Retired" },
            { key: "total_purchase_cost", header: "Purchase cost", render: (r) => money(r.total_purchase_cost) },
          ]}
          rows={nonAlpr.map((row) => ({ ...row, id: row.asset_type }))}
          empty="No non-ALPR assets on record."
        />
      </div>
      <div>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-800">Asset utilization (lifecycle history)</h3>
            <p className="mt-1 text-xs text-slate-500">
              Utilization % and High Downtime come from 90-day lifecycle segments (eligible ≥ 7 days, downtime ≥ 20% and ≥ 3 days).
              The KPI card is lifetime recorded work-order downtime hours — a different metric.
            </p>
          </div>
          <Link to="/analytics" className="text-xs font-semibold text-teal-800 hover:underline">
            Open analytics
          </Link>
        </div>
        <DataTable
          columns={[
            {
              key: "vin",
              header: "Asset ID",
              render: (r) => (
                <Link className="font-mono text-xs text-teal-800 hover:underline" to={`/assets/${r.asset_id}`}>
                  {r.vin}
                </Link>
              ),
            },
            { key: "utilization_30d", header: "30-day", render: (r) => `${Number(r.utilization_30d || 0).toFixed(1)}%` },
            { key: "utilization_90d", header: "90-day", render: (r) => `${Number(r.utilization_90d || 0).toFixed(1)}%` },
            { key: "utilization_365d", header: "365-day", render: (r) => `${Number(r.utilization_365d || 0).toFixed(1)}%` },
            { key: "deployed_days_90d", header: "Deployed 90d" },
            { key: "available_days_90d", header: "Available 90d" },
            { key: "maintenance_days_90d", header: "Downtime 90d" },
            { key: "consecutive_unused_days", header: "Unused days" },
            { key: "flags", header: "Flags", render: (r) => (r.flags || []).join(", ") || "—" },
          ]}
          rows={utilization.map((row) => ({ ...row, id: row.asset_id }))}
          empty="No utilization rows yet."
        />
      </div>
    </div>
  );
}
