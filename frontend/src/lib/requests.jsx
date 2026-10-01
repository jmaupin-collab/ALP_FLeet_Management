// Shared vocabulary for the Request Center, so the list, the detail page and the
// notification links all describe a request the same way.

// Fired after any action that may have changed the notification feed, so the
// bell in the layout header can refresh without the request pages having to
// reach into it. A window event keeps the two sides unaware of each other.
export const NOTIFICATIONS_CHANGED = "fleet:notifications-changed";

export const REQUEST_STATUSES = [
  "New",
  "Under Review",
  "Approved",
  "Scheduled",
  "In Progress",
  "Completed",
  "Rejected",
  "Cancelled",
];

// Mirrors ALLOWED_TRANSITIONS in backend/app/requests_api.py. The server is
// still the authority; this only keeps the UI from offering a move it will
// refuse.
export const NEXT_STATUSES = {
  New: ["Under Review", "Approved", "Rejected", "Cancelled"],
  "Under Review": ["Approved", "Rejected", "Cancelled"],
  Approved: ["Scheduled", "Under Review", "Cancelled"],
  Scheduled: ["In Progress", "Approved", "Cancelled"],
  "In Progress": ["Completed", "Scheduled", "Cancelled"],
  Completed: [],
  Rejected: [],
  Cancelled: [],
};

// A request can only take trailers or be fulfilled once it has been approved.
export const FULFILLABLE = ["Approved", "Scheduled", "In Progress"];

export const OPEN_STATUSES = ["New", "Under Review", "Approved", "Scheduled", "In Progress"];

const TONE = {
  New: "bg-rose-50 text-rose-800 ring-rose-200",
  "Under Review": "bg-violet-50 text-violet-800 ring-violet-200",
  Approved: "bg-blue-50 text-blue-800 ring-blue-200",
  Scheduled: "bg-indigo-50 text-indigo-800 ring-indigo-200",
  "In Progress": "bg-orange-50 text-orange-800 ring-orange-200",
  Completed: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  Rejected: "bg-slate-200 text-slate-700 ring-slate-300",
  Cancelled: "bg-slate-100 text-slate-600 ring-slate-200",
};

export function RequestStatusBadge({ value }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
        TONE[value] || TONE.Cancelled
      }`}
    >
      {value}
    </span>
  );
}

export function requestTypeLabel(value) {
  return value === "pickup" ? "Pickup" : "Deploy";
}

export function formatDate(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "—" : parsed.toLocaleDateString();
}

export function formatDateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "—" : parsed.toLocaleString();
}
