import { Badge } from "../lib/format.jsx";

export const LEMON_WARNING =
  "⚠️ CRITICAL COST WARNING: Lifetime maintenance costs have exceeded replacement value. Flagged for immediate evaluation/retirement.";

export function LemonBanner({ show }) {
  if (!show) return null;
  return (
    <div className="rounded-xl border-2 border-red-600 bg-red-600 px-4 py-3 text-sm font-bold text-white shadow-md">
      {LEMON_WARNING}
    </div>
  );
}

export function CustodySummary({ asset }) {
  if (!asset) return null;
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Current custody</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {asset.current_custody_type ? <Badge value={asset.current_custody_type} /> : <Badge value="unassigned" />}
        <p className="text-sm font-medium text-slate-900">{asset.current_location}</p>
      </div>
      {asset.current_custody_type === "In Transit" ? (
        <p className="mt-2 font-mono text-xs text-slate-600">
          {asset.carrier_name || "3PL"} · {asset.tracking_code || "NO-TRACKING"}
        </p>
      ) : null}
    </div>
  );
}
