import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DataTable } from "../components/DataTable";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import { ASSET_TYPES, PM_STATUSES } from "../lib/constants.js";
import { Badge, money } from "../lib/format.jsx";

export default function PreventiveMaintenance() {
  const [view, setView] = useState("overdue"); // overdue, due, due_soon, all
  const [pmStatus, setPmStatus] = useState("overdue");
  const [assetType, setAssetType] = useState("");
  const [schedules, setSchedules] = useState([]);
  const [assets, setAssets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({});
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams();
      if (assetType) query.set("asset_type", assetType);
      if (view !== "all") query.set("pm_status", view);
      const suffix = query.toString() ? `?${query}` : "";
      const [pmSchedules, fleet] = await Promise.all([api(`/pm/schedules${suffix}`), api("/assets")]);
      setSchedules(pmSchedules);
      setAssets(fleet);
    } catch (err) {
      setError(err.message);
      setSchedules([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [view, assetType]);

  async function saveSchedule(event) {
    event.preventDefault();
    if (!form.asset_id || !form.name) {
      setError("Asset and schedule name are required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (modal === "create") {
        await api("/pm/schedules", {
          method: "POST",
          body: JSON.stringify({
            asset_id: form.asset_id,
            name: form.name,
            description: form.description,
            interval_miles: form.interval_miles ? Number(form.interval_miles) : null,
            interval_engine_hours: form.interval_engine_hours ? Number(form.interval_engine_hours) : null,
            interval_days: form.interval_days ? Number(form.interval_days) : null,
            interval_months: form.interval_months ? Number(form.interval_months) : null,
            due_soon_threshold_miles: form.due_soon_threshold_miles ? Number(form.due_soon_threshold_miles) : 500,
            due_soon_threshold_days: form.due_soon_threshold_days ? Number(form.due_soon_threshold_days) : 7,
            auto_create_work_order: form.auto_create_work_order || false,
          }),
        });
        setSuccess("PM schedule created.");
      } else {
        await api(`/pm/schedules/${form.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: form.name,
            description: form.description,
            interval_miles: form.interval_miles ? Number(form.interval_miles) : null,
            interval_engine_hours: form.interval_engine_hours ? Number(form.interval_engine_hours) : null,
            interval_days: form.interval_days ? Number(form.interval_days) : null,
            interval_months: form.interval_months ? Number(form.interval_months) : null,
            due_soon_threshold_miles: form.due_soon_threshold_miles ? Number(form.due_soon_threshold_miles) : 500,
            due_soon_threshold_days: form.due_soon_threshold_days ? Number(form.due_soon_threshold_days) : 7,
            auto_create_work_order: form.auto_create_work_order,
            is_active: form.is_active,
          }),
        });
        setSuccess("PM schedule updated.");
      }
      setModal(null);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function completeSchedule(scheduleId) {
    setBusy(true);
    setError("");
    try {
      await api(`/pm/schedules/${scheduleId}/complete`, {
        method: "POST",
        body: JSON.stringify({
          completed_at: new Date().toISOString(),
          odometer_miles: form.completion_odometer || null,
          engine_hours: form.completion_engine_hours || null,
          notes: form.completion_notes || null,
        }),
      });
      setSuccess("PM marked complete. Next due updated.");
      setModal(null);
      setForm({});
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Preventive Maintenance</h2>
          <p className="mt-1 text-sm text-slate-600">Schedule and track preventive maintenance based on mileage, engine hours, or calendar intervals.</p>
        </div>
        <button
          type="button"
          onClick={() => {
            setForm({
              asset_id: "",
              name: "",
              description: "",
              interval_miles: "",
              interval_engine_hours: "",
              interval_days: "",
              interval_months: "",
              due_soon_threshold_miles: "500",
              due_soon_threshold_days: "7",
              auto_create_work_order: false,
            });
            setModal("create");
          }}
          className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white"
        >
          Add PM Schedule
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-2">
          {[
            ["overdue", "Overdue"],
            ["due", "Due"],
            ["due_soon", "Due Soon"],
            ["all", "All"],
          ].map(([status, label]) => (
            <button
              key={status}
              type="button"
              onClick={() => setView(status)}
              className={`rounded-lg px-3 py-2 text-sm font-semibold ${
                view === status ? "bg-slate-900 text-white" : "border border-slate-300 bg-white text-slate-700"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <select
          value={assetType}
          onChange={(e) => setAssetType(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
        >
          <option value="">All asset types</option>
          {ASSET_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </div>

      <Notice error={error} success={success} />
      {loading ? <p className="text-sm text-slate-600">Loading PM schedules…</p> : null}

      <DataTable
        columns={[
          { key: "name", header: "Schedule", render: (r) => <span className="font-medium">{r.name}</span> },
          {
            key: "asset_name",
            header: "Asset",
            render: (r) => (
              <Link to={`/assets/${r.asset_id}`} className="text-teal-800 hover:underline">
                {r.asset_name}
              </Link>
            ),
          },
          { key: "asset_type", header: "Type" },
          {
            key: "pm_status",
            header: "Status",
            render: (r) => <Badge value={r.pm_status} />,
          },
          {
            key: "next_due",
            header: "Next Due",
            render: (r) => {
              const parts = [];
              if (r.next_due_miles) parts.push(`${r.next_due_miles.toLocaleString()} mi`);
              if (r.next_due_hours) parts.push(`${r.next_due_hours} hrs`);
              if (r.next_due_date) parts.push(new Date(r.next_due_date).toLocaleDateString());
              return <span className="text-xs">{parts.join(" / ") || "—"}</span>;
            },
          },
          {
            key: "remaining",
            header: "Remaining",
            render: (r) => {
              const parts = [];
              if (r.remaining_miles !== null && r.remaining_miles !== undefined) parts.push(`${r.remaining_miles} mi`);
              if (r.remaining_hours !== null && r.remaining_hours !== undefined) parts.push(`${r.remaining_hours} hrs`);
              if (r.remaining_days !== null && r.remaining_days !== undefined) parts.push(`${r.remaining_days}d`);
              return <span className="text-xs">{parts.join(" / ") || "—"}</span>;
            },
          },
          {
            key: "actions",
            header: "Actions",
            render: (r) => (
              <div className="flex gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => {
                    setForm({ ...r, completion_odometer: "", completion_engine_hours: "", completion_notes: "" });
                    setModal("complete");
                  }}
                  className="font-semibold text-teal-800 hover:underline"
                >
                  Complete
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setForm(r);
                    setModal("edit");
                  }}
                  className="font-semibold text-slate-700 hover:underline"
                >
                  Edit
                </button>
              </div>
            ),
          },
        ]}
        rows={schedules}
      />

      {modal === "create" || modal === "edit" ? (
        <Modal
          title={modal === "create" ? "Add PM Schedule" : "Edit PM Schedule"}
          open={true}
          onClose={() => setModal(null)}
        >
          <form onSubmit={saveSchedule} className="space-y-4">
            {modal === "create" ? (
              <Field label="Asset ID">
                <select className={inputClass} value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })} required>
                  <option value="">Select asset…</option>
                  {assets.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.make_model} ({a.vin})
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
            <Field label="Schedule Name">
              <input className={inputClass} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            </Field>
            <Field label="Description">
              <textarea className={inputClass} value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Interval Miles">
                <input
                  type="number"
                  min="0"
                  step="1"
                  className={inputClass}
                  value={form.interval_miles || ""}
                  onChange={(e) => setForm({ ...form, interval_miles: e.target.value })}
                />
              </Field>
              <Field label="Interval Engine Hours">
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  className={inputClass}
                  value={form.interval_engine_hours || ""}
                  onChange={(e) => setForm({ ...form, interval_engine_hours: e.target.value })}
                />
              </Field>
              <Field label="Interval Days">
                <input
                  type="number"
                  min="1"
                  step="1"
                  className={inputClass}
                  value={form.interval_days || ""}
                  onChange={(e) => setForm({ ...form, interval_days: e.target.value })}
                />
              </Field>
              <Field label="Interval Months">
                <input
                  type="number"
                  min="1"
                  step="1"
                  className={inputClass}
                  value={form.interval_months || ""}
                  onChange={(e) => setForm({ ...form, interval_months: e.target.value })}
                />
              </Field>
              <Field label="Due Soon Threshold (miles)">
                <input
                  type="number"
                  min="0"
                  step="1"
                  className={inputClass}
                  value={form.due_soon_threshold_miles ?? ""}
                  onChange={(e) => setForm({ ...form, due_soon_threshold_miles: e.target.value })}
                />
              </Field>
              <Field label="Due Soon Threshold (days)">
                <input
                  type="number"
                  min="1"
                  step="1"
                  className={inputClass}
                  value={form.due_soon_threshold_days ?? ""}
                  onChange={(e) => setForm({ ...form, due_soon_threshold_days: e.target.value })}
                />
              </Field>
            </div>
            {modal === "edit" ? (
              <Field label="Status">
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
                  <span className="text-sm">Active</span>
                </label>
              </Field>
            ) : null}
            <div className="flex gap-3">
              <button disabled={busy} type="submit" className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white">
                {busy ? "Saving…" : "Save"}
              </button>
              <button type="button" onClick={() => setModal(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold">
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {modal === "complete" ? (
        <Modal title="Complete PM Schedule" open={true} onClose={() => setModal(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              completeSchedule(form.id);
            }}
            className="space-y-4"
          >
            <p className="text-sm text-slate-600">
              Mark <strong>{form.name}</strong> as completed. This will reset the next due date/mileage based on the schedule interval.
            </p>
            <Field label="Odometer Reading (miles)">
              <input
                type="number"
                min="0"
                step="0.1"
                className={inputClass}
                value={form.completion_odometer || ""}
                onChange={(e) => setForm({ ...form, completion_odometer: e.target.value })}
              />
            </Field>
            <Field label="Engine Hours">
              <input
                type="number"
                min="0"
                step="0.1"
                className={inputClass}
                value={form.completion_engine_hours || ""}
                onChange={(e) => setForm({ ...form, completion_engine_hours: e.target.value })}
              />
            </Field>
            <Field label="Notes">
              <textarea
                className={inputClass}
                value={form.completion_notes || ""}
                onChange={(e) => setForm({ ...form, completion_notes: e.target.value })}
              />
            </Field>
            <div className="flex gap-3">
              <button disabled={busy} type="submit" className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white">
                {busy ? "Saving…" : "Mark Complete"}
              </button>
              <button type="button" onClick={() => setModal(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold">
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </div>
  );
}
