import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { MANAGERS, hasRole } from "../lib/roles.js";
import { useMe } from "../lib/useMe.js";
import { inputClass } from "./Modal.jsx";

/**
 * Whether this unit's position is pulled from the telematics provider.
 *
 * Off by default and per asset: a Geotab or Cube account covers far more
 * vehicles than this fleet, and only the ones named here are asked about or
 * stored.
 */
export default function AssetTracking({ assetId, onChange }) {
  const { me } = useMe();
  const [state, setState] = useState(null);
  const [deviceId, setDeviceId] = useState("");
  const [provider, setProvider] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const canEdit = hasRole(me, MANAGERS);

  useEffect(() => {
    let active = true;
    api(`/assets/${assetId}/tracking`)
      .then((row) => {
        if (!active) return;
        setState(row);
        setDeviceId(row.device_id || "");
        setProvider(row.provider || "");
      })
      .catch(() => setState(null));
    return () => {
      active = false;
    };
  }, [assetId]);

  async function save(enabled) {
    setBusy(true);
    setError("");
    try {
      const row = await api(`/assets/${assetId}/tracking`, {
        method: "PUT",
        body: JSON.stringify({ enabled, device_id: deviceId.trim() || null, provider: provider.trim() || null }),
      });
      setState(row);
      onChange?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!state) return null;

  const enabled = state.tracking_enabled;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-800">Location tracking</h3>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${
            enabled
              ? "bg-teal-50 text-teal-800 ring-teal-200"
              : "bg-slate-100 text-slate-600 ring-slate-200"
          }`}
        >
          {enabled ? "Tracked" : "Not tracked"}
        </span>
      </div>

      <p className="mb-4 text-sm text-slate-600">
        {enabled
          ? `Positions for device ${state.device_id} are pulled from ${state.provider || "the provider"} and drive the geofence alerts.`
          : "No positions are pulled for this unit. Add its device id to start tracking it."}
      </p>

      {canEdit ? (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm text-slate-700">
              Device id
              <input
                className={`mt-1 ${inputClass}`}
                value={deviceId}
                onChange={(event) => setDeviceId(event.target.value)}
                placeholder="Serial from Geotab or Cube"
              />
            </label>
            <label className="block text-sm text-slate-700">
              Provider
              <input
                className={`mt-1 ${inputClass}`}
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
                placeholder="geotab, cube…"
              />
            </label>
          </div>
          {error ? <p className="text-sm text-rose-700">{error}</p> : null}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy || (!enabled && !deviceId.trim())}
              onClick={() => save(true)}
              className="rounded-lg bg-teal-800 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
            >
              {enabled ? "Save" : "Start tracking"}
            </button>
            {enabled ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => save(false)}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700 disabled:opacity-50"
              >
                Stop tracking
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
