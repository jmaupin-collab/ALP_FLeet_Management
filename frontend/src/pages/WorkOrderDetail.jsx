import { useEffect, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { Field, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import {
  ISSUE_SOURCES,
  PRIORITIES,
  REPAIR_CHANNELS,
  ROOT_CAUSE_CATEGORIES,
  WORK_ORDER_STATUSES,
} from "../lib/constants.js";
import { Badge, hours, money, statusLabel } from "../lib/format.jsx";
import { MANAGERS, hasRole } from "../lib/roles.js";

export default function WorkOrderDetail() {
  const { woId } = useParams();
  const navigate = useNavigate();
  const [wo, setWo] = useState(null);
  const [users, setUsers] = useState([]);
  const [parts, setParts] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [partForm, setPartForm] = useState({ part_id: "", quantity: "1", allow_negative: false });
  const [form, setForm] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [me, setMe] = useState(null);
  const canManageInventory = hasRole(me, MANAGERS);

  async function load() {
    setLoading(true);
    try {
      const [detail, people, used, stock, user] = await Promise.all([
        api(`/work-orders/${woId}`),
        api("/work-orders/users").catch(() => api("/users").catch(() => [])),
        api(`/work-orders/${woId}/parts`).catch(() => []),
        api("/parts").catch(() => []),
        api("/auth/me").catch(() => null),
      ]);
      setWo(detail);
      setUsers(people);
      setParts(used);
      setCatalog(stock);
      setMe(user);
      setForm({
        ...detail,
        assigned_to_id: detail.assigned_to_id || "",
        labor_cost: detail.labor_cost,
        parts_cost: detail.parts_cost,
        vendor_invoice_cost: detail.vendor_invoice_cost,
        labor_hours: detail.labor_hours,
        downtime_hours: detail.downtime_hours,
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [woId]);

  function patchPayload() {
    return {
      title: form.title,
      description: form.description,
      status: form.status,
      priority: form.priority,
      issue_source: form.issue_source,
      failed_component: form.failed_component || null,
      assigned_to_id: form.assigned_to_id || null,
      investigation_notes: form.investigation_notes,
      repair_actions: form.repair_actions,
      parts_used: form.parts_used,
      vendor_name: form.vendor_name,
      repair_channel: form.repair_channel,
      labor_cost: Number(form.labor_cost || 0),
      parts_cost: Number(form.parts_cost || 0),
      vendor_invoice_cost: Number(form.vendor_invoice_cost || 0),
      labor_hours: Number(form.labor_hours || 0),
      downtime_hours: Number(form.downtime_hours || 0),
      downtime_start: form.downtime_start || null,
      downtime_end: form.downtime_end || null,
      root_cause_category: form.root_cause_category || null,
      root_cause_description: form.root_cause_description,
      corrective_action: form.corrective_action,
      preventive_action: form.preventive_action,
      completion_notes: form.completion_notes,
    };
  }

  async function save(extra = {}) {
    setBusy(true);
    setError("");
    try {
      const updated = await api(`/work-orders/${woId}`, {
        method: "PATCH",
        body: JSON.stringify({ ...patchPayload(), ...extra }),
      });
      setWo(updated);
      setForm({ ...updated, assigned_to_id: updated.assigned_to_id || "" });
      setSuccess("Work order saved.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function complete() {
    if (!form.root_cause_category) {
      setError("Select a root cause category before completing. Use Unknown if it is still under review.");
      return;
    }
    try {
      setBusy(true);
      setError("");
      await api(`/work-orders/${woId}`, {
        method: "PATCH",
        body: JSON.stringify({ ...patchPayload(), status: "completed", completion_notes: form.completion_notes }),
      });
      // Navigate back to maintenance dashboard after completion
      navigate("/maintenance");
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  if (loading) return <p className="text-sm text-slate-600">Loading work order…</p>;
  if (!wo) {
    return (
      <div>
        <p>{error || "Work order not found."}</p>
        <Link to="/maintenance" className="text-sm text-teal-800 hover:underline">
          Back to maintenance
        </Link>
      </div>
    );
  }

  const total = Number(form.labor_cost || 0) + Number(form.parts_cost || 0) + Number(form.vendor_invoice_cost || 0);

  return (
    <div className="space-y-6">
      <Link to="/maintenance" className="text-sm text-teal-800 hover:underline">
        ← Maintenance board
      </Link>
      <Notice error={error} success={success} />
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-mono text-xs text-slate-500">WO {wo.id}</p>
            <h2 className="mt-1 text-2xl font-semibold text-slate-900">{wo.title}</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge value={wo.status} />
            <Badge value={wo.priority} />
            <Badge value={wo.issue_source} />
          </div>
        </div>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
          <div>
            <dt className="text-xs uppercase text-slate-500">Asset</dt>
            <dd>
              <Link className="text-teal-800 hover:underline" to={`/assets/${wo.asset_id}`}>
                {wo.asset}
              </Link>
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">Asset ID</dt>
            <dd className="font-mono text-xs">{wo.vin}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">Type</dt>
            <dd>{wo.asset_type}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">Location</dt>
            <dd>{wo.location || "—"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">Inspection ID</dt>
            <dd className="font-mono text-xs">{wo.inspection_id || "—"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">Inspection item</dt>
            <dd className="font-mono text-xs">{wo.inspection_item_id || "—"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">Inspector</dt>
            <dd>{wo.inspector || "—"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">Created</dt>
            <dd>{wo.opened_at}</dd>
          </div>
        </dl>
      </div>

      <form
        className="grid gap-6 lg:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-800">Workflow</h3>
          <Field label="Status">
            <select className={inputClass} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              {WORK_ORDER_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {statusLabel(status)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Priority">
            <select className={inputClass} value={form.priority || "medium"} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
              {PRIORITIES.map((priority) => (
                <option key={priority}>{priority}</option>
              ))}
            </select>
          </Field>
          <Field label="Assigned technician">
            <select className={inputClass} value={form.assigned_to_id || ""} onChange={(e) => setForm({ ...form, assigned_to_id: e.target.value })}>
              <option value="">Unassigned</option>
              {users.map((user) => (
                <option key={user.id} value={user.id}>
                  {user.full_name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Issue source">
            <select className={inputClass} value={form.issue_source} onChange={(e) => setForm({ ...form, issue_source: e.target.value })}>
              {ISSUE_SOURCES.map((source) => (
                <option key={source}>{source}</option>
              ))}
            </select>
          </Field>
          <Field label="Component / subsystem">
            <input className={inputClass} value={form.failed_component || ""} onChange={(e) => setForm({ ...form, failed_component: e.target.value })} />
          </Field>
          <Field label="Issue description">
            <textarea className={inputClass} value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Field>
          <Field label="Investigation notes">
            <textarea className={inputClass} value={form.investigation_notes || ""} onChange={(e) => setForm({ ...form, investigation_notes: e.target.value })} />
          </Field>
          <Field label="Repair actions">
            <textarea className={inputClass} value={form.repair_actions || ""} onChange={(e) => setForm({ ...form, repair_actions: e.target.value })} />
          </Field>
          <Field label="Parts used">
            <textarea className={inputClass} value={form.parts_used || ""} onChange={(e) => setForm({ ...form, parts_used: e.target.value })} />
          </Field>
        </section>

        <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-800">Cost, vendor, and root cause</h3>
          <Field label="Repair source">
            <select className={inputClass} value={form.repair_channel} onChange={(e) => setForm({ ...form, repair_channel: e.target.value })}>
              {REPAIR_CHANNELS.map((channel) => (
                <option key={channel}>{channel}</option>
              ))}
            </select>
          </Field>
          <Field label="Vendor">
            <input className={inputClass} value={form.vendor_name || ""} onChange={(e) => setForm({ ...form, vendor_name: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Downtime start">
              <input type="datetime-local" className={inputClass} value={(form.downtime_start || "").slice(0, 16)} onChange={(e) => setForm({ ...form, downtime_start: e.target.value || null })} />
            </Field>
            <Field label="Downtime end">
              <input type="datetime-local" className={inputClass} value={(form.downtime_end || "").slice(0, 16)} onChange={(e) => setForm({ ...form, downtime_end: e.target.value || null })} />
            </Field>
            <Field label="Labor hours">
              <input type="number" min="0" step="0.1" className={inputClass} value={form.labor_hours ?? ""} onChange={(e) => setForm({ ...form, labor_hours: e.target.value })} />
            </Field>
            <Field label="Downtime hours">
              <input type="number" min="0" step="0.1" className={inputClass} value={form.downtime_hours ?? ""} onChange={(e) => setForm({ ...form, downtime_hours: e.target.value })} />
            </Field>
            <Field label="Labor cost">
              <input type="number" min="0" step="0.01" className={inputClass} value={form.labor_cost ?? ""} onChange={(e) => setForm({ ...form, labor_cost: e.target.value })} />
            </Field>
            <Field label="Parts cost">
              <input type="number" min="0" step="0.01" className={inputClass} value={form.parts_cost ?? ""} onChange={(e) => setForm({ ...form, parts_cost: e.target.value })} />
            </Field>
            <Field label="Vendor invoice">
              <input type="number" min="0" step="0.01" className={inputClass} value={form.vendor_invoice_cost ?? ""} onChange={(e) => setForm({ ...form, vendor_invoice_cost: e.target.value })} />
            </Field>
            <Field label="Total repair cost">
              <input disabled className={inputClass} value={money(total)} />
            </Field>
          </div>
          <Field label="Root cause category">
            <select className={inputClass} value={form.root_cause_category || ""} onChange={(e) => setForm({ ...form, root_cause_category: e.target.value })}>
              <option value="">Select…</option>
              {ROOT_CAUSE_CATEGORIES.map((category) => (
                <option key={category}>{category}</option>
              ))}
            </select>
          </Field>
          <Field label="Root cause description">
            <textarea className={inputClass} value={form.root_cause_description || ""} onChange={(e) => setForm({ ...form, root_cause_description: e.target.value })} />
          </Field>
          <Field label="Corrective action">
            <textarea className={inputClass} value={form.corrective_action || ""} onChange={(e) => setForm({ ...form, corrective_action: e.target.value })} />
          </Field>
          <Field label="Preventive action">
            <textarea className={inputClass} value={form.preventive_action || ""} onChange={(e) => setForm({ ...form, preventive_action: e.target.value })} />
          </Field>
          <Field label="Completion notes">
            <textarea className={inputClass} value={form.completion_notes || ""} onChange={(e) => setForm({ ...form, completion_notes: e.target.value })} />
          </Field>
          <div className="flex flex-wrap gap-2">
            <button disabled={busy} type="submit" className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              {busy ? "Saving…" : "Save"}
            </button>
            <button type="button" disabled={busy} onClick={complete} className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white">
              Mark complete
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => save({ status: "cancelled" })}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold"
            >
              Cancel work order
            </button>
          </div>
        </section>
      </form>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">Parts used</h3>
        {parts.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">No inventory parts recorded on this work order.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {parts.map((line) => (
              <li key={line.id} className="flex flex-wrap justify-between gap-2 rounded-lg border border-slate-100 p-2 text-sm">
                <span>{line.sku} · {line.name} × {line.quantity}</span>
                <span>{money(line.line_cost)} at {money(line.unit_cost)} each</span>
              </li>
            ))}
          </ul>
        )}
        <form
          className="mt-4 grid gap-3 sm:grid-cols-3"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!partForm.part_id) return;
            try {
              setBusy(true);
              const result = await api(`/work-orders/${woId}/parts`, {
                method: "POST",
                body: JSON.stringify({
                  part_id: partForm.part_id,
                  quantity: Number(partForm.quantity),
                  allow_negative: partForm.allow_negative,
                }),
              });
              setWo(result.work_order);
              setForm({ ...result.work_order, assigned_to_id: result.work_order.assigned_to_id || "" });
              setParts(await api(`/work-orders/${woId}/parts`));
              setCatalog(await api("/parts"));
              setSuccess("Part recorded and inventory updated.");
            } catch (err) {
              setError(err.message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Part">
            <select className={inputClass} value={partForm.part_id} onChange={(e) => setPartForm({ ...partForm, part_id: e.target.value })}>
              <option value="">Select…</option>
              {catalog.map((part) => (
                <option key={part.id} value={part.id}>
                  {part.sku} · {part.name} ({part.quantity_on_hand} on hand)
                </option>
              ))}
            </select>
          </Field>
          <Field label="Quantity">
            <input type="number" min="1" className={inputClass} value={partForm.quantity} onChange={(e) => setPartForm({ ...partForm, quantity: e.target.value })} />
          </Field>
          <div className="flex items-end gap-3">
            {canManageInventory ? (
              <label className="text-xs text-slate-600">
                <input type="checkbox" className="mr-1" checked={partForm.allow_negative} onChange={(e) => setPartForm({ ...partForm, allow_negative: e.target.checked })} />
                Manager override
              </label>
            ) : null}
            <button disabled={busy} type="submit" className="rounded-lg bg-teal-700 px-3 py-2 text-sm font-semibold text-white">
              {canManageInventory ? "Add part" : "Record used"}
            </button>
          </div>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">Activity timeline</h3>
        {(wo.events || []).length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">No events recorded yet.</p>
        ) : (
          <ol className="mt-4 space-y-3">
            {wo.events.map((event) => (
              <li key={event.id} className="rounded-lg border border-slate-100 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge value={event.event_type} />
                  <span className="text-xs text-slate-500">{event.created_at}</span>
                  {event.created_by ? <span className="text-xs text-slate-600">{event.created_by}</span> : null}
                </div>
                <p className="mt-1 text-sm text-slate-700">
                  {event.previous_value ? `${event.previous_value} → ` : ""}
                  {event.new_value || ""}
                </p>
                {event.notes ? <p className="mt-1 text-xs text-slate-500">{event.notes}</p> : null}
              </li>
            ))}
          </ol>
        )}
        <p className="mt-3 text-xs text-slate-500">Downtime recorded: {hours(wo.downtime_hours)}. Updated {wo.updated_at}.</p>
      </section>
    </div>
  );
}
