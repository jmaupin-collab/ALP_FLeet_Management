import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { api, clearToken } from "../lib/api.js";
import { useMe, clearMeCache } from "../lib/useMe.js";
import { ADMINS, ALL_INTERNAL, EVERYONE, MANAGERS, OPERATORS, VIEWERS, hasRole, homePathFor } from "../lib/roles.js";

// Role groups come from lib/roles.js so nav visibility and the route guards in
// App.jsx cannot drift apart.
const tabs = [
  { to: "/", label: "Dashboard", end: true, roles: VIEWERS },
  { to: "/attention", label: "Attention", roles: OPERATORS },
  { to: "/assets", label: "Assets", roles: VIEWERS },
  { to: "/deployments", label: "Deployments", roles: VIEWERS },
  { to: "/map", label: "Map", roles: EVERYONE },
  { to: "/maintenance", label: "Maintenance", roles: ALL_INTERNAL },
  { to: "/parts", label: "Parts", roles: OPERATORS },
  { to: "/preventive-maintenance", label: "Preventive Maintenance", roles: ALL_INTERNAL },
  { to: "/inspections", label: "Inspections", roles: EVERYONE },
  { to: "/analytics", label: "Analytics", roles: VIEWERS },
  { to: "/warehouses", label: "Warehouses", roles: VIEWERS },
  { to: "/agencies", label: "Agencies", roles: VIEWERS },
  { to: "/vendors", label: "Vendors", roles: VIEWERS },
  { to: "/customer-access", label: "Customer Access", roles: MANAGERS },
  { to: "/admin", label: "Admin", roles: ADMINS },
];

export default function Layout() {
  const navigate = useNavigate();
  const { me } = useMe();
  const [showPasswordModal, setShowPasswordModal] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current_password: "", new_password: "" });
  const [passwordError, setPasswordError] = useState("");
  const [passwordSuccess, setPasswordSuccess] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Roles without a dashboard land on their own first page instead.
    const home = homePathFor(me);
    if (me && home !== "/" && window.location.pathname === "/") {
      navigate(home, { replace: true });
    }
  }, [me, navigate]);

  function logout() {
    clearToken();
    clearMeCache();
    window.location.replace("/login");
  }

  async function changePassword(e) {
    e.preventDefault();
    if (!passwordForm.current_password || !passwordForm.new_password) {
      setPasswordError("Both fields are required.");
      return;
    }
    if (passwordForm.new_password.length < 8) {
      setPasswordError("New password must be at least 8 characters.");
      return;
    }

    setBusy(true);
    setPasswordError("");
    setPasswordSuccess("");
    try {
      await api("/auth/change-password", {
        method: "POST",
        body: JSON.stringify(passwordForm),
      });
      // Changing the password revokes every existing token, including this one,
      // so send the user back through sign-in rather than leaving a dead session.
      setPasswordSuccess("Password changed. Signing you back in…");
      setPasswordForm({ current_password: "", new_password: "" });
      setTimeout(logout, 1500);
    } catch (err) {
      setPasswordError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen bg-slate-100">
      <aside className="sticky top-0 flex h-screen w-64 shrink-0 flex-col border-r border-slate-800 bg-ink-950 text-slate-200">
        <div className="border-b border-slate-800 px-5 py-5">
          <h1 className="text-lg font-semibold text-white">Fleet Command Services</h1>
          <p className="mt-1 text-xs text-slate-400">ALPR · Semis · Vehicles</p>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          {tabs
            .filter((tab) => !tab.roles || hasRole(me, tab.roles))
            .map((tab) => (
              <NavLink
                key={tab.to}
                to={tab.to}
                end={tab.end}
                className={({ isActive }) =>
                  `block rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                    isActive ? "bg-teal-500/15 text-teal-200" : "text-slate-300 hover:bg-white/5 hover:text-white"
                  }`
                }
              >
                {tab.label}
              </NavLink>
            ))}
        </nav>
        <div className="border-t border-slate-800 px-5 py-4 text-xs text-slate-500">
          <p>{me?.full_name || "Signed in"}</p>
          <p className="font-mono">{me?.email || ""}</p>
          <p className="mt-1 capitalize">{me?.role?.replaceAll("_", " ")}</p>
          <div className="mt-3 flex gap-3">
            <button type="button" onClick={() => setShowPasswordModal(true)} className="text-teal-300 hover:underline">
              Change Password
            </button>
            <button type="button" onClick={logout} className="text-teal-300 hover:underline">
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-8 py-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-slate-500">Operations control</p>
            <p className="text-sm text-slate-700">Live records from the fleet API · archive instead of erasing history</p>
          </div>
          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-800 ring-1 ring-inset ring-emerald-200">
            Systems nominal
          </span>
        </header>
        <main className="flex-1 p-8">
          <Outlet />
        </main>
      </div>

      {showPasswordModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-slate-900">Change Password</h3>
            <form onSubmit={changePassword} className="mt-4 space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700">Current Password</label>
                <input
                  type="password"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
                  value={passwordForm.current_password}
                  onChange={(e) => setPasswordForm({ ...passwordForm, current_password: e.target.value })}
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700">New Password</label>
                <input
                  type="password"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2"
                  value={passwordForm.new_password}
                  onChange={(e) => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
                  required
                />
                <p className="mt-1 text-xs text-slate-500">Minimum 8 characters</p>
              </div>
              {passwordError && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                  {passwordError}
                </div>
              )}
              {passwordSuccess && (
                <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
                  {passwordSuccess}
                </div>
              )}
              <div className="flex gap-3">
                <button
                  type="submit"
                  disabled={busy}
                  className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-50"
                >
                  {busy ? "Changing..." : "Change Password"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowPasswordModal(false);
                    setPasswordForm({ current_password: "", new_password: "" });
                    setPasswordError("");
                    setPasswordSuccess("");
                  }}
                  className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold hover:bg-slate-50"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
