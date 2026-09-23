export function Modal({ title, children, onClose, wide = false }) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/50 p-4">
      <div className={`max-h-[90vh] w-full ${wide ? "max-w-3xl" : "max-w-lg"} overflow-y-auto rounded-2xl bg-white p-6 shadow-xl`}>
        <div className="mb-4 flex items-start justify-between gap-3">
          <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
          <button type="button" onClick={onClose} className="text-sm text-slate-500 hover:text-slate-800">
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Notice({ error, success }) {
  if (!error && !success) return null;
  return (
    <p className={`text-sm font-medium ${error ? "text-red-700" : "text-teal-800"}`}>{error || success}</p>
  );
}

export function Field({ label, children }) {
  return (
    <label className="block text-sm text-slate-700">
      {label}
      <div className="mt-1">{children}</div>
    </label>
  );
}

export const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2 disabled:bg-slate-100";
