import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DataTable } from "../components/DataTable";
import InspectionChecklist from "../components/InspectionChecklist.jsx";
import InspectionPhotos from "../components/InspectionPhotos.jsx";
import { Notice } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import { Badge } from "../lib/format.jsx";
import { ROLES, canonicalRole } from "../lib/roles.js";

const PHOTO_LIMIT = 4;

export default function Inspections() {
  const [me, setMe] = useState(null);
  const [assets, setAssets] = useState([]);
  const [rows, setRows] = useState([]);
  const [assetId, setAssetId] = useState("");
  const [inspection, setInspection] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notes, setNotes] = useState("");
  const [photos, setPhotos] = useState([]);
  const [submitting, setSubmitting] = useState(false);

  const isCustomer = canonicalRole(me) === ROLES.CUSTOMER;

  async function load() {
    setLoading(true);
    try {
      const [user, fleet, inspections] = await Promise.all([api("/auth/me"), api("/assets"), api("/inspections")]);
      setMe(user);
      setAssets(fleet);
      setRows(inspections);
      setAssetId((current) => (fleet.some((row) => row.id === current) ? current : fleet[0]?.id || ""));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const selected = useMemo(() => assets.find((row) => row.id === assetId), [assets, assetId]);

  async function startInspection() {
    setError("");
    setMessage("");
    try {
      const created = await api(`/assets/${assetId}/inspections`, { method: "POST" });
      setInspection(created);
      setMessage(`${selected.asset_type} checklist loaded.`);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function onMark(item, result, itemNotes) {
    if (!inspection) return;
    setBusyId(item.id);
    setError("");
    try {
      const updated = await api(`/inspections/${inspection.id}/items/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ result, notes: itemNotes }),
      });
      setInspection(updated);
      const marked = updated.items.find((row) => row.id === item.id);
      if (result === "fail" && marked?.generated_work_order_id) {
        setMessage(`Fail on ${item.component}: open work order created for this asset.`);
      }
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  async function submit() {
    if (!inspection) return;
    try {
      const updated = await api(`/inspections/${inspection.id}/submit`, {
        method: "POST",
        body: JSON.stringify({ notes: "" }),
      });
      setInspection(updated);
      setMessage("Inspection submitted.");
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function cancel() {
    if (!inspection) return;
    try {
      const updated = await api(`/inspections/${inspection.id}/cancel`, { method: "POST" });
      setInspection(updated);
      setMessage("Inspection cancelled.");
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  function onPickPhotos(event) {
    const next = [...photos, ...Array.from(event.target.files || [])];
    if (next.length > PHOTO_LIMIT) {
      setError(`Customers can attach at most ${PHOTO_LIMIT} photos.`);
      setPhotos(next.slice(0, PHOTO_LIMIT));
    } else {
      setError("");
      setPhotos(next);
    }
    event.target.value = "";
  }

  async function submitCustomerInspection() {
    if (!assetId) {
      setError("Select an assigned asset first.");
      return;
    }
    if (photos.length > PHOTO_LIMIT) {
      setError(`Customers can attach at most ${PHOTO_LIMIT} photos.`);
      return;
    }
    setSubmitting(true);
    setError("");
    setMessage("");
    try {
      const body = new FormData();
      if (notes.trim()) body.append("notes", notes.trim());
      photos.forEach((file) => body.append("photos", file));
      const created = await api(`/assets/${assetId}/customer-inspections`, { method: "POST", body });
      setInspection(created);
      setNotes("");
      setPhotos([]);
      setMessage("Inspection submitted.");
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">
          {isCustomer ? "Submit an inspection" : "Digital inspections"}
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          {isCustomer
            ? "Choose one of your assigned assets, add notes, and attach up to 4 photos."
            : "ALPR trailers use a solar/camera/chassis checklist. Semis and fleet vehicles use a powertrain and cabin checklist. Marking Fail opens a maintenance ticket immediately."}
        </p>
      </div>
      {loading ? <p className="text-sm text-slate-600">Loading inspections…</p> : null}

      {isCustomer ? (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm">
              Asset
              <select
                value={assetId}
                onChange={(e) => {
                  setAssetId(e.target.value);
                  setInspection(null);
                  setMessage("");
                }}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
              >
                {assets.length === 0 ? <option value="">No assigned assets</option> : null}
                {assets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.make_model} · {asset.vin}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Notes
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                placeholder="What should the fleet team know?"
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
              />
            </label>
          </div>
          <div className="mt-4">
            <p className="text-sm font-medium text-slate-800">Photos ({photos.length} of {PHOTO_LIMIT})</p>
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp,image/heic,image/heif"
              multiple
              disabled={photos.length >= PHOTO_LIMIT}
              onChange={onPickPhotos}
              className="mt-2 block text-sm"
            />
            {photos.length ? (
              <ul className="mt-3 space-y-2">
                {photos.map((file, index) => (
                  <li key={`${file.name}-${index}`} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                    <span className="truncate">{file.name}</span>
                    <button
                      type="button"
                      onClick={() => setPhotos((current) => current.filter((_, i) => i !== index))}
                      className="text-xs font-semibold text-red-600 hover:text-red-800"
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-slate-500">JPEG, PNG, WebP, or HEIC. Maximum 4 photos.</p>
            )}
          </div>
          <button
            type="button"
            disabled={submitting || !assetId}
            onClick={submitCustomerInspection}
            className="mt-4 rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-600 disabled:opacity-50"
          >
            {submitting ? "Submitting…" : "Submit inspection"}
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            Asset
            <select
              value={assetId}
              onChange={(e) => {
                setAssetId(e.target.value);
                setInspection(null);
                setMessage("");
              }}
              className="mt-1 block w-80 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
            >
              {assets.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.make_model} · {asset.asset_type}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={startInspection} className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-600">
            Start inspection
          </button>
          {inspection && inspection.status === "in_progress" ? (
            <>
              <button type="button" onClick={submit} className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800">
                Submit inspection
              </button>
              <button type="button" onClick={cancel} className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800">
                Cancel inspection
              </button>
            </>
          ) : null}
          {selected ? <Badge value={selected.asset_type} tone="type" /> : null}
        </div>
      )}

      <Notice error={error} success={message} />
      <InspectionPhotos inspection={inspection} />
      {!isCustomer && inspection?.source !== "customer" ? (
        <InspectionChecklist inspection={inspection} onMark={onMark} busyId={busyId} />
      ) : null}

      <DataTable
        columns={[
          {
            key: "id",
            header: "Asset ID",
            render: (r) => <span className="font-mono text-xs">{r.asset_vin ? r.asset_vin.slice(-4) : String(r.id).slice(0, 4)}</span>,
          },
          {
            key: "asset",
            header: "Asset",
            render: (r) =>
              isCustomer ? (
                <span>{r.asset}</span>
              ) : (
                <Link to={`/assets/${r.asset_id}`} className="text-teal-800 hover:underline">
                  {r.asset}
                </Link>
              ),
          },
          { key: "source", header: "Type", render: (r) => <Badge value={r.source === "customer" ? "customer photos" : "checklist"} /> },
          { key: "status", header: "Status", render: (r) => <Badge value={r.status} /> },
          { key: "photo_count", header: "Photos" },
          { key: "fail_count", header: "Fails" },
          { key: "started_at", header: "Started" },
          {
            key: "open",
            header: "",
            render: (r) => (
              <button
                type="button"
                className="text-xs font-semibold text-teal-800 hover:underline"
                onClick={async () => {
                  try {
                    setInspection(await api(`/inspections/${r.id}`));
                  } catch (err) {
                    setError(err.message);
                  }
                }}
              >
                View
              </button>
            ),
          },
        ]}
        rows={rows}
        empty="No inspections recorded yet."
      />
    </div>
  );
}
