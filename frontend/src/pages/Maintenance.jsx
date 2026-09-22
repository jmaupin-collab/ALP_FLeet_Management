import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { DataTable } from "../components/DataTable";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import {
  ASSET_TYPES,
  ISSUE_SOURCES,
  PRIORITIES,
  REPAIR_CHANNELS,
  WORK_ORDER_COLUMNS,
  WORK_ORDER_STATUSES,
} from "../lib/constants.js";
import { Badge, money, statusLabel } from "../lib/format.jsx";

const emptyFilters = {
  status: "",
  asset_type: "",
  asset_id: "",
  priority: "",
  component: "",
  assigned_to_id: "",
  location: "",
  vendor: "",
  repair_channel: "",
  opened_from: "",
  opened_to: "",
};

export default function Maintenance() {
  const navigate = useNavigate();
  const [view, setView] = useState("board");
  const [rows, setRows] = useState([]);
  const [assets, setAssets] = useState([]);
  const [users, setUsers] = useState([]);
  const [filters, setFilters] = useState(emptyFilters);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({});
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const query = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value) query.set(key, value);
      });
      const suffix = query.toString() ? `?${query}` : "";
      const [orders, fleet, people] = await Promise.all([
        api(`/work-orders${suffix}`), 
        api("/assets"), 
        api("/work-orders/users")
      ]);
      console.log("Loaded assets:", fleet); // Debug log
      setRows(orders);
      setAssets(fleet);
      setUsers(people);
    } catch (err) {
      console.error("Error loading maintenance data:", err); // Debug log
      setError(err.message);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [filters]);

  const byStatus = useMemo(() => {
    const map = Object.fromEntries(WORK_ORDER_COLUMNS.map((col) => [col.id, []]));
    rows.forEach((row) => {
      if (map[row.status]) map[row.status].push(row);
      else map.open.push(row);
    });
    return map;
  }, [rows]);

  async function setStatus(wo, status) {
    setBusy(true);
    try {
      await api(`/work-orders/${wo.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
      setSuccess(`Moved to ${statusLabel(status)}.`);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function onDrop(event, status) {
    event.preventDefault();
    const id = event.dataTransfer.getData("text/wo-id");
    const wo = rows.find((row) => row.id === id);
    if (wo && wo.status !== status) await setStatus(wo, status);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Maintenance</h2>
          <p className="mt-1 text-sm text-slate-600">
            Inspection failures and manual tickets in a Jira-style board. Status history is appended, never overwritten.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setView("board")}
            className={`rounded-lg px-3 py-2 text-sm font-semibold ${view === "board" ? "bg-slate-900 text-white" : "border border-slate-300 bg-white"}`}
          >
            Kanban
          </button>
          <button
            type="button"
            onClick={() => setView("table")}
            className={`rounded-lg px-3 py-2 text-sm font-semibold ${view === "table" ? "bg-slate-900 text-white" : "border border-slate-300 bg-white"}`}
          >
            Table
          </button>
          <button
            type="button"
            onClick={() => {
              setForm({
                asset_id: "",  // Don't pre-select, let user choose from dropdown
                title: "",
                description: "",
                issue_source: "Manual Report",
                priority: "medium",
                repair_channel: "Internal",
                failed_component: "",
              });
              setCreateOpen(true);
            }}
            className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white"
          >
            Create work order
          </button>
        </div>
      </div>

      <div className="grid gap-3 rounded-xl border border-slate-200 bg-white p-4 md:grid-cols-3 lg:grid-cols-5">
        <Field label="Status">
          <select className={inputClass} value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">All</option>
            {WORK_ORDER_STATUSES.map((status) => (
              <option key={status} value={status}>
                {statusLabel(status)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Asset type">
          <select className={inputClass} value={filters.asset_type} onChange={(e) => setFilters({ ...filters, asset_type: e.target.value })}>
            <option value="">All</option>
            {ASSET_TYPES.map((type) => (
              <option key={type}>{type}</option>
            ))}
          </select>
        </Field>
        <Field label="Asset ID">
          <select className={inputClass} value={filters.asset_id} onChange={(e) => setFilters({ ...filters, asset_id: e.target.value })}>
            <option value="">All</option>
            {assets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.make_model}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Priority">
          <select className={inputClass} value={filters.priority} onChange={(e) => setFilters({ ...filters, priority: e.target.value })}>
            <option value="">All</option>
            {PRIORITIES.map((priority) => (
              <option key={priority}>{priority}</option>
            ))}
          </select>
        </Field>
        <Field label="Subsystem">
          <input className={inputClass} value={filters.component} onChange={(e) => setFilters({ ...filters, component: e.target.value })} />
        </Field>
        <Field label="Technician">
          <select className={inputClass} value={filters.assigned_to_id} onChange={(e) => setFilters({ ...filters, assigned_to_id: e.target.value })}>
            <option value="">All</option>
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.full_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Location">
          <input className={inputClass} value={filters.location} onChange={(e) => setFilters({ ...filters, location: e.target.value })} />
        </Field>
        <Field label="Vendor">
          <input className={inputClass} value={filters.vendor} onChange={(e) => setFilters({ ...filters, vendor: e.target.value })} />
        </Field>
        <Field label="Repair source">
          <select className={inputClass} value={filters.repair_channel} onChange={(e) => setFilters({ ...filters, repair_channel: e.target.value })}>
            <option value="">All</option>
            {REPAIR_CHANNELS.map((channel) => (
              <option key={channel}>{channel}</option>
            ))}
          </select>
        </Field>
        <Field label="From">
          <input type="date" className={inputClass} value={filters.opened_from} onChange={(e) => setFilters({ ...filters, opened_from: e.target.value ? `${e.target.value}T00:00:00` : "" })} />
        </Field>
        <Field label="To">
          <input type="date" className={inputClass} value={filters.opened_to} onChange={(e) => setFilters({ ...filters, opened_to: e.target.value ? `${e.target.value}T23:59:59` : "" })} />
        </Field>
      </div>

      <Notice error={error} success={success} />
      {loading ? <p className="text-sm text-slate-600">Loading work orders…</p> : null}

      {view === "board" ? (
        <div className="grid gap-3 overflow-x-auto pb-4 lg:grid-cols-7">
          {WORK_ORDER_COLUMNS.map((col) => (
            <div
              key={col.id}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => onDrop(e, col.id)}
              className="min-h-[320px] rounded-xl border border-slate-200 bg-slate-50 p-2"
            >
              <p className="px-1 py-2 text-xs font-semibold uppercase tracking-wide text-slate-600">
                {col.label} · {byStatus[col.id].length}
              </p>
              <div className="space-y-2">
                {byStatus[col.id].map((wo) => (
                  <article
                    key={wo.id}
                    draggable
                    onDragStart={(e) => e.dataTransfer.setData("text/wo-id", wo.id)}
                    className="cursor-grab rounded-lg border border-slate-200 bg-white p-3 shadow-sm"
                  >
                    <Link to={`/maintenance/${wo.id}`} className="font-medium text-teal-800 hover:underline">
                      {wo.title}
                    </Link>
                    <p className="mt-1 text-xs text-slate-600">{wo.asset}</p>
                    <p className="mt-1 text-xs text-slate-500">{wo.failed_component || wo.issue_source}</p>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <Badge value={wo.priority} />
                      <Badge value={wo.issue_source} />
                    </div>
                    <select
                      disabled={busy}
                      className="mt-2 w-full rounded border border-slate-200 px-2 py-1 text-xs"
                      value={wo.status}
                      onChange={(e) => setStatus(wo, e.target.value)}
                    >
                      {WORK_ORDER_STATUSES.map((status) => (
                        <option key={status} value={status}>
                          {statusLabel(status)}
                        </option>
                      ))}
                    </select>
                  </article>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <DataTable
          columns={[
            {
              key: "title",
              header: "Work order",
              render: (r) => (
                <Link to={`/maintenance/${r.id}`} className="font-medium text-teal-800 hover:underline">
                  {r.title}
                </Link>
              ),
            },
            { key: "asset", header: "Asset", render: (r) => <Link to={`/assets/${r.asset_id}`}>{r.asset}</Link> },
            { key: "failed_component", header: "Subsystem", render: (r) => r.failed_component || "—" },
            { key: "status", header: "Status", render: (r) => <Badge value={r.status} /> },
            { key: "priority", header: "Priority", render: (r) => <Badge value={r.priority} /> },
            { key: "assigned_to", header: "Owner", render: (r) => r.assigned_to || "—" },
            { key: "repair_channel", header: "Source" },
            { key: "total_repair_cost", header: "Cost", render: (r) => money(r.total_repair_cost) },
            {
              key: "move",
              header: "Move",
              render: (r) => (
                <select className="rounded border px-2 py-1 text-xs" value={r.status} onChange={(e) => setStatus(r, e.target.value)}>
                  {WORK_ORDER_STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {statusLabel(status)}
                    </option>
                  ))}
                </select>
              ),
            },
          ]}
          rows={rows}
        />
      )}

      {createOpen ? (
        <Modal title="Create work order" onClose={() => setCreateOpen(false)}>
          <form
            className="space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                const created = await api("/work-orders", {
                  method: "POST",
                  body: JSON.stringify(form),
                });
                setSuccess("Work order created.");
                setCreateOpen(false);
                await load();
                navigate(`/maintenance/${created.id}`);
              } catch (err) {
                setError(err.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Asset ID">
              <select required className={inputClass} value={form.asset_id || ""} onChange={(e) => setForm({ ...form, asset_id: e.target.value })}>
                <option value="">Select an asset...</option>
                {assets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.vin} - {asset.make_model}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Title">
              <input required className={inputClass} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </Field>
            <Field label="Issue source">
              <select className={inputClass} value={form.issue_source} onChange={(e) => setForm({ ...form, issue_source: e.target.value })}>
                {ISSUE_SOURCES.map((source) => (
                  <option key={source}>{source}</option>
                ))}
              </select>
            </Field>
            <Field label="Priority">
              <select className={inputClass} value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                {PRIORITIES.map((priority) => (
                  <option key={priority}>{priority}</option>
                ))}
              </select>
            </Field>
            <Field label="Component">
              <input className={inputClass} value={form.failed_component} onChange={(e) => setForm({ ...form, failed_component: e.target.value })} />
            </Field>
            <Field label="Description">
              <textarea className={inputClass} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <button disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Create
            </button>
          </form>
        </Modal>
      ) : null}
    </div>
  );
}
