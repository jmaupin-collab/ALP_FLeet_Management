import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DataTable } from "../components/DataTable";
import { Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import {
  OPEN_STATUSES,
  REQUEST_STATUSES,
  RequestStatusBadge,
  formatDate,
  requestTypeLabel,
} from "../lib/requests.jsx";

// Triage queue for requests that arrived through the public form. Nothing here
// moves fleet state; that happens on the detail page, through the lifecycle
// workflow.

const OPEN_TAB = "open";

export default function Requests() {
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [tab, setTab] = useState(OPEN_TAB);
  const [type, setType] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([api("/requests"), api("/requests/summary")])
      .then(([list, counts]) => {
        if (cancelled) return;
        setRows(list);
        setSummary(counts);
        setError("");
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (tab === OPEN_TAB ? !OPEN_STATUSES.includes(row.status) : row.status !== tab) return false;
      if (type && row.request_type !== type) return false;
      if (!term) return true;
      return [row.reference, row.agency_name, row.requester_name, row.requester_email, row.address]
        .filter(Boolean)
        .some((value) => value.toLowerCase().includes(term));
    });
  }, [rows, tab, type, search]);

  const counts = summary?.counts || {};
  const openCount = OPEN_STATUSES.reduce((total, status) => total + (counts[status] || 0), 0);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">Request Center</h2>
        <p className="mt-1 text-sm text-slate-600">
          Deployment and pickup requests submitted from the public form. Reviewing a request records a
          decision — no trailer moves until you fulfil it.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Tab label="Open" count={openCount} active={tab === OPEN_TAB} onClick={() => setTab(OPEN_TAB)} />
        {REQUEST_STATUSES.map((status) => (
          <Tab
            key={status}
            label={status}
            count={counts[status] || 0}
            active={tab === status}
            onClick={() => setTab(status)}
          />
        ))}
      </div>

      <div className="flex flex-wrap gap-3">
        <input
          className={`${inputClass} max-w-xs`}
          placeholder="Search reference, agency, requester, address"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={`${inputClass} max-w-[12rem]`} value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">Deploy and pickup</option>
          <option value="deploy">Deploy only</option>
          <option value="pickup">Pickup only</option>
        </select>
      </div>

      <Notice error={error} />
      {loading ? <p className="text-sm text-slate-600">Loading requests…</p> : null}

      <DataTable
        empty="No requests match this view."
        columns={[
          {
            key: "reference",
            header: "Reference",
            render: (row) => (
              <Link to={`/requests/${row.id}`} className="font-mono text-xs font-semibold text-teal-800 hover:underline">
                {row.reference}
              </Link>
            ),
          },
          { key: "request_type", header: "Type", render: (row) => requestTypeLabel(row.request_type) },
          { key: "status", header: "Status", render: (row) => <RequestStatusBadge value={row.status} /> },
          { key: "agency_name", header: "Agency / customer" },
          {
            key: "requester_name",
            header: "Requester",
            render: (row) => (
              <div>
                <p>{row.requester_name}</p>
                <p className="text-xs text-slate-500">{row.requester_email}</p>
              </div>
            ),
          },
          { key: "quantity", header: "Trailers", render: (row) => row.quantity },
          {
            key: "assets",
            header: "Assigned",
            render: (row) =>
              row.assets.length ? `${row.assets.filter((a) => a.fulfilled_at).length}/${row.assets.length} out` : "—",
          },
          { key: "requested_date", header: "Wanted", render: (row) => formatDate(row.requested_date) },
          { key: "created_at", header: "Submitted", render: (row) => formatDate(row.created_at) },
        ]}
        rows={visible}
      />
    </div>
  );
}

function Tab({ label, count, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-1.5 text-sm font-medium ring-1 ring-inset ${
        active
          ? "bg-teal-700 text-white ring-teal-700"
          : "bg-white text-slate-700 ring-slate-200 hover:bg-slate-50"
      }`}
    >
      {label}
      <span className={`ml-2 text-xs ${active ? "text-teal-100" : "text-slate-500"}`}>{count}</span>
    </button>
  );
}
