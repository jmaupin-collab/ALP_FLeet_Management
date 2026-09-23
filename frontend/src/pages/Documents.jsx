import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DataTable } from "../components/DataTable";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { API_BASE, api, displayApiError, getToken } from "../lib/api.js";

const DOCUMENT_TYPES = [
  ["registration", "Registration"],
  ["insurance", "Insurance"],
  ["title", "Title"],
  ["purchase_invoice", "Purchase Invoice"],
  ["warranty", "Warranty"],
  ["permit", "Permit"],
  ["service_record", "Service Record"],
  ["other", "Other"],
];

const STATES = [
  ["", "All documents"],
  ["expired", "Expired"],
  ["expiring", "Reminder due"],
  ["ok", "Current"],
  ["no_expiry", "No expiration"],
];

const REMINDER_CHOICES = [
  ["0", "On the expiration date"],
  ["14", "2 weeks before"],
  ["30", "30 days before"],
  ["60", "60 days before"],
  ["90", "90 days before"],
];

const TYPE_LABELS = Object.fromEntries(DOCUMENT_TYPES);

const emptyUpload = {
  asset_id: "",
  document_type: "registration",
  title: "",
  issue_date: "",
  expiration_date: "",
  reminder_days: "30",
  notes: "",
  file: null,
};

function tone(state) {
  if (state === "expired") return "bg-red-50 text-red-800";
  if (state === "soon") return "bg-amber-50 text-amber-800";
  return "bg-slate-50 text-slate-600";
}

function vehicleLabel(asset) {
  if (!asset) return "Unknown vehicle";
  return `${asset.make_model} · ${asset.license_plate || asset.vin}`;
}

