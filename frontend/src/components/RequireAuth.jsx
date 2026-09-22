import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { api, getToken } from "../lib/api.js";

export default function RequireAuth() {
  const [state, setState] = useState(getToken() ? "loading" : "anon");

  useEffect(() => {
    if (!getToken()) {
      setState("anon");
      return;
    }
    api("/auth/me")
      .then(() => setState("ok"))
      .catch(() => setState("anon"));
  }, []);

  if (state === "loading") {
    return <p className="p-8 text-sm text-slate-600">Checking session…</p>;
  }
  if (state !== "ok") {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
