import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { Badge } from "../lib/format.jsx";

export default function AssetUtilization({ assetId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api(`/assets/${assetId}/utilization`)
      .then(setData)
      .catch((err) => setError(err.message));
  }, [assetId]);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (!data) return <p className="text-sm text-slate-500">Loading utilization…</p>;

  const windows = [
    ["30", "30-day"],
    ["90", "90-day"],
    ["365", "365-day"],
  ];

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Utilization</h3>
          <p className="mt-1 text-xs text-slate-500">
            Lifecycle segments (not work-order calendar). High Downtime needs 90-day eligible ≥ 7 days, maintenance ≥ 20%, and ≥ 3 days. · {data.source.replaceAll("_", " ")}
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {data.flags.map((flag) => (
            <Badge key={flag} value={flag} />
          ))}
        </div>
      </div>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {windows.map(([key, label]) => {
          const row = data.windows[key];
          return (
            <div key={key} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
              <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
              <dd className="mt-1 text-xl font-semibold text-slate-900">{Number(row.utilization_pct).toFixed(1)}%</dd>
              <p className="mt-1 text-xs text-slate-600">
                Deployed {row.deployed_days}d · Available {row.available_days}d · Maintenance {row.maintenance_days}d · Transit {row.in_transit_days}d
              </p>
            </div>
          );
        })}
        <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
          <dt className="text-xs uppercase tracking-wide text-slate-500">Currently unused</dt>
          <dd className="mt-1 text-xl font-semibold text-slate-900">{data.consecutive_unused_days} days</dd>
          <p className="mt-1 text-xs text-slate-600">State: {data.current_state.replaceAll("_", " ")}</p>
        </div>
      </dl>
    </div>
  );
}
