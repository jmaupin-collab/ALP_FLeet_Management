import { useEffect, useState } from "react";
import { API_BASE, api, getToken } from "../lib/api.js";
import { Field, Modal, Notice, inputClass } from "./Modal.jsx";

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

const emptyForm = {
  document_type: "registration",
  title: "",
  issue_date: "",
  expiration_date: "",
  notes: "",
  file: null,
};

export default function AssetDocuments({ assetId }) {
  const [docs, setDocs] = useState([]);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [modal, setModal] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);
  const [confirmId, setConfirmId] = useState(null);

  async function load() {
    try {
      const rows = await api(`/assets/${assetId}/documents`);
      setDocs(rows);
      setForbidden(false);
    } catch (err) {
      if (/fleet admin|forbidden|403/i.test(err.message)) {
        setForbidden(true);
        return;
      }
      setError(err.message);
    }
  }

  useEffect(() => {
    load();
  }, [assetId]);

  if (forbidden) return null;

  async function upload(event) {
    event.preventDefault();
    if (!form.file || !form.title) {
      setError("Name and file are required.");
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
      if (form.notes) body.append("notes", form.notes);
      body.append("file", form.file);
      await api(`/assets/${assetId}/documents`, { method: "POST", body });
      setSuccess("Document uploaded.");
      setModal(false);
      setForm(emptyForm);
      await load();
    } catch (err) {
      setError(err.message);
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
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function tone(state) {
    if (state === "expired") return "border-red-200 bg-red-50";
    if (state === "soon") return "border-amber-200 bg-amber-50";
    return "border-slate-200 bg-white";
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Asset Documents</h3>
          <p className="mt-1 text-xs text-slate-500">Registration, insurance, and other private records. Management only.</p>
        </div>
        <button
          type="button"
          onClick={() => setModal(true)}
          className="rounded-lg bg-teal-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-teal-800"
        >
          Upload
        </button>
      </div>
      <Notice error={error} success={success} />
      {docs.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">No documents uploaded.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {docs.map((doc) => (
            <li key={doc.id} className={`rounded-lg border p-3 ${tone(doc.expiration_state)}`}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{doc.title}</p>
                  <p className="text-xs text-slate-600">
                    {doc.document_type_label} · {doc.original_filename}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {doc.issue_date ? `Issued ${doc.issue_date}` : "No issue date"}
                    {doc.expiration_date ? ` · Expires ${doc.expiration_date}` : ""}
                    {doc.expiration_state === "expired" ? " · Expired" : ""}
                    {doc.expiration_state === "soon" ? ` · ${doc.days_to_expire} days left` : ""}
                  </p>
                  {doc.notes ? <p className="mt-1 text-xs text-slate-600">{doc.notes}</p> : null}
                </div>
                <div className="flex gap-2">
                  <button type="button" onClick={() => download(doc)} className="text-xs font-semibold text-teal-800 hover:underline">
                    Download
                  </button>
                  <button type="button" onClick={() => setConfirmId(doc.id)} className="text-xs font-semibold text-red-700 hover:underline">
                    Delete
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {modal ? (
        <Modal title="Upload document" onClose={() => setModal(false)}>
          <form onSubmit={upload} className="space-y-3">
            <Field label="Document type">
              <select className={inputClass} value={form.document_type} onChange={(e) => setForm({ ...form, document_type: e.target.value })}>
                {DOCUMENT_TYPES.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Document name">
              <input className={inputClass} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Issue date">
                <input type="date" className={inputClass} value={form.issue_date} onChange={(e) => setForm({ ...form, issue_date: e.target.value })} />
              </Field>
              <Field label="Expiration date">
                <input type="date" className={inputClass} value={form.expiration_date} onChange={(e) => setForm({ ...form, expiration_date: e.target.value })} />
              </Field>
            </div>
            <Field label="Notes">
              <textarea className={inputClass} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </Field>
            <Field label="File">
              <input type="file" required onChange={(e) => setForm({ ...form, file: e.target.files?.[0] || null })} />
            </Field>
            <button disabled={busy} type="submit" className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white">
              {busy ? "Uploading…" : "Upload"}
            </button>
          </form>
        </Modal>
      ) : null}

      {confirmId ? (
        <Modal title="Delete document?" onClose={() => setConfirmId(null)}>
          <p className="text-sm text-slate-700">This removes the file and metadata. This cannot be undone.</p>
          <div className="mt-4 flex gap-2">
            <button type="button" disabled={busy} onClick={() => remove(confirmId)} className="rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white">
              Delete
            </button>
            <button type="button" onClick={() => setConfirmId(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold">
              Cancel
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
