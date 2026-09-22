import { useState } from "react";
import { Badge } from "../lib/format.jsx";

const RESULTS = [
  { value: "pass", label: "Pass" },
  { value: "fail", label: "Fail" },
  { value: "n/a", label: "N/A" },
];

export default function InspectionChecklist({ inspection, onMark, busyId }) {
  const [notes, setNotes] = useState({});
  if (!inspection) return null;

  return (
    <div className="space-y-3">
      {inspection.items.map((item) => (
        <div key={item.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-medium text-slate-900">{item.component}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <Badge value={item.result} />
                {item.generated_work_order_id ? (
                  <span className="text-xs font-medium text-rose-700">Open WO created instantly</span>
                ) : null}
              </div>
            </div>
            <div className="flex gap-2">
              {RESULTS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  disabled={busyId === item.id || inspection.status !== "in_progress"}
                  onClick={() => onMark(item, option.value, notes[item.id] || "")}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ring-1 ring-inset disabled:opacity-50 ${
                    item.result === option.value
                      ? option.value === "fail"
                        ? "bg-red-600 text-white ring-red-700"
                        : option.value === "pass"
                          ? "bg-emerald-600 text-white ring-emerald-700"
                          : "bg-slate-700 text-white ring-slate-800"
                      : "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <input
            value={notes[item.id] ?? item.notes ?? ""}
            disabled={inspection.status === "submitted"}
            onChange={(e) => setNotes((current) => ({ ...current, [item.id]: e.target.value }))}
            placeholder="Technician notes"
            className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
          />
        </div>
      ))}
    </div>
  );
}
