import { useEffect, useState } from "react";
import { API_BASE, getToken } from "../lib/api.js";

export default function InspectionPhotos({ inspection }) {
  const [urls, setUrls] = useState({});

  useEffect(() => {
    let cancelled = false;
    const created = [];

    async function load() {
      const next = {};
      for (const photo of inspection?.photos || []) {
        try {
          const response = await fetch(`${API_BASE}/inspections/${inspection.id}/photos/${photo.id}`, {
            headers: { Authorization: `Bearer ${getToken()}` },
          });
          if (!response.ok) continue;
          const url = URL.createObjectURL(await response.blob());
          created.push(url);
          next[photo.id] = url;
        } catch {
          /* ignore a single failed photo */
        }
      }
      if (!cancelled) setUrls(next);
    }

    load();
    return () => {
      cancelled = true;
      created.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [inspection]);

  if (!inspection?.photos?.length) return null;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">Photos ({inspection.photos.length} of 4)</h3>
      {inspection.notes ? <p className="mt-2 text-sm text-slate-600">{inspection.notes}</p> : null}
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        {inspection.photos.map((photo) => (
          <a
            key={photo.id}
            href={urls[photo.id]}
            target="_blank"
            rel="noreferrer"
            className="block overflow-hidden rounded-lg border border-slate-200 bg-slate-50"
          >
            {urls[photo.id] ? (
              <img src={urls[photo.id]} alt={photo.original_filename} className="h-36 w-full object-cover" />
            ) : (
              <div className="flex h-36 items-center justify-center text-xs text-slate-500">Loading…</div>
            )}
            <p className="truncate px-2 py-1 text-xs text-slate-500">{photo.original_filename}</p>
          </a>
        ))}
      </div>
    </div>
  );
}
