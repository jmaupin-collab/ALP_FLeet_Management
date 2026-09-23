import { MANAGERS, hasRole } from "./roles.js";

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");

const TOKEN_KEY = "access_token";

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join(" ");
  }
  return JSON.stringify(detail);
}

export function displayApiError(err, me) {
  const clean = err?.message || "Request failed.";
  // Only staff see status codes and paths; customers get the plain message.
  const privileged = Boolean(import.meta.env.DEV) || hasRole(me, MANAGERS);
  if (privileged && (err?.status || err?.path)) {
    return `${clean} (${err.status || "error"} ${err.path || ""})`.trim();
  }
  return clean;
}

export async function api(path, options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401) {
    clearToken();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.assign("/login");
    }
    const err = new Error("Not authenticated");
    err.status = 401;
    err.path = path;
    throw err;
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = formatDetail(body.detail) || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    console.error(`API ${response.status} ${path}:`, detail);
    const message = import.meta.env.DEV ? `${detail} (${response.status} ${path})` : detail;
    const err = new Error(message);
    err.status = response.status;
    err.path = path;
    throw err;
  }
  if (response.status === 204) return null;
  return response.json();
}

export async function downloadFile(path, filename, forbiddenMessage) {
  const token = getToken();
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API_BASE}${path}`, { headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = formatDetail(body.detail) || detail;
    } catch {
      /* response was not JSON; keep the status text */
    }
    if (response.status === 401) {
      clearToken();
      detail = "Your session expired. Sign in again.";
    } else if (response.status === 403 && forbiddenMessage) {
      detail = forbiddenMessage;
    }
    const err = new Error(detail);
    err.status = response.status;
    err.path = path;
    throw err;
  }
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

export function downloadCsv(path, filename) {
  return downloadFile(path, filename, "CSV export requires a fleet manager or admin role.");
}

export function uploadFile(path, file, field = "file") {
  const body = new FormData();
  body.append(field, file);
  return api(path, { method: "POST", body });
}
