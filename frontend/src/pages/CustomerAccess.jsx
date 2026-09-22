import { useEffect, useState } from "react";
import { Notice } from "../components/Modal.jsx";
import { api } from "../lib/api.js";

export default function CustomerAccess() {
  const [customers, setCustomers] = useState([]);
  const [assets, setAssets] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [assigned, setAssigned] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [customerRows, assetRows] = await Promise.all([api("/users/customers"), api("/assets")]);
      setCustomers(customerRows);
      setAssets(assetRows.filter((asset) => !asset.is_archived));
    } catch (err) {
      setError(err.message);
      setCustomers([]);
      setAssets([]);
    } finally {
      setLoading(false);
    }
  }

  async function loadAssigned(userId) {
    try {
      const rows = await api(`/users/${userId}/asset-authorizations`);
      setAssigned(rows);
    } catch (err) {
      setError(err.message);
      setAssigned([]);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function assignAsset(assetId) {
    if (!selectedCustomer || !assetId) return;
    setBusy(true);
    setError("");
    try {
      await api(`/users/${selectedCustomer.id}/asset-authorizations`, {
        method: "POST",
        body: JSON.stringify({ asset_id: assetId, can_view: true }),
      });
      setSuccess(`Assigned asset to ${selectedCustomer.full_name}`);
      await loadAssigned(selectedCustomer.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeAsset(assetId) {
    if (!selectedCustomer) return;
    setBusy(true);
    setError("");
    try {
      await api(`/users/${selectedCustomer.id}/asset-authorizations/${assetId}`, { method: "DELETE" });
      setSuccess("Asset access removed");
      await loadAssigned(selectedCustomer.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const assignedIds = new Set(assigned.map((row) => row.asset_id));
  const availableAssets = assets.filter((asset) => !assignedIds.has(asset.id));

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">Customer Asset Access</h2>
        <p className="mt-1 text-sm text-slate-600">
          Assign one or more assets to a customer. They will only see those assets on Map and Inspections.
        </p>
      </div>

      <Notice error={error} success={success} />
      {loading ? <p className="text-sm text-slate-600">Loading customers…</p> : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-lg font-semibold text-slate-900">Customers</h3>
          <p className="mt-1 text-xs text-slate-500">Select a customer, then assign assets on the right.</p>
          <div className="mt-4 space-y-2">
            {customers.length === 0 && !loading ? (
              <p className="text-sm text-slate-500">
                No customer users yet. An admin must create a user with the Customer role first.
              </p>
            ) : null}
            {customers.map((user) => (
              <button
                key={user.id}
                type="button"
                onClick={() => {
                  setSelectedCustomer(user);
                  setSuccess("");
                  loadAssigned(user.id);
                }}
                className={`w-full rounded-lg border px-3 py-3 text-left text-sm ${
                  selectedCustomer?.id === user.id
                    ? "border-teal-300 bg-teal-50"
                    : "border-slate-200 hover:bg-slate-50"
                }`}
              >
                <div className="font-medium text-slate-900">{user.full_name}</div>
                <div className="text-xs text-slate-500">{user.email}</div>
              </button>
            ))}
          </div>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-lg font-semibold text-slate-900">
            {selectedCustomer ? `Assets for ${selectedCustomer.full_name}` : "Assigned assets"}
          </h3>
          {!selectedCustomer ? (
            <p className="mt-3 text-sm text-slate-500">Choose a customer to assign assets.</p>
          ) : (
            <div className="mt-4 space-y-5">
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Add asset</label>
                <select
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  disabled={busy || availableAssets.length === 0}
                  defaultValue=""
                  onChange={(event) => {
                    assignAsset(event.target.value);
                    event.target.value = "";
                  }}
                >
                  <option value="">{availableAssets.length ? "Select an asset to assign…" : "All assets already assigned"}</option>
                  {availableAssets.map((asset) => (
                    <option key={asset.id} value={asset.id}>
                      {asset.vin} — {asset.make_model}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <h4 className="mb-2 text-sm font-medium text-slate-700">Currently assigned</h4>
                {assigned.length === 0 ? (
                  <p className="text-sm text-slate-500">No assets assigned to this customer yet.</p>
                ) : (
                  <div className="space-y-2">
                    {assigned.map((row) => (
                      <div
                        key={row.id}
                        className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
                      >
                        <div>
                          <p className="text-sm font-medium text-slate-900">{row.asset_make_model}</p>
                          <p className="text-xs text-slate-500">VIN {row.asset_vin}</p>
                        </div>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => removeAsset(row.asset_id)}
                          className="text-xs font-semibold text-red-600 hover:text-red-800 disabled:opacity-50"
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
