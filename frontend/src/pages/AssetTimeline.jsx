import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import AssetDocuments from "../components/AssetDocuments.jsx";
import AssetUtilization from "../components/AssetUtilization.jsx";
import { CustodySummary, LemonBanner } from "../components/Custody.jsx";
import InspectionChecklist from "../components/InspectionChecklist.jsx";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import { ASSET_TYPES, CUSTODY_TYPES, DEPLOYMENT_STATUSES, OPEN_WO_STATUSES, OPERATIONAL_STATUSES, REPAIR_CHANNELS, WORK_ORDER_STATUSES } from "../lib/constants.js";

const WAREHOUSE_STATUSES = OPERATIONAL_STATUSES.filter((status) => !["deployed", "in_transit"].includes(status));

// The site you pick is the location, so each custody type points at its own
// directory rather than asking for the same place twice.
const SITE_FIELD = {
  "Customer / LE Agency": { key: "agency_id", label: "Customer / Agency", directory: "agencies" },
  "In Transit": { key: "warehouse_id", label: "Destination warehouse", directory: "warehouses" },
  "Warehouse Depot": { key: "warehouse_id", label: "Warehouse", directory: "warehouses" },
};
import { Badge, hours, money } from "../lib/format.jsx";

export default function AssetTimeline() {
  const { assetId } = useParams();
  const [profile, setProfile] = useState(null);
  const [workOrders, setWorkOrders] = useState([]);
  const [pmSchedules, setPmSchedules] = useState([]);
  const [meterReading, setMeterReading] = useState(null);
  const [warehouses, setWarehouses] = useState([]);
  const [agencies, setAgencies] = useState([]);
  const [ready, setReady] = useState(false);
  const [inspection, setInspection] = useState(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({});
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(null);

  async function load() {
    const timeline = await api(`/assets/${assetId}/timeline`);
    setProfile(timeline);
    const [orders, pmSch, meter, whList, agList] = await Promise.all([
      api(`/work-orders?asset_id=${assetId}&include_archived=true`),
      api(`/pm/schedules?asset_id=${assetId}`),
      api(`/assets/${assetId}/meters/latest`),
      api(`/warehouses`),
      api(`/agencies`),
    ]);
    setWorkOrders(orders);
    setPmSchedules(pmSch);
    setMeterReading(meter);
    setWarehouses(whList);
    setAgencies(agList);
    setReady(true);
  }

  useEffect(() => {
    setReady(false);
    load().catch((err) => {
      setError(err.message);
      setReady(true);
    });
  }, [assetId]);

  async function run(path, options, message) {
    setBusy(true);
    setError("");
    try {
      const result = await api(path, options);
      setSuccess(message);
      setModal(null);
      await load();
      return result;
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setBusy(false);
    }
  }

  if (!ready) return <p className="text-sm text-slate-600">Loading asset summary…</p>;
  if (!profile?.asset) {
    return (
      <div>
        <p className="text-slate-700">{error || "Asset not found."}</p>
        <Link to="/assets" className="mt-3 inline-block text-sm text-teal-800 hover:underline">
          Back to assets
        </Link>
      </div>
    );
  }

  const asset = profile.asset;
  const openOrders = workOrders.filter((wo) => OPEN_WO_STATUSES.includes(wo.status));
  const historicalOrders = workOrders.filter((wo) => !OPEN_WO_STATUSES.includes(wo.status));
  const lifetimeCost = workOrders.reduce((sum, wo) => sum + Number(wo.total_repair_cost || Number(wo.labor_cost) + Number(wo.parts_cost)), 0);
  const totalDowntime = workOrders.reduce((sum, wo) => sum + Number(wo.downtime_hours || 0), 0);
  
  const overduePM = pmSchedules.filter((pm) => pm.pm_status === "overdue");
  const duePM = pmSchedules.filter((pm) => pm.pm_status === "due" || pm.pm_status === "due_soon");

  async function startInspection() {
    const created = await run(`/assets/${asset.id}/inspections`, { method: "POST" }, "Inspection started.");
    setInspection(created);
  }

  async function onMark(item, result, notes) {
    setBusyId(item.id);
    try {
      const updated = await api(`/inspections/${inspection.id}/items/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ result, notes }),
      });
      setInspection(updated);
      if (result === "fail") setSuccess(`Open work order created for failed ${item.component}.`);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-6">
      <Link to="/assets" className="text-sm text-teal-800 hover:underline">
        ← Assets
      </Link>
      <LemonBanner show={asset.lemon_flag} />
      <Notice error={error} success={success} />
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold text-slate-900">{asset.make_model}</h2>
            <p className="mt-1 font-mono text-xs text-slate-500">
              {asset.vin}
              {asset.license_plate
                ? ` · Plate ${asset.license_plate}${asset.license_plate_state ? ` (${asset.license_plate_state})` : ""}`
                : ""}
            </p>
            {asset.is_archived ? <p className="mt-2 text-sm font-semibold text-amber-800">Archived / retired</p> : null}
          </div>
          <Badge value={asset.asset_type} tone="type" />
        </div>
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <CustodySummary asset={asset} />
          
          {/* Preventive Maintenance section */}
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-start justify-between">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">Preventive Maintenance</h3>
                {meterReading ? (
                  <div className="mt-1 flex gap-4 text-xs text-slate-600">
                    {meterReading.odometer_miles ? <span><strong>Odometer:</strong> {Number(meterReading.odometer_miles).toLocaleString()} mi</span> : null}
                    {meterReading.engine_hours ? <span><strong>Engine Hours:</strong> {Number(meterReading.engine_hours).toFixed(1)} hrs</span> : null}
                  </div>
                ) : null}
              </div>
              <Link to="/preventive-maintenance" className="text-xs font-semibold text-teal-800 hover:underline">
                View all
              </Link>
            </div>
            {overduePM.length > 0 || duePM.length > 0 ? (
              <div className="space-y-3">
                {overduePM.length > 0 ? (
                  <div>
                    <h4 className="mb-1 text-xs font-semibold text-red-800">Overdue ({overduePM.length})</h4>
                    <ul className="space-y-1">
                      {overduePM.map((pm) => (
                        <li key={pm.id} className="flex items-center justify-between rounded border border-red-200 bg-red-50 p-2 text-xs">
                          <span className="font-medium text-red-900">{pm.name}</span>
                          <Badge value={pm.pm_status} />
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {duePM.length > 0 ? (
                  <div>
                    <h4 className="mb-1 text-xs font-semibold text-amber-800">Due / Due Soon ({duePM.length})</h4>
                    <ul className="space-y-1">
                      {duePM.map((pm) => (
                        <li key={pm.id} className="flex items-center justify-between rounded border border-amber-200 bg-amber-50 p-2 text-xs">
                          <span className="font-medium text-amber-900">{pm.name}</span>
                          <Badge value={pm.pm_status} />
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="text-xs text-slate-600">No preventive maintenance due or overdue.</p>
            )}
          </div>
          <dl className="grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-500">Status</dt>
              <dd className="mt-1 flex flex-wrap items-center gap-2">
                <Badge value={asset.operational_status || asset.current_status} />
                {asset.open_work_orders > 0 ? <Badge value={`${asset.open_work_orders} open WO`} /> : null}
                {asset.is_archived ? null : (
                  <button
                    type="button"
                    onClick={() => {
                      setForm({
                        operational_status: asset.operational_status || "available",
                        warehouse_id: asset.warehouse_id || warehouses[0]?.id || "",
                        notes: "",
                      });
                      setModal("status");
                    }}
                    className="text-xs font-semibold text-teal-800 hover:underline"
                  >
                    Change status
                  </button>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-500">Initial purchase cost</dt>
              <dd className="mt-1 text-sm text-slate-900">{money(asset.initial_purchase_cost)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-500">Maintenance status</dt>
              <dd className="mt-1 text-sm text-slate-900">{openOrders.length ? `${openOrders.length} open work order(s)` : "No open maintenance"}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-500">Lifetime maintenance cost</dt>
              <dd className="mt-1 text-sm text-slate-900">{money(lifetimeCost || asset.total_repair_cost || 0)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-500">Maintenance as % of purchase</dt>
              <dd className={`mt-1 text-sm font-semibold ${asset.lemon_flag ? "text-red-700" : "text-slate-900"}`}>
                {asset.maintenance_cost_pct != null ? `${Number(asset.maintenance_cost_pct).toFixed(1)}%` : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-500">Recorded WO downtime</dt>
              <dd className="mt-1 text-sm text-slate-900">{hours(totalDowntime || profile.total_downtime_hours)}</dd>
              <p className="mt-1 text-xs text-slate-500">Sum of work-order downtime hours. Utilization High Downtime is a separate 90-day lifecycle rule.</p>
            </div>
          </dl>
        </div>
        {asset.lemon_flag ? (
          <p className="mt-4 text-sm font-semibold text-red-700">
            Critical replacement warning: lifetime maintenance cost has reached or exceeded initial purchase cost.
          </p>
        ) : null}
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <AssetUtilization assetId={asset.id} />
          <AssetDocuments assetId={asset.id} />
        </div>
        <div className="mt-6 flex flex-wrap gap-2">
          {[
            ["status", "Change Status"],
            ["edit", "Edit Asset Details"],
            ["move", "Move / Transfer Asset"],
            ["start", "Start Deployment"],
            ["end", "End Deployment"],
            ["wo", "Create Maintenance Work Order"],
          ].map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => {
                setForm(
                  key === "status"
                    ? {
                        operational_status: asset.operational_status || "available",
                        warehouse_id: asset.warehouse_id || warehouses[0]?.id || "",
                        notes: "",
                      }
                    : key === "edit"
                    ? {
                        vin: asset.vin,
                        license_plate: asset.license_plate || "",
                        license_plate_state: asset.license_plate_state || "",
                        make_model: asset.make_model,
                        initial_purchase_cost: asset.initial_purchase_cost,
                        asset_type: asset.asset_type,
                      }
                    : key === "move" || key === "start"
                      ? {
                          custody_type: asset.current_custody_type || "Customer / LE Agency",
                          agency_id: asset.agency_id || "",
                          warehouse_id: asset.warehouse_id || "",
                          address: "",
                          field_site: false,
                          carrier_name: "",
                          tracking_code: "",
                          status: "deployed",
                        }
                      : key === "end"
                        ? { 
                            disposition: "return_to_warehouse",
                            completion_notes: "",
                            destination_warehouse_id: warehouses[0]?.id || "",
                            set_available: false,
                          }
                        : { title: "", description: "", status: "open", repair_channel: "Internal", vendor_name: "", labor_cost: "0", parts_cost: "0" }
                );
                setModal(key);
              }}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-50"
            >
              {label}
            </button>
          ))}
          <button type="button" onClick={startInspection} className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800">
            Run Inspection
          </button>
          <button type="button" onClick={() => setConfirm("archive")} className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-900">
            Retire / Archive Asset
          </button>
        </div>
      </div>

      {openOrders.length ? (
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-800">Open maintenance</h3>
          <ul className="mt-3 space-y-3">
            {openOrders.map((wo) => (
              <li key={wo.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 p-3">
                <div>
                  <Link to={`/maintenance/${wo.id}`} className="font-medium text-teal-800 hover:underline">
                    {wo.title}
                  </Link>
                  <p className="text-xs text-slate-500">
                    {money(Number(wo.total_repair_cost || Number(wo.labor_cost) + Number(wo.parts_cost)))} · <Badge value={wo.status} />
                  </p>
                </div>
                <div className="flex gap-2 text-xs font-semibold">
                  <Link to={`/maintenance/${wo.id}`} className="text-teal-800 hover:underline">
                    Open detail
                  </Link>
                  <button
                    type="button"
                    onClick={() => {
                      setForm({ ...wo });
                      setModal("updateWo");
                    }}
                    className="text-slate-700 hover:underline"
                  >
                    Update
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setForm({ id: wo.id, labor_cost: "0", parts_cost: "0", notes: "" });
                      setModal("cost");
                    }}
                    className="text-slate-700 hover:underline"
                  >
                    Add Repair Cost
                  </button>
                  <button type="button" onClick={() => run(`/work-orders/${wo.id}/close`, { method: "POST" }, "Work order closed.")} className="text-slate-700 hover:underline">
                    Close
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">Historical work orders</h3>
        {historicalOrders.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">No completed or cancelled work orders yet.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {historicalOrders.map((wo) => (
              <li key={wo.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 p-3">
                <Link to={`/maintenance/${wo.id}`} className="font-medium text-teal-800 hover:underline">
                  {wo.title}
                </Link>
                <span className="text-xs text-slate-600">
                  <Badge value={wo.status} /> · {money(wo.total_repair_cost || 0)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-slate-800">Digital inspection</h3>
          <button type="button" onClick={startInspection} className="text-sm font-semibold text-teal-800 hover:underline">
            Start / resume checklist
          </button>
        </div>
        <InspectionChecklist inspection={inspection} onMark={onMark} busyId={busyId} />
      </div>

      <div>
        <h3 className="mb-3 text-sm font-semibold text-slate-800">Lifecycle timeline</h3>
        {profile.events.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-sm text-slate-500">No historical events for this unit yet.</p>
        ) : (
          <ol className="space-y-3">
            {profile.events.map((event) => (
              <li key={`${event.event_type}-${event.id}`} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge value={event.event_type} />
                  <Badge value={event.status} />
                  <h4 className="font-medium text-slate-900">{event.title}</h4>
                </div>
                <p className="mt-2 text-sm text-slate-600">
                  {event.location ? `${event.location} · ` : ""}
                  {event.started_at}
                  {event.ended_at ? ` → ${event.ended_at}` : " → current"}
                  {event.downtime_hours != null ? ` · ${hours(event.downtime_hours)} downtime` : ""}
                </p>
                {event.notes ? <p className="mt-1 text-sm text-slate-500">{event.notes}</p> : null}
              </li>
            ))}
          </ol>
        )}
      </div>

      {modal === "edit" ? (
        <Modal title="Edit Asset Details" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                `/assets/${asset.id}`,
                {
                  method: "PATCH",
                  body: JSON.stringify({
                    vin: form.vin,
                    license_plate: form.license_plate || null,
                    license_plate_state: form.license_plate_state || null,
                    make_model: form.make_model,
                    initial_purchase_cost: Number(form.initial_purchase_cost),
                    asset_type: form.asset_type,
                  }),
                },
                "Asset details saved."
              );
            }}
          >
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
                    value={form.license_plate || ""}
                    onChange={(e) => setForm({ ...form, license_plate: e.target.value.toUpperCase() })}
                  />
                </Field>
              </div>
              <Field label="State">
                <input
                  maxLength={2}
                  className={`${inputClass} uppercase`}
                  placeholder="AZ"
                  value={form.license_plate_state || ""}
                  onChange={(e) => setForm({ ...form, license_plate_state: e.target.value.toUpperCase() })}
                />
              </Field>
            </div>
            <Field label="Make / model">
              <input required className={inputClass} value={form.make_model} onChange={(e) => setForm({ ...form, make_model: e.target.value })} />
            </Field>
            <Field label="Type">
              <select className={inputClass} value={form.asset_type} onChange={(e) => setForm({ ...form, asset_type: e.target.value })}>
                {ASSET_TYPES.map((type) => (
                  <option key={type}>{type}</option>
                ))}
              </select>
            </Field>
            <Field label="Purchase cost">
              <input required type="number" min="1" step="0.01" className={inputClass} value={form.initial_purchase_cost} onChange={(e) => setForm({ ...form, initial_purchase_cost: e.target.value })} />
            </Field>
            <button disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Save
            </button>
          </form>
        </Modal>
      ) : null}

      {modal === "status" ? (
        <Modal title="Change Status" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                `/assets/${asset.id}/operational-status`,
                {
                  method: "POST",
                  body: JSON.stringify({
                    operational_status: form.operational_status,
                    warehouse_id: form.warehouse_id || null,
                    notes: form.notes || null,
                  }),
                },
                "Status updated across dashboard, map, and lists."
              );
            }}
          >
            <p className="text-sm text-slate-600">
              Correct Available, Maintenance, or Retired. Temporary downtime is Maintenance. Use Start Deployment or Move / Transfer for Deployed and In Transit.
            </p>
            <Field label="Operational status">
              <select
                className={inputClass}
                value={form.operational_status}
                onChange={(e) => setForm({ ...form, operational_status: e.target.value })}
              >
                {WAREHOUSE_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {status.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Warehouse">
              <select
                className={inputClass}
                value={form.warehouse_id || ""}
                onChange={(e) => setForm({ ...form, warehouse_id: e.target.value })}
              >
                <option value="">Keep current location</option>
                {warehouses.map((warehouse) => (
                  <option key={warehouse.id} value={warehouse.id}>
                    {warehouse.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Notes">
              <textarea className={inputClass} rows={3} value={form.notes || ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </Field>
            <button disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Save status
            </button>
          </form>
        </Modal>
      ) : null}

      {modal === "move" || modal === "start" ? (
        <Modal title={modal === "move" ? "Move / Transfer Asset" : "Start Deployment"} onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              const site = SITE_FIELD[form.custody_type];
              const chosen = site
                ? (site.directory === "agencies" ? agencies : warehouses).find((row) => row.id === form[site.key])
                : null;
              const payload = {
                custody_type: form.custody_type,
                // The server renames this from whichever site is linked; it is
                // sent so a custody move without a directory entry still works.
                location: chosen
                  ? `${chosen.name}${chosen.site_name ? ` - ${chosen.site_name}` : ""}`
                  : asset.current_location,
                agency_id: form.agency_id || null,
                warehouse_id: form.warehouse_id || null,
                address: form.field_site ? form.address || null : null,
                carrier_name: form.carrier_name || null,
                tracking_code: form.tracking_code || null,
                notes: modal === "start" ? "Deployment started from asset profile." : "Custody transfer from asset profile.",
              };
              if (modal === "start") {
                run(
                  `/assets/${asset.id}/deployments`,
                  { method: "POST", body: JSON.stringify({ ...payload, status: form.status || "deployed" }) },
                  "Deployment started. Prior assignment was closed and kept in history."
                );
              } else {
                run(`/assets/${asset.id}/custody`, { method: "POST", body: JSON.stringify(payload) }, "Transfer recorded as a new history row.");
              }
            }}
          >
            <Field label="Custody type">
              <select className={inputClass} value={form.custody_type} onChange={(e) => setForm({ ...form, custody_type: e.target.value, agency_id: "", warehouse_id: "" })}>
                {CUSTODY_TYPES.map((type) => (
                  <option key={type}>{type}</option>
                ))}
              </select>
            </Field>
            {SITE_FIELD[form.custody_type] ? (
              <Field label={SITE_FIELD[form.custody_type].label}>
                <select
                  required
                  className={inputClass}
                  value={form[SITE_FIELD[form.custody_type].key] || ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      agency_id: "",
                      warehouse_id: "",
                      [SITE_FIELD[form.custody_type].key]: e.target.value,
                    })
                  }
                >
                  <option value="">Select {SITE_FIELD[form.custody_type].label.toLowerCase()}</option>
                  {(SITE_FIELD[form.custody_type].directory === "agencies" ? agencies : warehouses).map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}{row.site_name ? ` - ${row.site_name}` : ""}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-slate-500">The location and map pin come from this record.</p>
              </Field>
            ) : null}
            {modal === "start" ? (
              <Field label="Deployment status">
                <select className={inputClass} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                  {DEPLOYMENT_STATUSES.map((status) => (
                    <option key={status}>{status}</option>
                  ))}
                </select>
              </Field>
            ) : null}
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={form.field_site || false}
                onChange={(e) => setForm({ ...form, field_site: e.target.checked })}
              />
              Unit sits at a specific field site, not the address on file
            </label>
            {form.field_site ? (
              <Field label="Field site address">
                <input
                  className={inputClass}
                  placeholder="Street, city, state — geocoded for the map"
                  value={form.address || ""}
                  onChange={(e) => setForm({ ...form, address: e.target.value })}
                />
              </Field>
            ) : null}
            <Field label="3PL carrier">
              <input disabled={form.custody_type !== "In Transit"} className={inputClass} value={form.carrier_name || ""} onChange={(e) => setForm({ ...form, carrier_name: e.target.value })} />
            </Field>
            <Field label="Tracking code">
              <input disabled={form.custody_type !== "In Transit"} className={inputClass} value={form.tracking_code || ""} onChange={(e) => setForm({ ...form, tracking_code: e.target.value })} />
            </Field>
            <button disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Save
            </button>
          </form>
        </Modal>
      ) : null}

      {modal === "end" ? (
        <EndDeploymentModal
          form={form}
          setForm={setForm}
          warehouses={warehouses}
          agencies={agencies}
          busy={busy}
          onClose={() => setModal(null)}
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              const payload = {
                disposition: form.disposition,
                completion_notes: form.completion_notes,
              };

              if (form.disposition === "return_to_warehouse") {
                payload.destination_warehouse_id = form.destination_warehouse_id;
                payload.set_available = form.set_available || false;
              } else if (form.disposition === "transfer_to_agency") {
                payload.next_agency_id = form.next_agency_id;
                payload.next_deployment_location = form.next_deployment_location;
                payload.next_deployment_notes = form.next_deployment_notes;
              } else if (form.disposition === "in_transit") {
                payload.transit_origin = form.transit_origin;
                payload.transit_destination = form.transit_destination;
                payload.carrier_name = form.carrier_name;
                payload.tracking_code = form.tracking_code;
                payload.departure_date = form.departure_date;
                payload.expected_arrival_date = form.expected_arrival_date;
              } else if (form.disposition === "maintenance") {
                payload.maintenance_warehouse_id = form.maintenance_warehouse_id;
                payload.create_work_order = form.create_work_order || false;
                payload.work_order_title = form.work_order_title;
                payload.work_order_description = form.work_order_description;
              } else if (form.disposition === "retired") {
                payload.destination_warehouse_id = form.destination_warehouse_id || null;
                payload.out_of_service_reason = form.out_of_service_reason;
              }

              await api(`/assets/${asset.id}/deployments/end-workflow`, {
                method: "POST",
                body: JSON.stringify(payload),
              });
              setSuccess("Deployment ended successfully.");
              setModal(null);
              await load();
            } catch (err) {
              setError(err.message);
            } finally {
              setBusy(false);
            }
          }}
        />
      ) : null}

      {modal === "wo" ? (
        <Modal title="Create Maintenance Work Order" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                "/work-orders",
                {
                  method: "POST",
                  body: JSON.stringify({
                    asset_id: asset.id,
                    title: form.title,
                    description: form.description,
                    status: form.status,
                    repair_channel: form.repair_channel,
                    vendor_name: form.vendor_name || null,
                    labor_cost: Number(form.labor_cost || 0),
                    parts_cost: Number(form.parts_cost || 0),
                  }),
                },
                "Work order created."
              );
            }}
          >
            <Field label="Title">
              <input required className={inputClass} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </Field>
            <Field label="Description">
              <textarea className={inputClass} value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <Field label="Status">
              <select className={inputClass} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {WORK_ORDER_STATUSES.map((status) => (
                  <option key={status}>{status}</option>
                ))}
              </select>
            </Field>
            <Field label="Repair channel">
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
              <Field label="Labor cost">
                <input type="number" min="0" step="0.01" className={inputClass} value={form.labor_cost} onChange={(e) => setForm({ ...form, labor_cost: e.target.value })} />
              </Field>
              <Field label="Parts cost">
                <input type="number" min="0" step="0.01" className={inputClass} value={form.parts_cost} onChange={(e) => setForm({ ...form, parts_cost: e.target.value })} />
              </Field>
            </div>
            <button disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Create
            </button>
          </form>
        </Modal>
      ) : null}

      {modal === "updateWo" ? (
        <Modal title="Update Work Order" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                `/work-orders/${form.id}`,
                {
                  method: "PATCH",
                  body: JSON.stringify({
                    title: form.title,
                    description: form.description,
                    status: form.status,
                    repair_channel: form.repair_channel,
                    vendor_name: form.vendor_name,
                    labor_cost: Number(form.labor_cost),
                    parts_cost: Number(form.parts_cost),
                    downtime_hours: Number(form.downtime_hours || 0),
                  }),
                },
                "Work order updated."
              );
            }}
          >
            <Field label="Title">
              <input required className={inputClass} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </Field>
            <Field label="Status">
              <select className={inputClass} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {WORK_ORDER_STATUSES.map((status) => (
                  <option key={status}>{status}</option>
                ))}
              </select>
            </Field>
            <Field label="Description">
              <textarea className={inputClass} value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <button disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Save
            </button>
          </form>
        </Modal>
      ) : null}

      {modal === "cost" ? (
        <Modal title="Add Repair Cost" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                `/work-orders/${form.id}/costs`,
                {
                  method: "POST",
                  body: JSON.stringify({
                    labor_cost: Number(form.labor_cost || 0),
                    parts_cost: Number(form.parts_cost || 0),
                    notes: form.notes,
                  }),
                },
                "Repair cost added."
              );
            }}
          >
            <Field label="Labor">
              <input type="number" min="0" step="0.01" className={inputClass} value={form.labor_cost} onChange={(e) => setForm({ ...form, labor_cost: e.target.value })} />
            </Field>
            <Field label="Parts">
              <input type="number" min="0" step="0.01" className={inputClass} value={form.parts_cost} onChange={(e) => setForm({ ...form, parts_cost: e.target.value })} />
            </Field>
            <Field label="Notes">
              <input className={inputClass} value={form.notes || ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </Field>
            <button disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
              Add cost
            </button>
          </form>
        </Modal>
      ) : null}

      {confirm === "archive" ? (
        <Modal title="Retire / Archive Asset" onClose={() => setConfirm(null)}>
          <p className="text-sm text-slate-700">Archive this unit? The current assignment is closed and the lifecycle timeline is retained.</p>
          <div className="mt-4 flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => run(`/assets/${asset.id}/archive`, { method: "POST" }, "Asset archived.").then(() => setConfirm(null))}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white"
            >
              Confirm archive
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

function EndDeploymentModal({ form, setForm, warehouses, agencies, busy, onClose, onSubmit }) {
  return (
    <Modal title="End Deployment" onClose={onClose} size="large">
      <form className="space-y-4" onSubmit={onSubmit}>
        <div className="rounded-lg bg-slate-50 p-3">
          <p className="text-sm font-medium text-slate-700">
            Where is the asset going next?
          </p>
        </div>

        <Field label="Next Disposition">
          <select
            required
            className={inputClass}
            value={form.disposition}
            onChange={(e) => setForm({ ...form, disposition: e.target.value })}
          >
            <option value="return_to_warehouse">Return to Warehouse</option>
            <option value="transfer_to_agency">Transfer to Another Customer / Agency</option>
            <option value="in_transit">In Transit</option>
            <option value="maintenance">Send to Maintenance</option>
            <option value="retired">Retire</option>
          </select>
        </Field>

        {form.disposition === "return_to_warehouse" && (
          <>
            <Field label="Destination Warehouse">
              <select
                required
                className={inputClass}
                value={form.destination_warehouse_id}
                onChange={(e) => setForm({ ...form, destination_warehouse_id: e.target.value })}
              >
                {warehouses.map((wh) => (
                  <option key={wh.id} value={wh.id}>
                    {wh.name}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center">
              <input
                type="checkbox"
                checked={form.set_available || false}
                onChange={(e) => setForm({ ...form, set_available: e.target.checked })}
                className="mr-2 h-4 w-4 rounded border-slate-300 text-teal-700"
              />
              <span className="text-sm font-medium text-slate-700">Set asset status to Available</span>
            </label>
          </>
        )}

        {form.disposition === "transfer_to_agency" && (
          <>
            <Field label="Next Customer / Agency">
              <select
                required
                className={inputClass}
                value={form.next_agency_id}
                onChange={(e) => setForm({ ...form, next_agency_id: e.target.value })}
              >
                {agencies.map((agency) => (
                  <option key={agency.id} value={agency.id}>
                    {agency.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Next Deployment Location">
              <input
                className={inputClass}
                value={form.next_deployment_location || ""}
                onChange={(e) => setForm({ ...form, next_deployment_location: e.target.value })}
              />
            </Field>
            <Field label="Next Deployment Notes">
              <textarea
                className={inputClass}
                value={form.next_deployment_notes || ""}
                onChange={(e) => setForm({ ...form, next_deployment_notes: e.target.value })}
              />
            </Field>
          </>
        )}

        {form.disposition === "in_transit" && (
          <>
            <Field label="Transit Origin">
              <input
                className={inputClass}
                value={form.transit_origin || ""}
                onChange={(e) => setForm({ ...form, transit_origin: e.target.value })}
              />
            </Field>
            <Field label="Transit Destination">
              <input
                required
                className={inputClass}
                value={form.transit_destination || ""}
                onChange={(e) => setForm({ ...form, transit_destination: e.target.value })}
              />
            </Field>
            <Field label="3PL Carrier">
              <input
                required
                className={inputClass}
                value={form.carrier_name || ""}
                onChange={(e) => setForm({ ...form, carrier_name: e.target.value })}
              />
            </Field>
            <Field label="Tracking Code">
              <input
                required
                className={inputClass}
                value={form.tracking_code || ""}
                onChange={(e) => setForm({ ...form, tracking_code: e.target.value })}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Departure Date">
                <input
                  type="date"
                  className={inputClass}
                  value={form.departure_date || ""}
                  onChange={(e) => setForm({ ...form, departure_date: e.target.value })}
                />
              </Field>
              <Field label="Expected Arrival">
                <input
                  type="date"
                  className={inputClass}
                  value={form.expected_arrival_date || ""}
                  onChange={(e) => setForm({ ...form, expected_arrival_date: e.target.value })}
                />
              </Field>
            </div>
          </>
        )}

        {form.disposition === "maintenance" && (
          <>
            <Field label="Maintenance Location (Optional)">
              <select
                className={inputClass}
                value={form.maintenance_warehouse_id || ""}
                onChange={(e) => setForm({ ...form, maintenance_warehouse_id: e.target.value })}
              >
                <option value="">— Select Warehouse —</option>
                {warehouses.map((wh) => (
                  <option key={wh.id} value={wh.id}>
                    {wh.name}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center">
              <input
                type="checkbox"
                checked={form.create_work_order || false}
                onChange={(e) => setForm({ ...form, create_work_order: e.target.checked })}
                className="mr-2 h-4 w-4 rounded border-slate-300 text-teal-700"
              />
              <span className="text-sm font-medium text-slate-700">Create maintenance work order</span>
            </label>
            {form.create_work_order && (
              <>
                <Field label="Work Order Title">
                  <input
                    required
                    className={inputClass}
                    value={form.work_order_title || ""}
                    onChange={(e) => setForm({ ...form, work_order_title: e.target.value })}
                  />
                </Field>
                <Field label="Work Order Description">
                  <textarea
                    className={inputClass}
                    value={form.work_order_description || ""}
                    onChange={(e) => setForm({ ...form, work_order_description: e.target.value })}
                  />
                </Field>
              </>
            )}
          </>
        )}

        {form.disposition === "retired" && (
          <>
            <Field label="Warehouse (optional)">
              <select
                className={inputClass}
                value={form.destination_warehouse_id || ""}
                onChange={(e) => setForm({ ...form, destination_warehouse_id: e.target.value })}
              >
                <option value="">No warehouse</option>
                {warehouses.map((wh) => (
                  <option key={wh.id} value={wh.id}>
                    {wh.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Retirement notes">
              <textarea
                className={inputClass}
                value={form.out_of_service_reason || ""}
                onChange={(e) => setForm({ ...form, out_of_service_reason: e.target.value })}
              />
            </Field>
          </>
        )}

        <Field label="Completion Notes">
          <textarea
            className={inputClass}
            value={form.completion_notes || ""}
            onChange={(e) => setForm({ ...form, completion_notes: e.target.value })}
          />
        </Field>

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white hover:bg-red-800"
          >
            End Deployment
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-slate-200 px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-300"
          >
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
