import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Notice } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import { Badge } from "../lib/format.jsx";

const TONE = {
  critical: "border-red-200 bg-red-50",
  warning: "border-amber-200 bg-amber-50",
  info: "border-sky-200 bg-sky-50",
};

export default function Attention() {
  const [items, setItems] = useState([]);
  const [summary, setSummary] = useState({ critical: 0, warning: 0, info: 0, total: 0 });
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");

  async function load() {
    const [rows, counts] = await Promise.all([api("/attention"), api("/attention/summary")]);
    setItems(rows);
    setSummary(counts);
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, []);

  async function dismiss(id) {
    try {
      await api(`/attention/${id}/dismiss`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  const visible = filter ? items.filter((row) => row.severity === filter) : items;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">Attention Center</h2>
        <p className="mt-1 text-sm text-slate-600">Open conditions only. Duplicates for the same unresolved issue are not created again.</p>
      </div>
      <Notice error={error} />
      <div className="grid gap-4 sm:grid-cols-3">
        <CountCard label="Critical" value={summary.critical} className="border-red-200" />
        <CountCard label="Warning" value={summary.warning} className="border-amber-200" />
        <CountCard label="Info" value={summary.info} className="border-sky-200" />
      </div>
      <div className="flex flex-wrap gap-2">
        {[["", "All"], ["critical", "Critical"], ["warning", "Warning"], ["info", "Info"]].map(([value, label]) => (
          <button
            key={label}
            type="button"
            onClick={() => setFilter(value)}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${filter === value ? "bg-teal-700 text-white" : "border border-slate-300 bg-white"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {visible.length === 0 ? (
        <p className="text-sm text-slate-500">No open attention items.</p>
      ) : (
        <ul className="space-y-3">
          {visible.map((item) => (
            <li key={item.id} className={`rounded-xl border p-4 ${TONE[item.severity] || "border-slate-200 bg-white"}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge value={item.severity} />
                    <h3 className="text-sm font-semibold text-slate-900">{item.title}</h3>
                  </div>
                  <p className="mt-1 text-sm text-slate-700">{item.description}</p>
                </div>
                <div className="flex gap-3">
                  {item.link_path ? (
                    <Link to={item.link_path} className="text-xs font-semibold text-teal-800 hover:underline">
                      Open
                    </Link>
                  ) : null}
                  <button type="button" onClick={() => dismiss(item.id)} className="text-xs font-semibold text-slate-700 hover:underline">
                    Dismiss
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CountCard({ label, value, className }) {
  return (
    <div className={`rounded-xl border bg-white p-4 shadow-sm ${className}`}>
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}
