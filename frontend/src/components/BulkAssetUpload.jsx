import { useEffect, useRef, useState } from "react";
import { Modal, Notice } from "./Modal.jsx";
import { api, downloadFile, uploadFile } from "../lib/api.js";

const FORBIDDEN = "Importing assets requires an operator, fleet manager, or admin role.";

function RowIssues({ rows }) {
  const bad = rows.filter((row) => row.errors.length > 0);
  if (bad.length === 0) return null;
  return (
    <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-200">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2">Row</th>
            <th className="px-3 py-2">Asset ID</th>
            <th className="px-3 py-2">What to fix</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {bad.map((row) => (
            <tr key={row.row_number} className="align-top">
              <td className="px-3 py-2 font-mono text-xs text-slate-500">{row.row_number}</td>
              <td className="px-3 py-2 font-mono text-xs">{row.vin || "—"}</td>
              <td className="px-3 py-2 text-red-700">
                {row.errors.map((message, index) => (
                  <p key={index}>{message}</p>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function BulkAssetUpload({ onClose, onImported }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [reference, setReference] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(null);
  const fileInput = useRef(null);

  useEffect(() => {
    api("/assets/import/columns").then(setReference).catch(() => setReference(null));
  }, []);

  async function getTemplate(format) {
    setError("");
    try {
      await downloadFile(
        `/assets/import/template?format=${format}`,
        `asset-import-template.${format}`,
        FORBIDDEN,
      );
    } catch (err) {
      setError(err.message);
    }
  }

  async function checkFile(selected) {
    setFile(selected);
    setPreview(null);
    setError("");
    if (!selected) return;
    setBusy(true);
    try {
      setPreview(await uploadFile("/assets/import?commit=false", selected));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const result = await uploadFile("/assets/import?commit=true", file);
      if (!result.committed) {
        // The file changed under us, or a VIN was taken in the meantime.
        setPreview(result);
        setError("Some rows are no longer valid. Review the list and upload again.");
        return;
      }
      setDone(result);
      onImported?.(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setFile(null);
    setPreview(null);
    setDone(null);
    setError("");
    if (fileInput.current) fileInput.current.value = "";
  }

  if (done) {
    return (
      <Modal title="Import complete" onClose={onClose}>
        <p className="text-sm text-slate-700">
          Added <span className="font-semibold">{done.created_count}</span>{" "}
          {done.created_count === 1 ? "asset" : "assets"} from {done.filename}.
        </p>
        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
            Done
          </button>
          <button type="button" onClick={reset} className="rounded-lg border px-4 py-2 text-sm">
            Import another file
          </button>
        </div>
      </Modal>
    );
  }

  const clean = preview && preview.error_rows === 0;

  return (
    <Modal title="Bulk upload assets" onClose={onClose} wide>
      <div className="space-y-4">
        <div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
          <p>
            Upload an Excel (.xlsx) or CSV file to add many assets at once. Start from the template — it
            lists the required columns and your warehouse and agency names.
          </p>
          <div className="mt-2 flex flex-wrap gap-3 text-sm font-semibold text-teal-800">
            <button type="button" onClick={() => getTemplate("xlsx")} className="hover:underline">
              Download Excel template
            </button>
            <button type="button" onClick={() => getTemplate("csv")} className="hover:underline">
              Download CSV template
            </button>
          </div>
        </div>

        {reference ? (
          <details className="text-sm text-slate-700">
            <summary className="cursor-pointer font-medium text-slate-900">
              Column reference (up to {reference.max_rows} rows per file)
            </summary>
            <ul className="mt-2 space-y-1">
              {reference.columns.map((column) => (
                <li key={column.label}>
                  <span className="font-medium">{column.label}</span>
                  <span className="text-slate-500">{column.required ? " (required)" : " (optional)"}</span>
                  {" — "}
                  {column.help}
                </li>
              ))}
            </ul>
          </details>
        ) : null}

        <label className="block text-sm text-slate-700">
          Spreadsheet
          <input
            ref={fileInput}
            type="file"
            accept=".xlsx,.xlsm,.csv"
            disabled={busy}
            onChange={(event) => checkFile(event.target.files?.[0] || null)}
            className="mt-1 block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-slate-900 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white"
          />
        </label>

        {busy && !preview ? <p className="text-sm text-slate-600">Checking the file…</p> : null}
        <Notice error={error} />

        {preview ? (
          <div className="space-y-3">
            <p className="text-sm text-slate-700">
              Found <span className="font-semibold">{preview.total_rows}</span>{" "}
              {preview.total_rows === 1 ? "row" : "rows"}: {preview.valid_rows} ready
              {preview.error_rows > 0 ? `, ${preview.error_rows} needing attention` : ""}.
            </p>
            <RowIssues rows={preview.rows} />
            {preview.error_rows > 0 ? (
              <p className="text-sm text-slate-600">
                Nothing has been imported. Fix the rows above in your spreadsheet and choose the file
                again — the import runs all or nothing.
              </p>
            ) : null}
            <div className="flex gap-2">
              <button
                type="button"
                disabled={!clean || busy}
                onClick={commit}
                className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-600 disabled:opacity-50"
              >
                {busy ? "Importing…" : `Import ${preview.valid_rows} ${preview.valid_rows === 1 ? "asset" : "assets"}`}
              </button>
              <button type="button" onClick={reset} className="rounded-lg border px-4 py-2 text-sm">
                Choose a different file
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
