export function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
}

export function hours(value) {
  return `${Number(value).toFixed(1)} h`;
}

export function statusLabel(value) {
  const labels = {
    waiting_parts: "waiting on parts",
    waiting_vendor: "waiting on vendor",
    completed: "complete",
    in_progress: "in progress",
    ok: "OK",
    due_soon: "Due Soon",
    due: "Due",
    overdue: "Overdue",
    out_of_service: "retired",
  };
  if (labels[value]) return labels[value];
  return String(value).replaceAll("_", " ");
}

export function statusClass(value) {
  // Normalize to lowercase for case-insensitive matching
  const key = String(value).toLowerCase();
  
  const map = {
    // Operational statuses
    available: "bg-blue-50 text-blue-800 ring-blue-200",
    deployed: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    in_transit: "bg-sky-50 text-sky-800 ring-sky-200",
    maintenance: "bg-orange-50 text-orange-800 ring-orange-200",
    out_of_service: "bg-slate-400 text-slate-100 ring-slate-500",
    retired: "bg-slate-500 text-slate-100 ring-slate-600",
    // Deployment statuses
    staged: "bg-indigo-50 text-indigo-800 ring-indigo-200",
    idle: "bg-amber-50 text-amber-800 ring-amber-200",
    stored: "bg-slate-100 text-slate-700 ring-slate-200",
    returned: "bg-slate-100 text-slate-600 ring-slate-200",
    // Inspection results
    pass: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    fail: "bg-red-50 text-red-800 ring-red-200",
    pending: "bg-slate-100 text-slate-600 ring-slate-200",
    "n/a": "bg-slate-100 text-slate-600 ring-slate-200",
    // Custody types
    "warehouse depot": "bg-indigo-50 text-indigo-800 ring-indigo-200",
    "in transit": "bg-sky-50 text-sky-800 ring-sky-200",
    "customer / le agency": "bg-amber-50 text-amber-900 ring-amber-200",
    // Work order statuses
    inspection: "bg-violet-50 text-violet-800 ring-violet-200",
    open: "bg-rose-50 text-rose-800 ring-rose-200",
    investigating: "bg-violet-50 text-violet-800 ring-violet-200",
    in_progress: "bg-orange-50 text-orange-800 ring-orange-200",
    waiting_parts: "bg-amber-50 text-amber-900 ring-amber-200",
    waiting_vendor: "bg-sky-50 text-sky-800 ring-sky-200",
    completed: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    cancelled: "bg-slate-100 text-slate-600 ring-slate-200",
    // Priorities
    low: "bg-slate-100 text-slate-700 ring-slate-200",
    medium: "bg-indigo-50 text-indigo-800 ring-indigo-200",
    high: "bg-orange-50 text-orange-800 ring-orange-200",
    critical: "bg-red-50 text-red-800 ring-red-200",
    // PM statuses
    deployment: "bg-sky-50 text-sky-800 ring-sky-200",
    ok: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    due_soon: "bg-amber-50 text-amber-800 ring-amber-200",
    due: "bg-orange-50 text-orange-800 ring-orange-200",
    overdue: "bg-red-50 text-red-800 ring-red-200",
  };
  return map[key] ?? "bg-slate-100 text-slate-700 ring-slate-200";
}

export function typeClass(value) {
  if (value === "ALPR Trailer") return "bg-teal-50 text-teal-800 ring-teal-200";
  if (value === "Semi Truck") return "bg-blue-50 text-blue-800 ring-blue-200";
  return "bg-violet-50 text-violet-800 ring-violet-200";
}

export function Badge({ value, tone }) {
  const cls = tone === "type" ? typeClass(value) : statusClass(value);
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${cls}`}>
      {statusLabel(value)}
    </span>
  );
}
