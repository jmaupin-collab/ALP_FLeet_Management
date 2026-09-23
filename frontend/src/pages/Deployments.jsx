import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DataTable } from "../components/DataTable";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import { ASSET_TYPES, DEPLOYMENT_STATUSES, OPERATIONAL_STATUSES } from "../lib/constants.js";
import { Badge } from "../lib/format.jsx";

// The deployment location is the site you pick, so each custody type points at
// the directory it belongs to instead of asking for the name a second time.
const SITE_FIELD = {
  "Customer / LE Agency": { key: "agency_id", label: "Customer / Agency", directory: "agencies" },
  "In Transit": { key: "warehouse_id", label: "Destination warehouse", directory: "warehouses" },
  "Warehouse Depot": { key: "warehouse_id", label: "Warehouse", directory: "warehouses" },
};

export default function Deployments() {
  const [rows, setRows] = useState([]);
  const [filteredRows, setFilteredRows] = useState([]);
  const [assets, setAssets] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [agencies, setAgencies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({ asset_id: "", location: "", notes: "", address: "", latitude: "", longitude: "" });
  const [busy, setBusy] = useState(false);
  
  // Filters
  const [filters, setFilters] = useState({
    assetType: "",
    deploymentStatus: "",
    agencyId: "",
    warehouseId: "",
    startDateFrom: "",
    startDateTo: "",
    inTransitOnly: false,
    maintenanceOnly: false,
  });

  async function load() {
    setLoading(true);
    try {
      const [deployments, fleet, warehouseList, agencyList] = await Promise.all([
        api("/deployments"),
        api("/assets"),
        api("/warehouses"),
        api("/agencies"),
      ]);
      setRows(deployments);
      setAssets(fleet);
      setWarehouses(warehouseList);
      setAgencies(agencyList);
      applyFilters(deployments, filters);
    } catch (err) {
      setError(err.message);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    applyFilters(rows, filters);
  }, [filters, rows]);

  function applyFilters(data, currentFilters) {
    let filtered = [...data];

    if (currentFilters.assetType) {
      filtered = filtered.filter((r) => r.asset_type === currentFilters.assetType);
    }

    if (currentFilters.deploymentStatus) {
      filtered = filtered.filter((r) => r.status === currentFilters.deploymentStatus);
    }

    if (currentFilters.agencyId) {
      filtered = filtered.filter((r) => {
        const asset = assets.find((a) => a.id === r.asset_id);
        return asset?.agency_id === currentFilters.agencyId;
      });
    }

    if (currentFilters.warehouseId) {
      filtered = filtered.filter((r) => {
        const asset = assets.find((a) => a.id === r.asset_id);
        return asset?.warehouse_id === currentFilters.warehouseId;
      });
    }

    if (currentFilters.startDateFrom) {
      filtered = filtered.filter((r) => new Date(r.started_at) >= new Date(currentFilters.startDateFrom));
    }

    if (currentFilters.startDateTo) {
      filtered = filtered.filter((r) => new Date(r.started_at) <= new Date(currentFilters.startDateTo));
    }

    if (currentFilters.inTransitOnly) {
      filtered = filtered.filter((r) => r.custody_type === "In Transit");
    }

    if (currentFilters.maintenanceOnly) {
      filtered = filtered.filter((r) => {
        const asset = assets.find((a) => a.id === r.asset_id);
        return String(asset?.operational_status || "").toLowerCase() === "maintenance" || asset?.open_work_orders > 0;
      });
    }

    setFilteredRows(filtered);
  }

  function clearFilters() {
    setFilters({
      assetType: "",
      deploymentStatus: "",
      agencyId: "",
      warehouseId: "",
      startDateFrom: "",
      startDateTo: "",
      inTransitOnly: false,
      maintenanceOnly: false,
    });
  }

  // Calculate summary metrics
  const activeDeployments = filteredRows.filter((r) => !r.ended_at).length;
  const scheduledDeployments = filteredRows.filter((r) => r.status === "scheduled").length;
  const inTransit = filteredRows.filter((r) => r.custody_type === "In Transit" && !r.ended_at).length;
  const completedDeployments = filteredRows.filter((r) => r.status === "completed").length;
  const availableForDeployment = assets.filter(
    (a) => String(a.operational_status || "").toLowerCase() === "available" && !a.is_archived
  ).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Deployments</h2>
          <p className="mt-1 text-sm text-slate-600">
            Manage asset deployments, custody transfers, and lifecycle workflows.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setForm({
              asset_id: assets.find((a) => String(a.operational_status || "").toLowerCase() === "available")?.id || assets[0]?.id || "",
              notes: "",
              address: "",
              latitude: "",
              longitude: "",
              custody_type: "Customer / LE Agency",
              agency_id: "",
              warehouse_id: "",
              field_site: false,
              carrier_name: "",
              tracking_code: "",
            });
            setModal("start");
          }}
          className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800"
        >
          Start Deployment
        </button>
      </div>

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        <div className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-slate-200">
          <p className="text-sm font-medium text-slate-600">Active</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{activeDeployments}</p>
        </div>
        <div className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-slate-200">
          <p className="text-sm font-medium text-slate-600">Scheduled</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{scheduledDeployments}</p>
        </div>
        <div className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-slate-200">
          <p className="text-sm font-medium text-slate-600">In Transit</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{inTransit}</p>
        </div>
        <div className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-slate-200">
          <p className="text-sm font-medium text-slate-600">Completed</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{completedDeployments}</p>
        </div>
        <div className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-slate-200">
          <p className="text-sm font-medium text-slate-600">Available</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{availableForDeployment}</p>
        </div>
      </div>

      {/* Filters */}
      <div className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-slate-200">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-medium text-slate-900">Filters</h3>
          <button
            type="button"
            onClick={clearFilters}
            className="text-sm font-medium text-teal-700 hover:text-teal-800"
          >
            Clear All
          </button>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Asset Type</label>
            <select
              className={inputClass}
              value={filters.assetType}
              onChange={(e) => setFilters({ ...filters, assetType: e.target.value })}
            >
              <option value="">All Types</option>
              {ASSET_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Deployment Status</label>
            <select
              className={inputClass}
              value={filters.deploymentStatus}
              onChange={(e) => setFilters({ ...filters, deploymentStatus: e.target.value })}
            >
              <option value="">All Statuses</option>
              <option value="scheduled">Scheduled</option>
              <option value="active">Active</option>
              <option value="completed">Completed</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Customer / Agency</label>
            <select
              className={inputClass}
              value={filters.agencyId}
              onChange={(e) => setFilters({ ...filters, agencyId: e.target.value })}
            >
              <option value="">All Agencies</option>
              {agencies.map((agency) => (
                <option key={agency.id} value={agency.id}>
                  {agency.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Warehouse / Depot</label>
            <select
              className={inputClass}
              value={filters.warehouseId}
              onChange={(e) => setFilters({ ...filters, warehouseId: e.target.value })}
            >
              <option value="">All Warehouses</option>
              {warehouses.map((wh) => (
                <option key={wh.id} value={wh.id}>
                  {wh.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Start Date From</label>
            <input
              type="date"
              className={inputClass}
              value={filters.startDateFrom}
              onChange={(e) => setFilters({ ...filters, startDateFrom: e.target.value })}
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Start Date To</label>
            <input
              type="date"
              className={inputClass}
              value={filters.startDateTo}
              onChange={(e) => setFilters({ ...filters, startDateTo: e.target.value })}
            />
          </div>
          <div className="flex items-center space-x-4">
            <label className="flex items-center">
              <input
                type="checkbox"
                checked={filters.inTransitOnly}
                onChange={(e) => setFilters({ ...filters, inTransitOnly: e.target.checked })}
                className="mr-2 h-4 w-4 rounded border-slate-300 text-teal-700 focus:ring-teal-600"
              />
              <span className="text-sm font-medium text-slate-700">In Transit Only</span>
            </label>
          </div>
          <div className="flex items-center space-x-4">
            <label className="flex items-center">
              <input
                type="checkbox"
                checked={filters.maintenanceOnly}
                onChange={(e) => setFilters({ ...filters, maintenanceOnly: e.target.checked })}
                className="mr-2 h-4 w-4 rounded border-slate-300 text-teal-700 focus:ring-teal-600"
              />
              <span className="text-sm font-medium text-slate-700">Maintenance Only</span>
            </label>
          </div>
        </div>
      </div>

      <Notice error={error} success={success} />
      {loading ? <p className="text-sm text-slate-600">Loading deployments…</p> : null}
      
      <DataTable
        columns={[
          {
            key: "id",
            header: "Asset ID",
            render: (r) => {
              const asset = assets.find(a => a.id === r.asset_id);
              return asset ? (
                <span className="font-mono text-xs">{asset.vin.slice(-4)}</span>
              ) : (
                <span className="font-mono text-xs text-slate-400">—</span>
              );
            },
          },
          {
            key: "asset",
            header: "Asset",
            render: (r) => (
              <Link to={`/assets/${r.asset_id}`} className="text-teal-800 hover:underline">
                {r.asset}
              </Link>
            ),
          },
          { key: "asset_type", header: "Type", render: (r) => <Badge value={r.asset_type} tone="type" /> },
          {
            key: "custody_type",
            header: "Custody",
            render: (r) => (r.custody_type ? <Badge value={r.custody_type} /> : "—"),
          },
          { key: "location", header: "Location / party" },
          {
            key: "tracking_code",
            header: "3PL tracking",
            render: (r) =>
              r.tracking_code ? (
                <span className="font-mono text-xs">
                  {r.carrier_name} · {r.tracking_code}
                </span>
              ) : (
                "—"
              ),
          },
          { key: "status", header: "Deployment", render: (r) => <Badge value={r.status} /> },
          { 
            key: "operational_status", 
            header: "Asset Status", 
            render: (r) => {
              const asset = assets.find(a => a.id === r.asset_id);
              return asset?.operational_status ? (
                <Badge value={asset.operational_status} />
              ) : <span className="text-slate-400">—</span>;
            }
          },
          {
            key: "started_at",
            header: "Started",
            render: (r) => new Date(r.started_at).toLocaleDateString(),
          },
          {
            key: "ended_at",
            header: "Ended",
            render: (r) => (r.ended_at ? new Date(r.ended_at).toLocaleDateString() : "active"),
          },
          {
            key: "actions",
            header: "",
            render: (r) =>
              !r.ended_at ? (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="text-xs font-semibold text-teal-800 hover:underline"
                    onClick={() => {
                      setForm({ id: r.id, notes: r.notes || "", location: r.location });
                      setModal("edit");
                    }}
                  >
                    Edit notes
                  </button>
                  <button
                    type="button"
                    className="text-xs font-semibold text-red-700 hover:underline"
                    onClick={() => {
                      setForm({
                        deployment_id: r.id,
                        asset_id: r.asset_id,
                        disposition: "return_to_warehouse",
                        completion_notes: "",
                        destination_warehouse_id: warehouses[0]?.id || "",
                      });
                      setModal("end");
                    }}
                  >
                    End Deployment
                  </button>
                </div>
              ) : null,
          },
        ]}
        rows={filteredRows}
      />

      {/* Start Deployment Modal */}
      {modal === "start" ? (
        <Modal title="Start Deployment" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                const site = SITE_FIELD[form.custody_type];
                if (site && !form[site.key]) {
                  throw new Error(`Select a ${site.label.toLowerCase()} for this deployment.`);
                }
                await api(`/assets/${form.asset_id}/deployments`, {
                  method: "POST",
                  body: JSON.stringify({
                    custody_type: form.custody_type,
                    notes: form.notes,
                    carrier_name: form.carrier_name || null,
                    tracking_code: form.tracking_code || null,
                    agency_id: form.agency_id || null,
                    warehouse_id: form.warehouse_id || null,
                    // Only sent when the unit is going somewhere other than the
                    // site's own address; otherwise the server uses the site pin.
                    address: form.field_site ? form.address || null : null,
                    latitude: form.field_site && form.latitude ? parseFloat(form.latitude) : null,
                    longitude: form.field_site && form.longitude ? parseFloat(form.longitude) : null,
                  }),
                });
                setSuccess("Deployment started.");
                setModal(null);
                await load();
              } catch (err) {
                setError(err.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Asset ID">
              <select
                required
                className={inputClass}
                value={form.asset_id}
                onChange={(e) => setForm({ ...form, asset_id: e.target.value })}
              >
                {assets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.vin} - {asset.make_model} ({asset.operational_status || "unspecified"})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Custody Type">
              <select
                required
                className={inputClass}
                value={form.custody_type}
                onChange={(e) => setForm({ ...form, custody_type: e.target.value, agency_id: "", warehouse_id: "" })}
              >
                <option value="Customer / LE Agency">Customer / LE Agency</option>
                <option value="In Transit">In Transit</option>
                <option value="Warehouse Depot">Warehouse Depot</option>
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
                <p className="mt-1 text-xs text-slate-500">
                  The deployment location and map pin come from this record.
                </p>
              </Field>
            ) : null}
            {form.custody_type === "In Transit" && (
              <>
                <Field label="3PL Carrier">
                  <input
                    required
                    className={inputClass}
                    placeholder="FedEx, UPS, etc."
                    value={form.carrier_name || ""}
                    onChange={(e) => setForm({ ...form, carrier_name: e.target.value })}
                  />
                </Field>
                <Field label="Tracking Code">
                  <input
                    required
                    className={inputClass}
                    placeholder="1Z999AA10123456784"
                    value={form.tracking_code || ""}
                    onChange={(e) => setForm({ ...form, tracking_code: e.target.value })}
                  />
                </Field>
              </>
            )}
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={form.field_site || false}
                onChange={(e) => setForm({ ...form, field_site: e.target.checked })}
              />
              Unit sits at a specific field site, not the address on file
            </label>
            {form.field_site ? (
              <>
                <Field label="Field site address">
                  <input
                    className={inputClass}
                    placeholder="123 Main St, Phoenix, AZ 85001"
                    value={form.address || ""}
                    onChange={(e) => setForm({ ...form, address: e.target.value })}
                  />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Latitude (optional)">
                    <input
                      type="number"
                      step="any"
                      className={inputClass}
                      placeholder="33.4484"
                      value={form.latitude || ""}
                      onChange={(e) => setForm({ ...form, latitude: e.target.value })}
                    />
                  </Field>
                  <Field label="Longitude (optional)">
                    <input
                      type="number"
                      step="any"
                      className={inputClass}
                      placeholder="-112.0740"
                      value={form.longitude || ""}
                      onChange={(e) => setForm({ ...form, longitude: e.target.value })}
                    />
                  </Field>
                </div>
              </>
            ) : null}
            <Field label="Notes">
              <textarea
                className={inputClass}
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </Field>
            <button
              disabled={busy}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
            >
              Start
            </button>
          </form>
        </Modal>
      ) : null}

      {/* Edit Notes Modal */}
      {modal === "edit" ? (
        <Modal title="Update Deployment Notes" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                await api(`/deployments/${form.id}`, {
                  method: "PATCH",
                  body: JSON.stringify({ notes: form.notes }),
                });
                setSuccess("Deployment notes updated.");
                setModal(null);
                await load();
              } catch (err) {
                setError(err.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Notes">
              <textarea
                className={inputClass}
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </Field>
            <button
              disabled={busy}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
            >
              Save
            </button>
          </form>
        </Modal>
      ) : null}

      {/* End Deployment Modal */}
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

              await api(`/assets/${form.asset_id}/deployments/end-workflow`, {
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
                value={form.next_agency_id || ""}
                onChange={(e) => setForm({ ...form, next_agency_id: e.target.value })}
              >
                <option value="">Select agency</option>
                {agencies.map((agency) => (
                  <option key={agency.id} value={agency.id}>
                    {agency.name}{agency.site_name ? ` - ${agency.site_name}` : ""}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-slate-500">
                The new location and map pin come from this agency.
              </p>
            </Field>
            <Field label="Field site address (optional)">
              <input
                className={inputClass}
                placeholder="Leave blank to use the agency's own address"
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
