import { useEffect, useState } from "react";
import { DataTable } from "../components/DataTable";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api, displayApiError } from "../lib/api.js";
import { Badge, money } from "../lib/format.jsx";
import { MANAGERS, hasRole } from "../lib/roles.js";

const emptyForm = {
  sku: "",
  name: "",
  description: "",
  manufacturer: "",
  vendor: "",
  warehouse_location: "",
  bin_location: "",
  quantity_on_hand: "0",
  reorder_point: "0",
  reorder_quantity: "0",
  unit_cost: "0",
  compatible_models: "",
};

export default function Parts() {
  const [parts, setParts] = useState([]);
  const [summary, setSummary] = useState(null);
  const [history, setHistory] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [txn, setTxn] = useState({ txn_type: "receipt", quantity: "1", notes: "", allow_negative: false, transfer_location: "" });
  const [install, setInstall] = useState({ asset_id: "", quantity: "1", notes: "", allow_negative: false });
  const [assets, setAssets] = useState([]);
  const [stock, setStock] = useState("");
  const [busy, setBusy] = useState(false);
  const [me, setMe] = useState(null);
  const canManageInventory = hasRole(me, MANAGERS);

  async function load() {
    const query = stock ? `?stock=${stock}` : "";
    const [rows, rollup, fleet, user] = await Promise.all([
      api(`/parts${query}`),
      api("/parts/summary"),
      api("/assets"),
      api("/auth/me"),
    ]);
    setParts(rows);
    setSummary(rollup);
    setAssets(fleet);
    setMe(user);
  }

  useEffect(() => {
    load().catch((err) => setError(displayApiError(err, me)));
  }, [stock]);

  async function openHistory(part) {
    setSelected(part);
    setHistory(await api(`/parts/${part.id}/transactions`));
  }

  async function createPart(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/parts", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          quantity_on_hand: Number(form.quantity_on_hand || 0),
          reorder_point: Number(form.reorder_point || 0),
          reorder_quantity: Number(form.reorder_quantity || 0),
          unit_cost: Number(form.unit_cost || 0),
        }),
      });
      setSuccess("Part created.");
      setModal(null);
      setForm(emptyForm);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitInstall(event) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await api(`/parts/${selected.id}/install`, {
        method: "POST",
        body: JSON.stringify({
          asset_id: install.asset_id,
          quantity: Number(install.quantity),
          notes: install.notes || null,
          allow_negative: install.allow_negative,
        }),
      });
      setSuccess("Part marked as installed / used on the asset.");
      setModal(null);
      await load();
      await openHistory({ ...selected });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function removePart(part) {
    const confirmed = window.confirm(
      `Remove ${part.sku}?\n\nIf the part has stock or repair history it is retired instead of deleted, so that history stays intact.`,
    );
    if (!confirmed) return;
    setError("");
    try {
      const result = await api(`/parts/${part.id}`, { method: "DELETE" });
      setSuccess(result.detail || `${part.sku} deleted.`);
      if (selected?.id === part.id) {
        setSelected(null);
        setHistory([]);
      }
      await load();
    } catch (err) {
      setError(displayApiError(err, me));
    }
  }

  async function submitTxn(event) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await api(`/parts/${selected.id}/transactions`, {
        method: "POST",
        body: JSON.stringify({
          txn_type: txn.txn_type,
          quantity: Number(txn.quantity),
          notes: txn.notes,
          allow_negative: txn.allow_negative,
          transfer_location: txn.transfer_location || null,
        }),
      });
      setSuccess("Inventory transaction recorded.");
      setModal(null);
      await load();
      await openHistory({ ...selected });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Parts Inventory</h2>
          <p className="mt-1 text-sm text-slate-600">
            {canManageInventory
              ? "Stock, reorder points, and usage history. Quantities only change through transactions."
              : "Record a part you took from inventory and installed on an asset. Adding new catalog parts is limited to managers."}
          </p>
        </div>
        {canManageInventory ? (
          <button type="button" onClick={() => { setForm(emptyForm); setModal("create"); }} className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white">
            Add part
          </button>
        ) : null}
      </div>
      <Notice error={error} success={success} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryCard label="Active parts" value={summary?.part_count ?? "—"} />
        <SummaryCard label="Inventory value" value={summary ? money(summary.inventory_value) : "—"} />
        <SummaryCard label="Low stock" value={summary?.low_stock_count ?? "—"} />
        <SummaryCard label="Out of stock" value={summary?.out_of_stock_count ?? "—"} />
      </div>
      <div className="flex flex-wrap gap-2">
        {[
          ["", "All"],
          ["reorder", "Reorder needed"],
          ["low_stock", "Low stock"],
          ["out_of_stock", "Out of stock"],
        ].map(([value, label]) => (
          <button
            key={label}
            type="button"
            onClick={() => setStock(value)}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${stock === value ? "bg-teal-700 text-white" : "border border-slate-300 bg-white"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <DataTable
        columns={[
          { key: "sku", header: "SKU" },
          { key: "name", header: "Name" },
          { key: "quantity_on_hand", header: "On hand" },
          { key: "reorder_point", header: "Reorder" },
          { key: "unit_cost", header: "Unit cost", render: (r) => money(r.unit_cost) },
          { key: "inventory_value", header: "Value", render: (r) => money(r.inventory_value) },
          { key: "stock_state", header: "Status", render: (r) => <Badge value={r.stock_state} /> },
          { key: "warehouse_location", header: "Location", render: (r) => [r.warehouse_location, r.bin_location].filter(Boolean).join(" / ") || "—" },
          {
            key: "id",
            header: "",
            render: (r) => (
              <div className="flex gap-2">
                <button type="button" className="text-xs font-semibold text-teal-800 hover:underline" onClick={() => openHistory(r)}>
                  History
                </button>
                <button
                  type="button"
                  className="text-xs font-semibold text-teal-800 hover:underline"
                  onClick={() => {
                    setSelected(r);
                    setInstall({ asset_id: assets[0]?.id || "", quantity: "1", notes: "", allow_negative: false });
                    setModal("install");
                  }}
                >
                  Install / Use
                </button>
                {canManageInventory ? (
                  <button
                    type="button"
                    className="text-xs font-semibold text-slate-700 hover:underline"
                    onClick={() => {
                      setSelected(r);
                      setModal("txn");
                    }}
                  >
                    Transact
                  </button>
                ) : null}
                {canManageInventory ? (
                  <button
                    type="button"
                    className="text-xs font-semibold text-red-700 hover:underline"
                    onClick={() => removePart(r)}
                  >
                    Delete
                  </button>
                ) : null}
              </div>
            ),
          },
        ]}
        rows={parts}
        empty="No parts yet."
      />
      {selected ? (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-900">Usage history · {selected.sku}</h3>
          {history.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">No transactions yet.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {history.map((row) => {
                const alreadyReversed = history.some((item) => item.reverses_transaction_id === row.id);
                return (
                <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 p-2 text-sm">
                  <span>
                    <span className="font-medium">{row.txn_type.replaceAll("_", " ")}</span> · {row.quantity_delta > 0 ? "+" : ""}
                    {row.quantity_delta} → {row.quantity_after}
                    {row.asset_vin ? ` · ${row.asset_name || "Asset"} ${row.asset_vin}` : ""}
                    {row.work_order_id ? ` · WO ${row.work_order_id}` : ""}
                    {row.notes ? ` · ${row.notes}` : ""}
                    {row.reversal_reason ? ` · reversed: ${row.reversal_reason}` : ""}
                  </span>
                  {canManageInventory && row.txn_type !== "reversal" && !alreadyReversed ? (
                    <button
                      type="button"
                      className="text-xs font-semibold text-slate-700 hover:underline"
                      onClick={async () => {
                        const reason = window.prompt("Why are you reversing this transaction?");
                        if (!reason) return;
                        try {
                          await api(`/parts/transactions/${row.id}/reverse`, {
                            method: "POST",
                            body: JSON.stringify({ reason }),
                          });
                          setSuccess("Transaction reversed. History was kept.");
                          await load();
                          await openHistory(selected);
                        } catch (err) {
                          setError(displayApiError(err, me));
                        }
                      }}
                    >
                      Reverse
                    </button>
                  ) : null}
                </li>
              );
              })}
            </ul>
          )}
        </div>
      ) : null}

      {modal === "create" ? (
        <Modal title="Add part" onClose={() => setModal(null)}>
          <form onSubmit={createPart} className="grid gap-3 sm:grid-cols-2">
            <Field label="SKU"><input required className={inputClass} value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} /></Field>
            <Field label="Name"><input required className={inputClass} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Manufacturer"><input className={inputClass} value={form.manufacturer} onChange={(e) => setForm({ ...form, manufacturer: e.target.value })} /></Field>
            <Field label="Vendor"><input className={inputClass} value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })} /></Field>
            <Field label="Warehouse / location"><input className={inputClass} value={form.warehouse_location} onChange={(e) => setForm({ ...form, warehouse_location: e.target.value })} /></Field>
            <Field label="Bin"><input className={inputClass} value={form.bin_location} onChange={(e) => setForm({ ...form, bin_location: e.target.value })} /></Field>
            <Field label="Initial qty"><input type="number" min="0" className={inputClass} value={form.quantity_on_hand} onChange={(e) => setForm({ ...form, quantity_on_hand: e.target.value })} /></Field>
            <Field label="Reorder point"><input type="number" min="0" className={inputClass} value={form.reorder_point} onChange={(e) => setForm({ ...form, reorder_point: e.target.value })} /></Field>
            <Field label="Reorder qty"><input type="number" min="0" className={inputClass} value={form.reorder_quantity} onChange={(e) => setForm({ ...form, reorder_quantity: e.target.value })} /></Field>
            <Field label="Unit cost"><input type="number" min="0" step="0.01" className={inputClass} value={form.unit_cost} onChange={(e) => setForm({ ...form, unit_cost: e.target.value })} /></Field>
            <div className="sm:col-span-2">
              <Field label="Compatible models"><input className={inputClass} value={form.compatible_models} onChange={(e) => setForm({ ...form, compatible_models: e.target.value })} /></Field>
            </div>
            <div className="sm:col-span-2">
              <button disabled={busy} type="submit" className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white">Save</button>
            </div>
          </form>
        </Modal>
      ) : null}

      {modal === "install" && selected ? (
        <Modal title={`Install / Use · ${selected.sku}`} onClose={() => setModal(null)}>
          <form onSubmit={submitInstall} className="space-y-3">
            <p className="text-sm text-slate-600">Record that this part was installed or used to repair an asset. Stock on hand decreases.</p>
            <Field label="Asset">
              <select required className={inputClass} value={install.asset_id} onChange={(e) => setInstall({ ...install, asset_id: e.target.value })}>
                <option value="">Select asset</option>
                {assets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.vin} · {asset.make_model}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Quantity used">
              <input type="number" min="1" required className={inputClass} value={install.quantity} onChange={(e) => setInstall({ ...install, quantity: e.target.value })} />
            </Field>
            <Field label="Notes">
              <input className={inputClass} placeholder="Installed to replace failed camera" value={install.notes} onChange={(e) => setInstall({ ...install, notes: e.target.value })} />
            </Field>
            {canManageInventory ? (
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={install.allow_negative} onChange={(e) => setInstall({ ...install, allow_negative: e.target.checked })} />
                Manager override: allow negative
              </label>
            ) : null}
            <button disabled={busy} type="submit" className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white">
              Mark installed
            </button>
          </form>
        </Modal>
      ) : null}

      {modal === "txn" && selected ? (
        <Modal title={`Transaction · ${selected.sku}`} onClose={() => setModal(null)}>
          <form onSubmit={submitTxn} className="space-y-3">
            <Field label="Type">
              <select className={inputClass} value={txn.txn_type} onChange={(e) => setTxn({ ...txn, txn_type: e.target.value })}>
                <option value="receipt">Receipt</option>
                <option value="adjustment">Adjustment (set on-hand)</option>
                <option value="return">Return</option>
              </select>
            </Field>
            <p className="text-xs text-slate-500">
              Multi-location transfers are disabled until stock is tracked per warehouse. Do not rename this SKU&apos;s location to move part of the quantity.
            </p>
            <Field label={txn.txn_type === "adjustment" ? "New quantity on hand" : "Quantity"}>
              <input type="number" min="1" className={inputClass} value={txn.quantity} onChange={(e) => setTxn({ ...txn, quantity: e.target.value })} />
            </Field>
            <Field label="Notes">
              <input className={inputClass} value={txn.notes} onChange={(e) => setTxn({ ...txn, notes: e.target.value })} />
            </Field>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" checked={txn.allow_negative} onChange={(e) => setTxn({ ...txn, allow_negative: e.target.checked })} />
              Manager override: allow negative
            </label>
            <button disabled={busy} type="submit" className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white">Record</button>
          </form>
        </Modal>
      ) : null}
    </div>
  );
}

function SummaryCard({ label, value }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}