export default function Documents() {
  const [docs, setDocs] = useState([]);
  const [assets, setAssets] = useState([]);
  const [summary, setSummary] = useState(null);
  const [state, setState] = useState("");
  const [documentType, setDocumentType] = useState("");
  const [view, setView] = useState("folders");
  const [openAsset, setOpenAsset] = useState(null);
  const [form, setForm] = useState(emptyUpload);
  const [modal, setModal] = useState(false);
  const [confirmId, setConfirmId] = useState(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (state) params.set("state", state);
      if (documentType) params.set("document_type", documentType);
      const query = params.toString() ? `?${params}` : "";
      const [rows, rollup, fleet] = await Promise.all([
        api(`/documents${query}`),
        api("/documents/summary"),
        api("/assets"),
      ]);
      setDocs(rows);
      setSummary(rollup);
      setAssets(fleet);
      setError("");
    } catch (err) {
      setError(displayApiError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [state, documentType]);

  async function upload(event) {
    event.preventDefault();
    if (!form.asset_id || !form.title || !form.file) {
      setError("Vehicle, document name, and file are required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const body = new FormData();
      body.append("document_type", form.document_type);
      body.append("title", form.title);
      if (form.issue_date) body.append("issue_date", form.issue_date);
      if (form.expiration_date) body.append("expiration_date", form.expiration_date);
      body.append("reminder_days", form.reminder_days || "30");
      if (form.notes) body.append("notes", form.notes);
      body.append("file", form.file);
      await api(`/assets/${form.asset_id}/documents`, { method: "POST", body });
      setSuccess("Document filed.");
      setOpenAsset(form.asset_id);
      setModal(false);
      setForm(emptyUpload);
      await load();
    } catch (err) {
      setError(displayApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function download(doc) {
    const response = await fetch(`${API_BASE}/documents/${doc.id}/file`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!response.ok) {
      setError("Download failed.");
      return;
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = doc.original_filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function remove(id) {
    setBusy(true);
    try {
      await api(`/documents/${id}`, { method: "DELETE" });
      setConfirmId(null);
      setSuccess("Document deleted.");
      await load();
    } catch (err) {
      setError(displayApiError(err));
    } finally {
      setBusy(false);
    }
  }

  function startUpload(assetId) {
    setForm({ ...emptyUpload, asset_id: assetId || "" });
    setModal(true);
  }

  // One folder per vehicle, including vehicles with nothing on file yet so
  // there is somewhere to drop the first document.
  const folders = useMemo(() => {
    const byAsset = new Map();
    for (const doc of docs) {
      const id = doc.asset?.asset_id || doc.asset_id;
      if (!byAsset.has(id)) byAsset.set(id, { asset: doc.asset, documents: [] });
      byAsset.get(id).documents.push(doc);
    }
    const filtering = Boolean(state || documentType);
    return assets
      .filter((asset) => !asset.is_archived)
      .map((asset) => ({
        asset: { ...asset, asset_id: asset.id },
        documents: byAsset.get(asset.id)?.documents || [],
      }))
      .filter((folder) => folder.documents.length > 0 || !filtering)
      .sort((a, b) => b.documents.length - a.documents.length || a.asset.vin.localeCompare(b.asset.vin));
  }, [docs, assets, state, documentType]);

  const counts = summary?.counts || {};
  const gaps = summary?.missing_core_documents || [];
  const missingByAsset = useMemo(
    () => Object.fromEntries(gaps.map((row) => [String(row.asset_id), row.missing])),
    [gaps]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Documents</h2>
          <p className="mt-1 text-sm text-slate-600">
            Registration, insurance, titles, and other records, filed per vehicle. Anything past its reminder window
            also shows up in the <Link to="/attention" className="text-teal-800 hover:underline">Attention Center</Link>.
          </p>
        </div>
        <button
          type="button"
          onClick={() => startUpload("")}
          className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800"
        >
          Upload document
        </button>
      </div>
      <Notice error={error} success={success} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <SummaryCard label="On file" value={counts.total ?? "—"} />
        <SummaryCard label="Expired" value={counts.expired ?? "—"} tone="text-red-700" />
        <SummaryCard label="Reminder due" value={counts.expiring ?? "—"} tone="text-amber-700" />
        <SummaryCard label="Current" value={counts.ok ?? "—"} />
        <SummaryCard
          label="Vehicles with a gap"
          value={summary ? `${gaps.length} of ${summary.assets_total}` : "—"}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-slate-300 bg-white p-0.5">
          {[
            ["folders", "By vehicle"],
            ["list", "All documents"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setView(value)}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold ${
                view === value ? "bg-teal-700 text-white" : "text-slate-600"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <select
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
          value={state}
          onChange={(e) => setState(e.target.value)}
        >
          {STATES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
          value={documentType}
          onChange={(e) => setDocumentType(e.target.value)}
        >
          <option value="">All types</option>
          {DOCUMENT_TYPES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {loading ? <p className="text-sm text-slate-600">Loading documents…</p> : null}

      {view === "folders" ? (
        <div className="space-y-2">
          {folders.length === 0 ? (
            <p className="text-sm text-slate-500">No vehicles match this filter.</p>
          ) : null}
          {folders.map((folder) => {
            const id = String(folder.asset.asset_id);
            const isOpen = openAsset === id;
            const missing = missingByAsset[id] || [];
            const needsAttention = folder.documents.filter((d) =>
              ["expired", "soon"].includes(d.expiration_state)
            ).length;
            return (
              <div key={id} className="rounded-xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3 p-4">
                  <button
                    type="button"
                    onClick={() => setOpenAsset(isOpen ? null : id)}
                    className="flex min-w-0 flex-1 items-center gap-3 text-left"
                  >
                    <span aria-hidden className="text-slate-400">{isOpen ? "▾" : "▸"}</span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-semibold text-slate-900">
                        {vehicleLabel(folder.asset)}
                      </span>
                      <span className="mt-0.5 block text-xs text-slate-500">
                        {folder.documents.length} document{folder.documents.length === 1 ? "" : "s"}
                        {needsAttention > 0 ? ` · ${needsAttention} needing attention` : ""}
                        {missing.length > 0
                          ? ` · missing ${missing.map((m) => TYPE_LABELS[m] || m).join(", ")}`
                          : ""}
                      </span>
                    </span>
                  </button>
                  <div className="flex shrink-0 gap-3">
                    <button
                      type="button"
                      onClick={() => startUpload(id)}
                      className="text-xs font-semibold text-teal-800 hover:underline"
                    >
                      Add document
                    </button>
                    <Link to={`/assets/${id}`} className="text-xs font-semibold text-slate-600 hover:underline">
                      Open vehicle
                    </Link>
                  </div>
                </div>
                {isOpen ? (
                  <div className="border-t border-slate-100 p-4 pt-3">
                    {folder.documents.length === 0 ? (
                      <p className="text-sm text-slate-500">Nothing filed for this vehicle yet.</p>
                    ) : (
                      DOCUMENT_TYPES.filter(([value]) =>
                        folder.documents.some((d) => d.document_type === value)
                      ).map(([value, label]) => (
                        <div key={value} className="mb-3 last:mb-0">
                          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
                          <ul className="mt-1 space-y-1">
                            {folder.documents
                              .filter((d) => d.document_type === value)
                              .map((doc) => (
                                <li
                                  key={doc.id}
                                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 px-3 py-2"
                                >
                                  <span className="min-w-0">
                                    <span className="block truncate text-sm text-slate-900">{doc.title}</span>
                                    <span className="text-xs text-slate-500">
                                      {doc.expiration_date ? `Expires ${doc.expiration_date}` : "No expiration"}
                                      {doc.expiration_date ? ` · reminder ${doc.reminder_days}d ahead` : ""}
                                    </span>
                                  </span>
                                  <span className="flex items-center gap-3">
                                    <span
                                      className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(doc.expiration_state)}`}
                                    >
                                      {stateLabel(doc)}
                                    </span>
                                    <button
                                      type="button"
                                      onClick={() => download(doc)}
                                      className="text-xs font-semibold text-teal-800 hover:underline"
                                    >
                                      Download
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => setConfirmId(doc.id)}
                                      className="text-xs font-semibold text-red-700 hover:underline"
                                    >
                                      Delete
                                    </button>
                                  </span>
                                </li>
                              ))}
                          </ul>
                        </div>
                      ))
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <DataTable
          columns={[
            {
              key: "asset",
              header: "Vehicle",
              render: (r) =>
                r.asset ? (
                  <Link to={`/assets/${r.asset.asset_id}`} className="text-teal-800 hover:underline">
                    {r.asset.make_model}
                    <span className="ml-2 font-mono text-xs text-slate-500">
                      {r.asset.license_plate || r.asset.vin}
                    </span>
                  </Link>
                ) : (
                  "—"
                ),
            },
            { key: "document_type_label", header: "Type" },
            { key: "title", header: "Document" },
            { key: "issue_date", header: "Issued", render: (r) => r.issue_date || "—" },
            { key: "expiration_date", header: "Expires", render: (r) => r.expiration_date || "—" },
            {
              key: "expiration_state",
              header: "Status",
              render: (r) => (
                <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(r.expiration_state)}`}>
                  {stateLabel(r)}
                </span>
              ),
            },
            {
              key: "id",
              header: "",
              render: (r) => (
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => download(r)}
                    className="text-xs font-semibold text-teal-800 hover:underline"
                  >
                    Download
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmId(r.id)}
                    className="text-xs font-semibold text-red-700 hover:underline"
                  >
                    Delete
                  </button>
                </div>
              ),
            },
          ]}
          rows={docs}
          empty="No documents match this filter."
        />
      )}

      {gaps.length > 0 ? (
        <div>
          <h3 className="text-sm font-semibold text-slate-800">Vehicles missing a core document</h3>
          <p className="mt-1 text-xs text-slate-500">
            Registration, insurance, and title are listed here when nothing is on file. Trailers and yard equipment
            legitimately have gaps, so nothing is blocked — this is a checklist, not a rule.
          </p>
          <DataTable
            columns={[
              {
                key: "asset_id",
                header: "Vehicle",
                render: (r) => (
                  <Link to={`/assets/${r.asset_id}`} className="text-teal-800 hover:underline">
                    {r.make_model}
                    <span className="ml-2 font-mono text-xs text-slate-500">{r.license_plate || r.vin}</span>
                  </Link>
                ),
              },
              { key: "asset_type", header: "Type" },
              {
                key: "missing",
                header: "Not on file",
                render: (r) => r.missing.map((m) => TYPE_LABELS[m] || m).join(", "),
              },
              {
                key: "upload",
                header: "",
                render: (r) => (
                  <button
                    type="button"
                    onClick={() => startUpload(String(r.asset_id))}
                    className="text-xs font-semibold text-teal-800 hover:underline"
                  >
                    Upload
                  </button>
                ),
              },
            ]}
            rows={gaps.map((row) => ({ ...row, id: row.asset_id }))}
            empty="Every vehicle has its core documents."
          />
        </div>
      ) : null}

      {modal ? (
        <Modal title="Upload document" onClose={() => setModal(false)}>
          <form onSubmit={upload} className="space-y-3">
            <Field label="Vehicle">
              <select
                required
                className={inputClass}
                value={form.asset_id}
                onChange={(e) => setForm({ ...form, asset_id: e.target.value })}
              >
                <option value="">Select vehicle</option>
                {assets
                  .filter((asset) => !asset.is_archived)
                  .map((asset) => (
                    <option key={asset.id} value={asset.id}>
                      {asset.make_model} · {asset.license_plate || asset.vin}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Document type">
              <select
                className={inputClass}
                value={form.document_type}
                onChange={(e) => setForm({ ...form, document_type: e.target.value })}
              >
                {DOCUMENT_TYPES.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Document name">
              <input
                required
                className={inputClass}
                placeholder="2026 Registration"
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Issue date">
                <input
                  type="date"
                  className={inputClass}
                  value={form.issue_date}
                  onChange={(e) => setForm({ ...form, issue_date: e.target.value })}
                />
              </Field>
              <Field label="Expiration date">
                <input
                  type="date"
                  className={inputClass}
                  value={form.expiration_date}
                  onChange={(e) => setForm({ ...form, expiration_date: e.target.value })}
                />
              </Field>
            </div>
            {form.expiration_date ? (
              <Field label="Remind me">
                <select
                  className={inputClass}
                  value={form.reminder_days}
                  onChange={(e) => setForm({ ...form, reminder_days: e.target.value })}
                >
                  {REMINDER_CHOICES.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
            <Field label="Notes">
              <textarea
                className={inputClass}
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </Field>
            <Field label="File">
              <input
                type="file"
                required
                onChange={(e) => setForm({ ...form, file: e.target.files?.[0] || null })}
              />
            </Field>
            <button
              disabled={busy}
              type="submit"
              className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
            >
              {busy ? "Uploading…" : "Upload"}
            </button>
          </form>
        </Modal>
      ) : null}

      {confirmId ? (
        <Modal title="Delete document?" onClose={() => setConfirmId(null)}>
          <p className="text-sm text-slate-700">This removes the file and its details. This cannot be undone.</p>
          <div className="mt-4 flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => remove(confirmId)}
              className="rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white"
            >
              Delete
            </button>
            <button
              type="button"
              onClick={() => setConfirmId(null)}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold"
            >
              Cancel
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

function stateLabel(doc) {
  if (!doc.expiration_date) return "No expiry";
  if (doc.expiration_state === "expired") return `Expired ${Math.abs(doc.days_to_expire)}d ago`;
  return `${doc.days_to_expire}d left`;
}

function SummaryCard({ label, value, tone: toneClass = "text-slate-900" }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-2 text-2xl font-semibold ${toneClass}`}>{value}</p>
    </div>
  );
}
