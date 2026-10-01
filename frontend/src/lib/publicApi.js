// Fetch helper for the unauthenticated request form.
//
// Deliberately separate from lib/api.js: that one attaches the session token and
// bounces to /login on a 401, both of which are wrong here. A visitor with no
// account must never be redirected away from the form they are filling in, and a
// staff member who happens to be signed in should not have their token sent to a
// public endpoint.

import { API_BASE } from "./api.js";

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join(" ");
  return JSON.stringify(detail);
}

export async function publicApi(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API_BASE}/public${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = formatDetail(body.detail) || detail;
    } catch {
      /* not JSON; the status text will have to do */
    }
    const err = new Error(detail);
    err.status = response.status;
    throw err;
  }
  return response.status === 204 ? null : response.json();
}
