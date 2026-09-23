import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import BulkAssetUpload from "../components/BulkAssetUpload.jsx";
import { DataTable } from "../components/DataTable";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import { ASSET_TYPES, CUSTODY_TYPES } from "../lib/constants.js";
import { Badge, money } from "../lib/format.jsx";
import { ADMINS, hasRole } from "../lib/roles.js";

const emptyForm = {
  vin: "",
  license_plate: "",
  license_plate_state: "",
  make_model: "",
  initial_purchase_cost: "",
  current_location: "",
  asset_type: "ALPR Trailer",
  current_custody_type: "Warehouse Depot",
  warehouse_id: "",
  agency_id: "",
};

// A new asset's location comes from the site it sits at, so the picker follows
// the custody type instead of asking for a typed location.
const SITE_FIELD = {
  "Warehouse Depot": { key: "warehouse_id", label: "Warehouse", directory: "warehouses" },
  "In Transit": { key: "warehouse_id", label: "Destination warehouse", directory: "warehouses" },
  "Customer / LE Agency": { key: "agency_id", label: "Agency", directory: "agencies" },
};

export default function Assets() {
  const [q, setQ] = useState("");
  const [assetType, setAssetType] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [assets, setAssets] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [agencies, setAgencies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const [currentUser, setCurrentUser] = useState(null);
  const [bulkOpen, setBulkOpen] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams();
      if (q.trim()) query.set("q", q.trim());
      if (assetType) query.set("asset_type", assetType);
      if (includeArchived) query.set("include_archived", "true");
      const suffix = query.toString() ? `?${query}` : "";
      setAssets(await api(`/assets${suffix}`));
    } catch (err) {
      setError(err.message);
      setAssets([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // Load the sites a new asset can start at
    api("/warehouses").then(setWarehouses).catch(() => setWarehouses([]));
    api("/agencies").then(setAgencies).catch(() => setAgencies([]));
    // Load current user to check role
    api("/auth/me").then(setCurrentUser).catch(() => setCurrentUser(null));
  }, [assetType, includeArchived]);

  const rows = useMemo(() => assets, [assets]);

  // Check if current user is an admin
  const isAdmin = hasRole(currentUser, ADMINS);

  const site = SITE_FIELD[form.current_custody_type] || SITE_FIELD["Warehouse Depot"];
  const siteOptions = site.directory === "agencies" ? agencies : warehouses;

  function selectSite(event) {
    const id = event.target.value;
    const name = event.target.selectedOptions[0]?.text || "";
    // Only one link applies at a time, so clear both before setting the live one.
    setForm({ ...form, warehouse_id: "", agency_id: "", [site.key]: id, current_location: id ? name : "" });
  }

  async function saveAsset(event) {
    event.preventDefault();
    if (!form.vin || !form.make_model || !form.initial_purchase_cost) {
      setError("Asset ID, make/model, and purchase cost are required.");
      return;
    }
    if (modal === "create" && !form[site.key]) {
      setError(`Select a ${site.label.toLowerCase()} so the asset has a location.`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (modal === "create") {
        await api("/assets", {
          method: "POST",
          body: JSON.stringify({
            vin: form.vin,
            license_plate: form.license_plate || null,
            license_plate_state: form.license_plate_state || null,
            make_model: form.make_model,
            asset_type: form.asset_type,
            initial_purchase_cost: Number(form.initial_purchase_cost),
            current_location: form.current_location,
            current_custody_type: form.current_custody_type,
            // Empty strings are not valid UUIDs; send null for the unused link.
            warehouse_id: form.warehouse_id || null,
            agency_id: form.agency_id || null,
          }),
        });
        setSuccess("Asset created.");
      } else {
        await api(`/assets/${form.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            vin: form.vin,
            license_plate: form.license_plate || null,
            license_plate_state: form.license_plate_state || null,
            make_model: form.make_model,
            initial_purchase_cost: Number(form.initial_purchase_cost),
            asset_type: form.asset_type,
          }),
        });
        setSuccess("Asset updated.");
      }
      setModal(null);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function runConfirm() {
    if (!confirm) return;
    setBusy(true);
    setError("");
    try {
      if (confirm.action === "archive") {
        await api(`/assets/${confirm.id}/archive`, { method: "POST" });
        setSuccess("Asset archived. History was kept.");
      } else if (confirm.action === "restore") {
        await api(`/assets/${confirm.id}/restore`, { method: "POST" });
        setSuccess("Asset restored.");
      } else {
        await api(`/assets/${confirm.id}`, { method: "DELETE" });
        setSuccess("Test/accidental asset deleted.");
      }
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
          <h2 className="text-2xl font-semibold text-slate-900">Assets</h2>
          <p className="mt-1 text-sm text-slate-600">Search the mixed fleet. Archive operational units; delete only test or accidental records.</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setBulkOpen(true)}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
          >
            Bulk Upload
          </button>
          <button
            type="button"
            onClick={() => {
              setForm(emptyForm);
              setModal("create");
            }}
            className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-600"
          >
            Add New Asset
          </button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
          placeholder="Search Asset ID, plate, model, custody"
          className="w-64 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
        />
        <select
          value={assetType}
          onChange={(e) => setAssetType(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
        >
          <option value="">All types</option>
          {ASSET_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <button type="button" onClick={load} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
          Search
        </button>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={includeArchived} onChange={(e) => setIncludeArchived(e.target.checked)} />
          Include archived
        </label>
      </div>
      <Notice error={error} success={success} />
      {loading ? <p className="text-sm text-slate-600">Loading assets…</p> : null}
      <DataTable
        columns={[
          {
            key: "make_model",
            header: "Asset",
            render: (r) => (
              <Link to={`/assets/${r.id}`} className="font-medium text-teal-800 hover:underline">
                {r.make_model}
                {r.lemon_flag ? <span className="ml-2 text-xs font-bold text-red-700">LEMON</span> : null}
                {r.is_archived ? <span className="ml-2 text-xs font-semibold text-slate-500">ARCHIVED</span> : null}
              </Link>
            ),
          },
          { key: "vin", header: "Asset ID", render: (r) => <span className="font-mono text-xs">{r.vin}</span> },
          {
            key: "license_plate",
            header: "Plate",
            render: (r) =>
              r.license_plate ? (
                <span className="font-mono text-xs">
                  {r.license_plate}
                  {r.license_plate_state ? <span className="text-slate-500"> · {r.license_plate_state}</span> : null}
                </span>
              ) : (
                <span className="text-slate-400">—</span>
              ),
          },
          { key: "asset_type", header: "Type", render: (r) => <Badge value={r.asset_type} tone="type" /> },
          {
            key: "custody",
            header: "Custody",
            render: (r) => (
              <div>
                {r.current_custody_type ? <Badge value={r.current_custody_type} /> : "—"}
                <p className="mt-1 text-xs text-slate-600">{r.current_location}</p>
              </div>
            ),
          },
          { 
            key: "operational_status", 
            header: "Status", 
            render: (r) => (
              <div className="flex flex-wrap items-center gap-1">
                {r.operational_status ? <Badge value={r.operational_status} /> : <span className="text-slate-400">—</span>}
                {r.open_work_orders > 0 ? <Badge value={`${r.open_work_orders} open WO`} /> : null}
              </div>
            )
          },
          { key: "initial_purchase_cost", header: "Purchase cost", render: (r) => money(r.initial_purchase_cost) },
          { key: "open_work_orders", header: "Open WOs" },
          {
            key: "actions",
            header: "",
            render: (r) => (
              <div className="flex flex-wrap gap-2 text-xs font-semibold">
                <Link to={`/assets/${r.id}`} className="text-teal-800 hover:underline">
                  View
                </Link>
                <button
                  type="button"
                  className="text-slate-700 hover:underline"
                  onClick={() => {
                    setForm({
                      id: r.id,
                      vin: r.vin,
                      license_plate: r.license_plate || "",
                      license_plate_state: r.license_plate_state || "",
                      make_model: r.make_model,
                      initial_purchase_cost: r.initial_purchase_cost,
                      current_location: r.current_location,
                      asset_type: r.asset_type,
                      current_custody_type: r.current_custody_type || "Warehouse Depot",
                    });
                    setModal("edit");
                  }}
                >
                  Edit
                </button>
                {r.is_archived ? (
                  <button type="button" className="text-slate-700 hover:underline" onClick={() => setConfirm({ action: "restore", id: r.id, name: r.make_model })}>
                    Restore
                  </button>
                ) : (
                  <button type="button" className="text-amber-800 hover:underline" onClick={() => setConfirm({ action: "archive", id: r.id, name: r.make_model })}>
                    Archive
                  </button>
                )}
                {isAdmin && (
                  <button type="button" className="text-red-700 hover:underline" onClick={() => setConfirm({ action: "delete", id: r.id, name: r.make_model })}>
                    Delete
                  </button>
                )}
              </div>
            ),
          },
        ]}
        rows={rows}
        empty="No assets match those filters."
      />

      {bulkOpen ? (
        <BulkAssetUpload
          onClose={() => setBulkOpen(false)}
          onImported={(result) => {
            setSuccess(`Imported ${result.created_count} ${result.created_count === 1 ? "asset" : "assets"}.`);
            load();
          }}
        />
      ) : null}

      {modal ? (
        <Modal title={modal === "create" ? "Add New Asset" : "Edit Asset"} onClose={() => setModal(null)}>
          <form onSubmit={saveAsset} className="space-y-3">
            <Field label="Asset ID">
              <input required minLength={4} maxLength={32} className={inputClass} value={form.vin} onChange={(e) => setForm({ ...form, vin: e.target.value })} />
            </Field>
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <Field label="Plate number (optional)">
                  <input
                    maxLength={16}
                    className={`${inputClass} uppercase`}
                    placeholder="Leave blank if the unit has no plate"
                    value={form.license_plate}
                    onChange={(e) => setForm({ ...form, license_plate: e.target.value.toUpperCase() })}
                  />
                </Field>
              </div>
              <Field label="State">
                <input
                  maxLength={2}
                  className={`${inputClass} uppercase`}
                  placeholder="AZ"
                  value={form.license_plate_state}
                  onChange={(e) => setForm({ ...form, license_plate_state: e.target.value.toUpperCase() })}
                />
              </Field>
            </div>
            <Field label="Make / model">
              <input required className={inputClass} value={form.make_model} onChange={(e) => setForm({ ...form, make_model: e.target.value })} />
            </Field>
            <Field label="Asset type">
              <select className={inputClass} value={form.asset_type} onChange={(e) => setForm({ ...form, asset_type: e.target.value })}>
                {ASSET_TYPES.map((type) => (
                  <option key={type}>{type}</option>
                ))}
              </select>
            </Field>
            <Field label="Purchase cost">
              <input
                required
                type="number"
                min="1"
                step="0.01"
                className={inputClass}
                value={form.initial_purchase_cost}
                onChange={(e) => setForm({ ...form, initial_purchase_cost: e.target.value })}
              />
            </Field>
            {modal === "create" ? (
              <>
                <Field label="Custody type">
                  <select
                    className={inputClass}
                    value={form.current_custody_type}
                    onChange={(e) =>
                      setForm({ ...form, current_custody_type: e.target.value, warehouse_id: "", agency_id: "", current_location: "" })
                    }
                  >
                    {CUSTODY_TYPES.map((type) => (
                      <option key={type}>{type}</option>
                    ))}
                  </select>
                </Field>
                <Field label={site.label}>
                  <select required className={inputClass} value={form[site.key]} onChange={selectSite}>
                    <option value="">{`-- Select a ${site.label.toLowerCase()} --`}</option>
                    {siteOptions.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.name}
                      </option>
                    ))}
                  </select>
                  {siteOptions.length === 0 ? (
                    <p className="mt-1 text-xs text-slate-600">
                      No {site.directory} yet. Add one under {site.directory === "agencies" ? "Agencies" : "Warehouses"} first.
                    </p>
                  ) : null}
                </Field>
              </>
            ) : null}
            <button disabled={busy} type="submit" className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">
              {busy ? "Saving…" : "Save"}
            </button>
          </form>
        </Modal>
      ) : null}

      {confirm ? (
        <Modal title="Confirm action" onClose={() => setConfirm(null)}>
          <p className="text-sm text-slate-700">
            {confirm.action === "delete"
              ? `Permanently delete ${confirm.name}? This is only allowed for assets with no operational history.`
              : confirm.action === "archive"
                ? `Archive ${confirm.name}? Current assignment will be closed and the timeline kept.`
                : `Restore ${confirm.name} to the active fleet?`}
          </p>
          <div className="mt-4 flex gap-2">
            <button type="button" disabled={busy} onClick={runConfirm} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              {busy ? "Working…" : "Confirm"}
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
