import { useState } from "react";
import { API_BASE, setToken } from "../lib/api.js";
import { clearMeCache } from "../lib/useMe.js";
import { canonicalRole } from "../lib/roles.js";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        throw new Error("Incorrect email or password");
      }
      const data = await response.json();
      setToken(data.access_token);
      clearMeCache();
      const me = await fetch(`${API_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${data.access_token}` },
      }).then((res) => (res.ok ? res.json() : null));
      window.location.replace(canonicalRole(me) === "customer" ? "/inspections" : "/");
    } catch (err) {
      setError(err.message || "Could not reach the API. Start the backend and try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-950 px-4">
      <form onSubmit={onSubmit} className="w-full max-w-md rounded-2xl border border-slate-800 bg-ink-900 p-8 text-slate-100 shadow-xl">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-teal-400">Fleet Command Services</p>
        <h1 className="mt-2 text-2xl font-semibold text-white">Sign in</h1>
        <label className="mt-6 block text-sm">
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-ink-800 px-3 py-2 text-sm text-white outline-none ring-teal-500 focus:ring-2"
          />
        </label>
        <label className="mt-4 block text-sm">
          Password
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-ink-800 px-3 py-2 text-sm text-white outline-none ring-teal-500 focus:ring-2"
          />
        </label>
        {error ? <p className="mt-3 text-sm text-amber-300">{error}</p> : null}
        <button
          type="submit"
          disabled={loading}
          className="mt-6 w-full rounded-lg bg-teal-500 px-4 py-2.5 text-sm font-semibold text-ink-950 hover:bg-teal-400 disabled:opacity-60"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
