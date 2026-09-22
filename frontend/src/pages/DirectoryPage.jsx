import { useEffect, useState } from "react";
import { DataTable } from "../components/DataTable";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";

export default function DirectoryPage({ title, path, extraFields }) {
  const [rows, setRows] = useState([]);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({ name: "" });
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(null);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const suffix = includeArchived ? "?include_archived=true" : "";
      setRows(await api(`${path}${suffix}`));
    } catch (err) {
      setError(err.message);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [path, includeArchived]);

  async function save(event) {
    event.preventDefault();
    if (!form.name?.trim()) {
      setError("Name is required.");
      return;
    }
    setBusy(true);
    try {
      if (form.id) {
        await api(`${path}/${form.id}`, { method: "PATCH", body: JSON.stringify(form) });
        setSuccess("Record updated.");
      } else {
        await api(path, { method: "POST", body: JSON.stringify(form) });
        setSuccess("Record created.");
      }
      setModal(null);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function destroy() {
    setBusy(true);
    try {
      const result = await api(`${path}/${confirm.id}`, { method: "DELETE" });
      setSuccess(result?.action === "archived" ? result.detail : "Record deleted.");
      setConfirm(null);
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
          <h2 className="text-2xl font-semibold text-slate-900">{title}</h2>
          <p className="mt-1 text-sm text-slate-600">Directory records used by custody, deployments, and maintenance.</p>
        </div>
        <button
          type="button"
          onClick={() => {
            setForm({ name: "" });
            extraFields.forEach((field) => {
              /* keep empty */
            });
            setModal(true);
          }}
          className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white"
        >
          Add
        </button>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-600">
        <input type="checkbox" checked={includeArchived} onChange={(e) => setIncludeArchived(e.target.checked)} />
        Include archived
      </label>
      <Notice error={error} success={success} />
      {loading ? <p className="text-sm text-slate-600">Loading…</p> : null}
      <DataTable
        columns={[
          { key: "name", header: "Name" },
          ...extraFields.map((field) => ({ key: field.key, header: field.label, render: (r) => r[field.key] || "—" })),
          ...(path === "/agencies" || path === "/warehouses"
            ? [{ key: "latitude", header: "Map", render: (r) => (r.latitude && r.longitude ? "Geocoded" : "Needs address") }]
            : []),
          { key: "is_archived", header: "Status", render: (r) => (r.is_archived ? "Archived" : "Active") },
          {
            key: "actions",
            header: "",
            render: (r) => (
              <div className="flex gap-2 text-xs font-semibold">
                <button
                  type="button"
                  className="text-teal-800 hover:underline"
                  onClick={() => {
                    setForm(r);
                    setModal(true);
                  }}
                >
                  Edit
                </button>
                <button type="button" className="text-red-700 hover:underline" onClick={() => setConfirm(r)}>
                  Delete
                </button>
              </div>
            ),
          },
        ]}
        rows={rows}
      />
      {modal ? (
        <Modal title={form.id ? "Edit record" : "Add record"} onClose={() => setModal(null)}>
          <form onSubmit={save} className="space-y-3">
            <Field label="Name">
              <input required className={inputClass} value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            {extraFields.map((field) => (
              <Field key={field.key} label={field.label}>
                <input className={inputClass} value={form[field.key] || ""} onChange={(e) => setForm({ ...form, [field.key]: e.target.value })} />
              </Field>
            ))}
            <button disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Save
            </button>
          </form>
        </Modal>
      ) : null}
      {confirm ? (
        <Modal title="Confirm delete" onClose={() => setConfirm(null)}>
          <p className="text-sm text-slate-700">
            Delete {confirm.name}? If it is referenced by operational records it will be archived instead.
          </p>
          <div className="mt-4 flex gap-2">
            <button type="button" disabled={busy} onClick={destroy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Confirm
            </button>
            <button type="button" onClick={() => setConfirm(null)} className="rounded-lg border px-4 py-2 text-sm">
              Cancel
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
