import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import AssetPicker from "../components/AssetPicker.jsx";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import {
  FULFILLABLE,
  NEXT_STATUSES,
  NOTIFICATIONS_CHANGED,
  RequestStatusBadge,
  formatDate,
  formatDateTime,
  requestTypeLabel,
} from "../lib/requests.jsx";

// One request, end to end: what was asked for, what staff decided, which
// trailers were picked, and the moment the real deployment workflow ran.
//
// Fulfilling is the only control on this page that touches the fleet, and it
// does so through the ordinary lifecycle endpoint on the server.

export default function RequestDetail() {
  const { requestId } = useParams();
  const [row, setRow] = useState(null);
  const [assets, setAssets] = useState([]);
  const [agencies, setAgencies] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({});
  const [picked, setPicked] = useState([]);

  const load = useCallback(async () => {
    const fresh = await api(`/requests/${requestId}`);
    setRow(fresh);
    setPicked(fresh.assets.map((link) => link.asset_id));
    return fresh;
  }, [requestId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([load(), api("/assets"), api("/agencies"), api("/warehouses")])
      .then(([, fleet, agencyList, warehouseList]) => {
        if (cancelled) return;
        setAssets(fleet);
        setAgencies(agencyList);
        setWarehouses(warehouseList);
        setError("");
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [load]);

  // Only ALPR Trailers can fulfil a request, and the server enforces that too.
  const trailers = useMemo(
    () => assets.filter((asset) => asset.asset_type === "ALPR Trailer" && !asset.is_archived),
    [assets]
  );
  const byId = useMemo(() => new Map(assets.map((asset) => [String(asset.id), asset])), [assets]);

  async function run(work, message) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await work();
      await load();
      // Deciding a request deletes its notification server-side. Tell the bell
      // so it clears now instead of on its next 30-second poll.
      window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED));
      setSuccess(message);
      setModal(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="text-sm text-slate-600">Loading request…</p>;
  if (!row) return <Notice error={error || "Request not found."} />;

  const fulfillable = FULFILLABLE.includes(row.status);
  const editable = !["Completed", "Rejected", "Cancelled"].includes(row.status);
  const pendingCount = row.assets.filter((link) => !link.fulfilled_at).length;
  const lockedIds = new Set(row.assets.filter((link) => link.fulfilled_at).map((link) => link.asset_id));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/requests" className="text-sm text-teal-800 hover:underline">
            ← Request Center
          </Link>
          <h2 className="mt-1 flex items-center gap-3 text-2xl font-semibold text-slate-900">
            <span className="font-mono">{row.reference}</span>
            <RequestStatusBadge value={row.status} />
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            {requestTypeLabel(row.request_type)} · {row.quantity} trailer{row.quantity === 1 ? "" : "s"} ·{" "}
            {row.agency_name}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {(NEXT_STATUSES[row.status] || []).map((next) => (
            <button
              key={next}
              type="button"
              disabled={busy}
              onClick={() => {
                setForm({ status: next, message: "" });
                setModal("status");
              }}
              className={`rounded-lg px-3 py-2 text-sm font-semibold ${
                next === "Rejected" || next === "Cancelled"
                  ? "border border-slate-300 text-slate-700 hover:bg-slate-50"
                  : "bg-teal-700 text-white hover:bg-teal-800"
              }`}
            >
              {next}
            </button>
          ))}
        </div>
      </div>

      <Notice error={error} success={success} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card
            title="Request"
            action={
              editable ? (
                <button
                  type="button"
                  className="text-sm font-semibold text-teal-800 hover:underline"
                  onClick={() => {
                    setForm({
                      agency_name: row.agency_name,
                      agency_id: row.agency_id || "",
                      requester_name: row.requester_name,
                      requester_email: row.requester_email,
                      requester_phone: row.requester_phone || "",
                      requested_date: row.requested_date || "",
                      address: row.address,
                      quantity: row.quantity,
                      review_notes: row.review_notes || "",
                    });
                    setModal("edit");
                  }}
                >
                  Edit
                </button>
              ) : null
            }
          >
            <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
              <Detail label="Requester">
                {row.requester_name}
                <span className="block text-xs text-slate-500">
                  <a href={`mailto:${row.requester_email}`} className="hover:underline">
                    {row.requester_email}
                  </a>
                  {row.requester_phone ? ` · ${row.requester_phone}` : ""}
                </span>
              </Detail>
              <Detail label="Agency / customer">
                {row.agency_name}
                <span className="block text-xs text-slate-500">
                  {row.agency_id
                    ? agencies.find((agency) => agency.id === row.agency_id)?.name || "Linked"
                    : "Not linked to an agency record yet"}
                </span>
              </Detail>
              <Detail label={row.request_type === "pickup" ? "Pickup address" : "Deployment address"}>
                {row.address}
                <span className="block text-xs text-slate-500">
                  {row.latitude && row.longitude
                    ? `${Number(row.latitude).toFixed(5)}, ${Number(row.longitude).toFixed(5)}`
                    : "Not geocoded — the crew will need the address as written."}
                </span>
              </Detail>
              <Detail label="Preferred date">{formatDate(row.requested_date)}</Detail>
              <Detail label="Scheduled for">{formatDate(row.scheduled_date)}</Detail>
              <Detail label="Submitted">{formatDateTime(row.created_at)}</Detail>
            </dl>
            {row.notes ? (
              <div className="mt-5 rounded-lg bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">From the requester</p>
                <p className="mt-1 whitespace-pre-line text-sm text-slate-800">{row.notes}</p>
              </div>
            ) : null}
            {row.review_notes ? (
              <div className="mt-3 rounded-lg bg-amber-50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">Internal notes</p>
                <p className="mt-1 whitespace-pre-line text-sm text-slate-800">{row.review_notes}</p>
              </div>
            ) : null}
          </Card>

          <Card
            title="Trailers"
            action={
              fulfillable ? (
                <button
                  type="button"
                  className="text-sm font-semibold text-teal-800 hover:underline"
                  onClick={() => setModal("assets")}
                >
                  Choose trailers
                </button>
              ) : null
            }
          >
            {row.assets.length === 0 ? (
              <p className="text-sm text-slate-600">
                {fulfillable
                  ? "No trailers picked yet. Choosing a trailer records intent; it does not move anything."
                  : "Approve the request before assigning trailers."}
              </p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {row.assets.map((link) => (
                  <li key={link.id} className="flex items-center justify-between py-2 text-sm">
                    <div>
                      <Link to={`/assets/${link.asset_id}`} className="font-medium text-teal-800 hover:underline">
                        {link.make_model}
                      </Link>
                      <span className="block font-mono text-xs text-slate-500">
                        {link.vin}
                        {link.license_plate ? ` · ${link.license_plate}` : ""}
                      </span>
                    </div>
                    <span className="text-xs text-slate-600">
                      {link.fulfilled_at ? `Sent ${formatDate(link.fulfilled_at)}` : "Not sent yet"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="History">
            <ol className="space-y-4">
              {row.events.map((event) => (
                <li key={event.id} className="border-l-2 border-slate-200 pl-4">
                  <p className="text-sm text-slate-900">
                    {event.message || event.event_type.replaceAll("_", " ")}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500">
                    {formatDateTime(event.created_at)}
                    {event.actor_name ? ` · ${event.actor_name}` : " · public form"}
                    {event.to_status ? ` · → ${event.to_status}` : ""}
                  </p>
                </li>
              ))}
            </ol>
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="Next step">
            {fulfillable ? (
              <div className="space-y-3">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setForm({ scheduled_date: row.scheduled_date || "", message: "" });
                    setModal("schedule");
                  }}
                  className="w-full rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                >
                  {row.scheduled_date ? "Reschedule" : "Schedule a date"}
                </button>
                <button
                  type="button"
                  disabled={busy || pendingCount === 0}
                  onClick={() => {
                    setForm({
                      agency_id: row.agency_id || "",
                      warehouse_id: warehouses[0]?.id || "",
                      carrier_name: "",
                      tracking_code: "",
                      in_transit: false,
                      notes: "",
                    });
                    setModal("fulfill");
                  }}
                  className="w-full rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-50"
                >
                  {row.request_type === "pickup" ? "Run pickup workflow" : "Run deployment workflow"}
                </button>
                <p className="text-xs text-slate-500">
                  {pendingCount === 0
                    ? "Every selected trailer has already gone out on this request."
                    : `This runs the standard ${
                        row.request_type === "pickup" ? "end-deployment" : "deployment"
                      } workflow for ${pendingCount} trailer${pendingCount === 1 ? "" : "s"}.`}
                </p>
              </div>
            ) : (
              <p className="text-sm text-slate-600">
                {editable
                  ? "Approve the request to schedule it and assign trailers."
                  : `This request is ${row.status.toLowerCase()} and is closed to further action.`}
              </p>
            )}
          </Card>

          <Card title="Intake audit">
            <dl className="space-y-3">
              <Detail label="Source address">{row.source_ip || "—"}</Detail>
              <Detail label="Human verification">
                {row.captcha_verified ? "Passed a CAPTCHA challenge" : "CAPTCHA not enabled at submission"}
              </Detail>
              <Detail label="Reviewed">{formatDateTime(row.reviewed_at)}</Detail>
              <Detail label="Completed">{formatDateTime(row.completed_at)}</Detail>
            </dl>
          </Card>
        </div>
      </div>

      {modal === "status" ? (
        <Modal title={`Move to ${form.status}`} onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                () =>
                  api(`/requests/${row.id}/status`, {
                    method: "POST",
                    body: JSON.stringify({ status: form.status, message: form.message || null }),
                  }),
                `Request moved to ${form.status}.`
              );
            }}
          >
            <p className="text-sm text-slate-600">
              {form.status === "Approved"
                ? "Approving records the decision. No trailer is assigned and nothing is deployed yet."
                : "This is recorded in the request history."}
            </p>
            <Field label="Note (optional)">
              <textarea
                className={inputClass}
                rows={3}
                value={form.message}
                onChange={(e) => setForm({ ...form, message: e.target.value })}
              />
            </Field>
            <SubmitRow busy={busy} label={form.status} onCancel={() => setModal(null)} />
          </form>
        </Modal>
      ) : null}

      {modal === "edit" ? (
        <Modal title="Edit request" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                () =>
                  api(`/requests/${row.id}`, {
                    method: "PATCH",
                    body: JSON.stringify({
                      agency_name: form.agency_name,
                      agency_id: form.agency_id || null,
                      requester_name: form.requester_name,
                      requester_email: form.requester_email,
                      requester_phone: form.requester_phone || null,
                      requested_date: form.requested_date || null,
                      address: form.address,
                      quantity: Number(form.quantity),
                      review_notes: form.review_notes || null,
                    }),
                  }),
                "Request updated."
              );
            }}
          >
            <Field label="Agency / customer as typed">
              <input
                className={inputClass}
                value={form.agency_name}
                onChange={(e) => setForm({ ...form, agency_name: e.target.value })}
              />
            </Field>
            <Field label="Link to an agency record">
              <select
                className={inputClass}
                value={form.agency_id}
                onChange={(e) => setForm({ ...form, agency_id: e.target.value })}
              >
                <option value="">Not linked</option>
                {agencies.map((agency) => (
                  <option key={agency.id} value={agency.id}>
                    {agency.name}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-slate-500">
                A deploy request needs a linked agency before it can be fulfilled.
              </p>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Requester">
                <input
                  className={inputClass}
                  value={form.requester_name}
                  onChange={(e) => setForm({ ...form, requester_name: e.target.value })}
                />
              </Field>
              <Field label="Phone">
                <input
                  className={inputClass}
                  value={form.requester_phone}
                  onChange={(e) => setForm({ ...form, requester_phone: e.target.value })}
                />
              </Field>
            </div>
            <Field label="Email">
              <input
                type="email"
                className={inputClass}
                value={form.requester_email}
                onChange={(e) => setForm({ ...form, requester_email: e.target.value })}
              />
            </Field>
            <Field label="Address">
              <input
                className={inputClass}
                value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
              />
              <p className="mt-1 text-xs text-slate-500">Changing this re-geocodes the map pin.</p>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Trailers requested">
                <input
                  type="number"
                  min={1}
                  className={inputClass}
                  value={form.quantity}
                  onChange={(e) => setForm({ ...form, quantity: e.target.value })}
                />
              </Field>
              <Field label="Preferred date">
                <input
                  type="date"
                  className={inputClass}
                  value={form.requested_date}
                  onChange={(e) => setForm({ ...form, requested_date: e.target.value })}
                />
              </Field>
            </div>
            <Field label="Internal notes">
              <textarea
                className={inputClass}
                rows={3}
                value={form.review_notes}
                onChange={(e) => setForm({ ...form, review_notes: e.target.value })}
              />
            </Field>
            <SubmitRow busy={busy} label="Save" onCancel={() => setModal(null)} />
          </form>
        </Modal>
      ) : null}

      {modal === "assets" ? (
        <Modal title="Choose trailers" onClose={() => setModal(null)} wide>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                () =>
                  api(`/requests/${row.id}/assets`, {
                    method: "POST",
                    body: JSON.stringify({ asset_ids: picked }),
                  }),
                "Trailer selection saved."
              );
            }}
          >
            <p className="text-sm text-slate-600">
              The requester asked for {row.quantity}. Picking a trailer reserves it on this request only —
              custody changes when you run the workflow.
            </p>
            <Field label="Add a trailer">
              <AssetPicker
                assets={trailers.filter((asset) => !picked.includes(asset.id))}
                value=""
                onChange={(assetId) => setPicked((current) => [...current, assetId])}
                inputClass={inputClass}
                placeholder="Search by Asset ID, plate, or model"
              />
            </Field>
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {picked.length === 0 ? (
                <li className="px-3 py-4 text-sm text-slate-500">Nothing picked yet.</li>
              ) : null}
              {picked.map((assetId) => {
                const asset = byId.get(String(assetId));
                const locked = lockedIds.has(assetId);
                return (
                  <li key={assetId} className="flex items-center justify-between px-3 py-2 text-sm">
                    <div>
                      <span className="text-slate-900">{asset?.make_model || "Unknown trailer"}</span>
                      <span className="block font-mono text-xs text-slate-500">{asset?.vin}</span>
                    </div>
                    {locked ? (
                      <span className="text-xs text-slate-500">Already sent — cannot remove</span>
                    ) : (
                      <button
                        type="button"
                        className="text-xs font-semibold text-red-700 hover:underline"
                        onClick={() => setPicked((current) => current.filter((id) => id !== assetId))}
                      >
                        Remove
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
            <SubmitRow busy={busy} label="Save selection" onCancel={() => setModal(null)} />
          </form>
        </Modal>
      ) : null}

      {modal === "schedule" ? (
        <Modal title="Schedule this request" onClose={() => setModal(null)}>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                () =>
                  api(`/requests/${row.id}/schedule`, {
                    method: "POST",
                    body: JSON.stringify({
                      scheduled_date: form.scheduled_date,
                      message: form.message || null,
                    }),
                  }),
                "Request scheduled."
              );
            }}
          >
            <Field label="Date">
              <input
                type="date"
                required
                className={inputClass}
                value={form.scheduled_date}
                onChange={(e) => setForm({ ...form, scheduled_date: e.target.value })}
              />
              <p className="mt-1 text-xs text-slate-500">
                Requester asked for {formatDate(row.requested_date)}.
              </p>
            </Field>
            <Field label="Note (optional)">
              <textarea
                className={inputClass}
                rows={3}
                value={form.message}
                onChange={(e) => setForm({ ...form, message: e.target.value })}
              />
            </Field>
            <SubmitRow busy={busy} label="Schedule" onCancel={() => setModal(null)} />
          </form>
        </Modal>
      ) : null}

      {modal === "fulfill" ? (
        <Modal
          title={row.request_type === "pickup" ? "Run pickup workflow" : "Run deployment workflow"}
          onClose={() => setModal(null)}
        >
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                () =>
                  api(`/requests/${row.id}/fulfill`, {
                    method: "POST",
                    body: JSON.stringify({
                      agency_id: form.agency_id || null,
                      warehouse_id: form.warehouse_id || null,
                      carrier_name: form.carrier_name || null,
                      tracking_code: form.tracking_code || null,
                      in_transit: Boolean(form.in_transit),
                      notes: form.notes || null,
                    }),
                  }),
                "Workflow ran. The trailers have moved."
              );
            }}
          >
            <p className="text-sm text-slate-600">
              {pendingCount} trailer{pendingCount === 1 ? "" : "s"} will go through the standard
              {row.request_type === "pickup" ? " end-deployment " : " deployment "}
              workflow, exactly as if you had done it from the Deployments page.
            </p>
            {row.request_type === "deploy" ? (
              <>
                <Field label="Deploy to agency">
                  <select
                    required
                    className={inputClass}
                    value={form.agency_id}
                    onChange={(e) => setForm({ ...form, agency_id: e.target.value })}
                  >
                    <option value="">Select agency</option>
                    {agencies.map((agency) => (
                      <option key={agency.id} value={agency.id}>
                        {agency.name}
                      </option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-slate-500">
                    The map pin uses the request address: {row.address}
                  </p>
                </Field>
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={Boolean(form.in_transit)}
                    onChange={(e) => setForm({ ...form, in_transit: e.target.checked })}
                  />
                  Shipping with a carrier rather than handing over on site
                </label>
                {form.in_transit ? (
                  <>
                    <Field label="Carrier">
                      <input
                        required
                        className={inputClass}
                        placeholder="FedEx, UPS, etc."
                        value={form.carrier_name}
                        onChange={(e) => setForm({ ...form, carrier_name: e.target.value })}
                      />
                    </Field>
                    <Field label="Tracking code">
                      <input
                        className={inputClass}
                        value={form.tracking_code}
                        onChange={(e) => setForm({ ...form, tracking_code: e.target.value })}
                      />
                      <p className="mt-1 text-xs text-slate-500">Optional — add it once the carrier issues one.</p>
                    </Field>
                  </>
                ) : null}
              </>
            ) : (
              <Field label="Returning to warehouse">
                <select
                  required
                  className={inputClass}
                  value={form.warehouse_id}
                  onChange={(e) => setForm({ ...form, warehouse_id: e.target.value })}
                >
                  <option value="">Select warehouse</option>
                  {warehouses.map((warehouse) => (
                    <option key={warehouse.id} value={warehouse.id}>
                      {warehouse.name}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Notes">
              <textarea
                className={inputClass}
                rows={3}
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </Field>
            <SubmitRow busy={busy} label="Run workflow" onCancel={() => setModal(null)} />
          </form>
        </Modal>
      ) : null}
    </div>
  );
}

function Card({ title, action, children }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="font-semibold text-slate-900">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function Detail({ label, children }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 text-sm text-slate-900">{children}</dd>
    </div>
  );
}

function SubmitRow({ busy, label, onCancel }) {
  return (
    <div className="flex gap-3 pt-1">
      <button
        type="submit"
        disabled={busy}
        className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
      >
        {busy ? "Working…" : label}
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
      >
        Cancel
      </button>
    </div>
  );
}
