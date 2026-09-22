import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { DataTable } from "../components/DataTable";
import { Notice } from "../components/Modal.jsx";
import { api, downloadCsv } from "../lib/api.js";
import { ASSET_TYPES } from "../lib/constants.js";
import { hours, money } from "../lib/format.jsx";

const EMPTY = {
  asset_type: "All",
  failures: [],
  stock_alerts: [],
  efficiency: [],
  root_causes: [],
  repeat_failures: [],
  deployment_stats: [],
  avg_repair_cost: 0,
  avg_turnaround_hours: 0,
  avg_downtime_hours: 0,
  total_downtime_hours: 0,
  total_uptime_hours: 0,
  uptime_percentage: 0,
  completed_work_orders: 0,
  open_work_orders: 0,
  total_deployments: 0,
  active_deployments: 0,
  avg_deployment_duration_days: 0,
  maintenance_frequency_days: 0,
};

export default function Analytics() {
  const [assetType, setAssetType] = useState("");
  const [chartType, setChartType] = useState("bar");
  const [deploymentChartType, setDeploymentChartType] = useState("bar");
  const [hub, setHub] = useState(EMPTY);
  const [utilization, setUtilization] = useState([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [draggedItem, setDraggedItem] = useState(null);
  
  // Default section order
  const defaultOrder = [
    "fleet-metrics",
    "failures-chart", 
    "deployment-chart",
    "stock-alerts",
    "efficiency-table",
    "root-causes",
    "repeat-failures",
    "utilization"
  ];
  
  const [sectionOrder, setSectionOrder] = useState(() => {
    const saved = localStorage.getItem("analytics-section-order");
    return saved ? JSON.parse(saved) : defaultOrder;
  });

  useEffect(() => {
    const query = assetType ? `?asset_type=${encodeURIComponent(assetType)}` : "";
    setLoading(true);
    Promise.all([api(`/analytics${query}`), api("/analytics/utilization").catch(() => [])])
      .then(([data, util]) => {
        setHub(data);
        setUtilization(util);
        setError("");
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [assetType]);

  const handleDragStart = (e, sectionId) => {
    setDraggedItem(sectionId);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const handleDrop = (e, targetId) => {
    e.preventDefault();
    if (!draggedItem || draggedItem === targetId) return;

    const newOrder = [...sectionOrder];
    const draggedIndex = newOrder.indexOf(draggedItem);
    const targetIndex = newOrder.indexOf(targetId);

    newOrder.splice(draggedIndex, 1);
    newOrder.splice(targetIndex, 0, draggedItem);

    setSectionOrder(newOrder);
    localStorage.setItem("analytics-section-order", JSON.stringify(newOrder));
    setDraggedItem(null);
  };

  const resetOrder = () => {
    setSectionOrder(defaultOrder);
    localStorage.setItem("analytics-section-order", JSON.stringify(defaultOrder));
    setNotice("Section order reset to default");
    setTimeout(() => setNotice(""), 3000);
  };

  async function exportTable(table) {
    const query = new URLSearchParams({ table });
    if (assetType) query.set("asset_type", assetType);
    try {
      await downloadCsv(`/analytics/export?${query}`, `fleet-${table}.csv`);
      setNotice(`Downloaded ${table} CSV.`);
    } catch (err) {
      setNotice(err.message);
    }
  }

  const DraggableSection = ({ id, children }) => (
    <div
      draggable
      onDragStart={(e) => handleDragStart(e, id)}
      onDragOver={handleDragOver}
      onDrop={(e) => handleDrop(e, id)}
      className={`cursor-move rounded-xl border-2 transition-all ${
        draggedItem === id 
          ? "border-teal-500 bg-teal-50 opacity-50" 
          : "border-transparent hover:border-slate-300"
      }`}
    >
      <div className="flex items-center gap-2 px-2 py-1 text-slate-400 hover:text-slate-600">
        <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8h16M4 16h16" />
        </svg>
        <span className="text-xs font-medium">Drag to reorder</span>
      </div>
      {children}
    </div>
  );

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Analytics hub</h2>
          <p className="mt-1 text-sm text-slate-600">
            Failure mix, safety-stock spikes, and in-house vs vendor efficiency — filterable by asset class.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <select
            value={assetType}
            onChange={(e) => setAssetType(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            <option value="">All asset types</option>
            {ASSET_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => exportTable("failures")} className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white">
            Export failures CSV
          </button>
          <button type="button" onClick={() => exportTable("stock-alerts")} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold">
            Export stock CSV
          </button>
          <button type="button" onClick={() => exportTable("efficiency")} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold">
            Export efficiency CSV
          </button>
          <button type="button" onClick={() => exportTable("root-causes")} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold">
            Export root causes CSV
          </button>
          <button type="button" onClick={() => exportTable("repeats")} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold">
            Export repeats CSV
          </button>
          <button type="button" onClick={resetOrder} className="rounded-lg border-2 border-teal-600 bg-teal-50 px-3 py-2 text-sm font-semibold text-teal-700 hover:bg-teal-100">
            Reset Section Order
          </button>
        </div>
      </div>
      {loading ? <p className="text-sm text-slate-600">Loading analytics…</p> : null}
      <Notice error={error} success={notice} />
      
      <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
        <strong>💡 Tip:</strong> Drag and drop sections by their headers to reorder them. Your layout will be saved automatically.
      </div>

      {sectionOrder.map(sectionId => {
        const sections = {
          "fleet-metrics": (
            <DraggableSection key="fleet-metrics" id="fleet-metrics">
              <section>
        <h3 className="mb-4 text-sm font-semibold text-slate-800">Fleet Performance Metrics</h3>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Uptime Percentage</p>
            <p className="mt-1 text-2xl font-bold text-teal-700">{Number(hub.uptime_percentage || 0).toFixed(1)}%</p>
            <p className="text-xs text-slate-500">{hours(hub.total_uptime_hours)} uptime</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Total Downtime</p>
            <p className="mt-1 text-2xl font-bold text-red-600">{hours(hub.total_downtime_hours)}</p>
            <p className="text-xs text-slate-500">Avg {hours(hub.avg_downtime_hours)} per incident</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Avg Repair Cost</p>
            <p className="mt-1 text-2xl font-bold text-slate-900">{money(hub.avg_repair_cost)}</p>
            <p className="text-xs text-slate-500">{hub.completed_work_orders} completed WOs</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Avg Turnaround</p>
            <p className="mt-1 text-2xl font-bold text-slate-900">{hours(hub.avg_turnaround_hours)}</p>
            <p className="text-xs text-slate-500">Work order completion time</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Active Deployments</p>
            <p className="mt-1 text-2xl font-bold text-cyan-700">{hub.active_deployments || 0}</p>
            <p className="text-xs text-slate-500">{hub.total_deployments || 0} total deployments</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Avg Deployment Duration</p>
            <p className="mt-1 text-2xl font-bold text-slate-900">{Number(hub.avg_deployment_duration_days || 0).toFixed(0)} days</p>
            <p className="text-xs text-slate-500">Completed deployments</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Open Work Orders</p>
            <p className="mt-1 text-2xl font-bold text-amber-600">{hub.open_work_orders || 0}</p>
            <p className="text-xs text-slate-500">Currently in maintenance</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Maintenance Frequency</p>
            <p className="mt-1 text-2xl font-bold text-slate-900">Every {Number(hub.maintenance_frequency_days || 0).toFixed(0)} days</p>
            <p className="text-xs text-slate-500">Average time between WOs</p>
          </div>
        </div>
      </section>
            </DraggableSection>
          ),
          "failures-chart": (
            <DraggableSection key="failures-chart" id="failures-chart">
              <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-800">Most common failures</h3>
            <p className="mt-1 text-xs text-slate-500">Subsystem failure counts used to decide which parts to stock.</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setChartType("bar")}
              className={`rounded-lg px-3 py-1 text-xs font-semibold ${
                chartType === "bar" ? "bg-teal-700 text-white" : "border border-slate-300 bg-white text-slate-700"
              }`}
            >
              Bar
            </button>
            <button
              onClick={() => setChartType("line")}
              className={`rounded-lg px-3 py-1 text-xs font-semibold ${
                chartType === "line" ? "bg-teal-700 text-white" : "border border-slate-300 bg-white text-slate-700"
              }`}
            >
              Line
            </button>
            <button
              onClick={() => setChartType("scatter")}
              className={`rounded-lg px-3 py-1 text-xs font-semibold ${
                chartType === "scatter" ? "bg-teal-700 text-white" : "border border-slate-300 bg-white text-slate-700"
              }`}
            >
              Scatter
            </button>
          </div>
        </div>
        <div className="mt-4 h-80">
          <ResponsiveContainer width="100%" height="100%">
            {chartType === "bar" && (
              <BarChart data={hub.failures} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="component" tick={{ fontSize: 12 }} interval={0} />
                <YAxis allowDecimals={false} />
                <Tooltip
                  formatter={(value, name, props) => {
                    if (name === "Failures") {
                      return [`${value} (rate ${props.payload.failure_rate_pct}%)`, name];
                    }
                    return [value, name];
                  }}
                />
                <Bar dataKey="fail_count" name="Failures" fill="#0f766e" radius={[6, 6, 0, 0]} isAnimationActive={false} />
              </BarChart>
            )}
            {chartType === "line" && (
              <LineChart data={hub.failures} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="component" tick={{ fontSize: 12 }} interval={0} />
                <YAxis allowDecimals={false} />
                <Tooltip
                  formatter={(value, name, props) => {
                    if (name === "Failures") {
                      return [`${value} (rate ${props.payload.failure_rate_pct}%)`, name];
                    }
                    return [value, name];
                  }}
                />
                <Line type="monotone" dataKey="fail_count" name="Failures" stroke="#0f766e" strokeWidth={2} dot={{ fill: "#0f766e", r: 4 }} isAnimationActive={false} />
              </LineChart>
            )}
            {chartType === "scatter" && (
              <ScatterChart data={hub.failures} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="component" tick={{ fontSize: 12 }} interval={0} />
                <YAxis dataKey="fail_count" allowDecimals={false} />
                <ZAxis range={[60, 400]} />
                <Tooltip
                  formatter={(value, name, props) => {
                    if (name === "fail_count") {
                      return [`${value} (rate ${props.payload.failure_rate_pct}%)`, "Failures"];
                    }
                    return [value, name];
                  }}
                  cursor={{ strokeDasharray: "3 3" }}
                />
                <Scatter name="Failures" dataKey="fail_count" fill="#0f766e" isAnimationActive={false} />
              </ScatterChart>
            )}
          </ResponsiveContainer>
        </div>
      </section>
            </DraggableSection>
          ),
          "deployment-chart": (
            <DraggableSection key="deployment-chart" id="deployment-chart">
              <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-800">Deployment Activity (Last 12 Months)</h3>
            <p className="mt-1 text-xs text-slate-500">Monthly deployment counts and fleet utilization percentage.</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setDeploymentChartType("bar")}
              className={`rounded-lg px-3 py-1 text-xs font-semibold ${
                deploymentChartType === "bar" ? "bg-teal-700 text-white" : "border border-slate-300 bg-white text-slate-700"
              }`}
            >
              Bar
            </button>
            <button
              onClick={() => setDeploymentChartType("line")}
              className={`rounded-lg px-3 py-1 text-xs font-semibold ${
                deploymentChartType === "line" ? "bg-teal-700 text-white" : "border border-slate-300 bg-white text-slate-700"
              }`}
            >
              Line
            </button>
          </div>
        </div>
        <div className="mt-4 h-80">
          <ResponsiveContainer width="100%" height="100%">
            {deploymentChartType === "bar" && (
              <BarChart data={hub.deployment_stats} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis yAxisId="left" allowDecimals={false} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 100]} />
                <Tooltip
                  formatter={(value, name) => {
                    if (name === "Deployments") return [value, name];
                    if (name === "Utilization %") return [`${value}%`, name];
                    return [value, name];
                  }}
                />
                <Bar yAxisId="left" dataKey="deployment_count" name="Deployments" fill="#0d9488" radius={[6, 6, 0, 0]} isAnimationActive={false} />
                <Bar yAxisId="right" dataKey="deployment_percentage" name="Utilization %" fill="#06b6d4" radius={[6, 6, 0, 0]} isAnimationActive={false} />
              </BarChart>
            )}
            {deploymentChartType === "line" && (
              <LineChart data={hub.deployment_stats} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis yAxisId="left" allowDecimals={false} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 100]} />
                <Tooltip
                  formatter={(value, name) => {
                    if (name === "Deployments") return [value, name];
                    if (name === "Utilization %") return [`${value}%`, name];
                    return [value, name];
                  }}
                />
                <Line yAxisId="left" type="monotone" dataKey="deployment_count" name="Deployments" stroke="#0d9488" strokeWidth={2} dot={{ fill: "#0d9488", r: 4 }} isAnimationActive={false} />
                <Line yAxisId="right" type="monotone" dataKey="deployment_percentage" name="Utilization %" stroke="#06b6d4" strokeWidth={2} dot={{ fill: "#06b6d4", r: 4 }} isAnimationActive={false} />
              </LineChart>
            )}
          </ResponsiveContainer>
        </div>
      </section>
            </DraggableSection>
          ),
          "stock-alerts": (
            <DraggableSection key="stock-alerts" id="stock-alerts">
              <section className="space-y-3">
        <h3 className="text-sm font-semibold text-slate-800">Supply chain stock recommendation</h3>
        {hub.stock_alerts.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
            No subsystem has spiked more than 20% over the last 30 days.
          </p>
        ) : (
          hub.stock_alerts.map((alert) => (
            <div key={alert.component} className="rounded-xl border-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-950 shadow-sm">
              <p className="font-bold">Safety stock alert · {alert.component}</p>
              <p className="mt-1">{alert.recommendation}</p>
              <p className="mt-1 font-mono text-xs">
                Prior 30d: {alert.prior_30d_failures} · Last 30d: {alert.last_30d_failures} · Spike {alert.spike_pct}%
              </p>
            </div>
          ))
        )}
      </section>
            </DraggableSection>
          ),
          "efficiency-table": (
            <DraggableSection key="efficiency-table" id="efficiency-table">
              <section>
        <h3 className="mb-3 text-sm font-semibold text-slate-800">Internal repairs vs third-party vendor efficiency</h3>
        <DataTable
          columns={[
            { key: "channel", header: "Channel" },
            { key: "work_order_count", header: "Work orders" },
            { key: "closed_jobs", header: "Closed jobs" },
            { key: "avg_turnaround_hours", header: "Avg turnaround", render: (r) => hours(r.avg_turnaround_hours) },
            { key: "avg_invoice_total", header: "Avg invoice", render: (r) => money(r.avg_invoice_total) },
          ]}
          rows={hub.efficiency.map((row) => ({ ...row, id: row.channel }))}
        />
        <p className="mt-2 text-xs text-slate-500">
          Faster, lower-cost channels should stay in-house. High invoice + long turnaround is a candidate to keep outsourcing — or to bring back in-house if vendors are the bottleneck.
        </p>
      </section>
            </DraggableSection>
          ),
          "root-causes": (
            <DraggableSection key="root-causes" id="root-causes">
              <section>
        <h3 className="mb-3 text-sm font-semibold text-slate-800">Root cause frequency</h3>
        <DataTable
          columns={[
            { key: "category", header: "Category" },
            { key: "count", header: "Count" },
          ]}
          rows={(hub.root_causes || []).map((row) => ({ ...row, id: row.category }))}
        />
      </section>
            </DraggableSection>
          ),
          "repeat-failures": (
            <DraggableSection key="repeat-failures" id="repeat-failures">
              <section>
        <h3 className="mb-3 text-sm font-semibold text-slate-800">Repeat failures</h3>
        <DataTable
          columns={[
            { key: "asset_id", header: "Asset" },
            { key: "component", header: "Subsystem" },
            { key: "count", header: "Work orders" },
          ]}
          rows={(hub.repeat_failures || []).map((row) => ({ ...row, id: `${row.asset_id}-${row.component}` }))}
        />
      </section>
            </DraggableSection>
          ),
          utilization: (
            <DraggableSection key="utilization" id="utilization">
              <section>
        <h3 className="mb-3 text-sm font-semibold text-slate-800">Asset utilization (lifecycle history)</h3>
        <DataTable
          columns={[
            { key: "make_model", header: "Asset" },
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
      </section>
            </DraggableSection>
          )
        };
        return sections[sectionId];
      })}
    </div>
  );
}
