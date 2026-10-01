import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { publicApi } from "../lib/publicApi.js";

// Public, unauthenticated page. Nothing on it may reveal fleet state: no asset
// list, no availability, no agency directory. The visitor describes what they
// need and gets back a reference number, and that is the whole exchange.

const TURNSTILE_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

const EMPTY = {
  request_type: "deploy",
  agency_name: "",
  requester_name: "",
  requester_email: "",
  requester_phone: "",
  requested_date: "",
  address: "",
  quantity: 1,
  notes: "",
};

function loadScript(src) {
  const existing = document.querySelector(`script[src="${src}"]`);
  if (existing) return existing.dataset.loaded ? Promise.resolve() : new Promise((r) => existing.addEventListener("load", r));
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error("Could not load the verification challenge."));
    document.head.appendChild(script);
  });
}

/** Cloudflare Turnstile, rendered only when the server says it is configured. */
function Turnstile({ siteKey, onToken }) {
  const holder = useRef(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let widgetId;
    let cancelled = false;
    loadScript(TURNSTILE_SRC)
      .then(() => {
        if (cancelled || !holder.current || !window.turnstile) return;
        widgetId = window.turnstile.render(holder.current, {
          sitekey: siteKey,
          callback: onToken,
          // A token that expired while the visitor was still typing must not be
          // submitted; clearing it forces a fresh challenge instead of a 400.
          "expired-callback": () => onToken(""),
          "error-callback": () => onToken(""),
        });
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId);
    };
  }, [siteKey, onToken]);

  if (failed) {
    return (
      <p className="text-sm text-amber-700">
        The verification challenge could not load. Check your connection and reload the page.
      </p>
    );
  }
  return <div ref={holder} />;
}

export default function PublicRequest() {
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [accepted, setAccepted] = useState(null);

  useEffect(() => {
    publicApi("/alpr-requests/config")
      .then(setConfig)
      .catch(() => setConfig({ captcha_provider: "none", max_quantity: 50 }));
  }, []);

  const captchaRequired = config?.captcha_provider === "turnstile" && config?.captcha_site_key;

  async function onSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = {
        request_type: form.request_type,
        agency_name: form.agency_name,
        requester_name: form.requester_name,
        requester_email: form.requester_email,
        requester_phone: form.requester_phone || null,
        requested_date: form.requested_date || null,
        address: form.address,
        quantity: Number(form.quantity) || 1,
        notes: form.notes || null,
      };
      if (captchaRequired) payload.captcha_token = token;
      const result = await publicApi("/alpr-requests", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setAccepted(result);
    } catch (err) {
      setError(err.message || "Could not submit the request. Try again.");
      // The challenge is single-use, so a failed submission needs a new one.
      if (captchaRequired && window.turnstile) window.turnstile.reset();
      setToken("");
    } finally {
      setBusy(false);
    }
  }

  if (accepted) {
    return (
      <Shell>
        <div className="rounded-2xl border border-teal-200 bg-white p-8 shadow-sm">
          <h2 className="text-xl font-semibold text-slate-900">Request received</h2>
          <p className="mt-2 text-sm text-slate-600">{accepted.message}</p>
          <p className="mt-6 text-sm text-slate-600">Your reference number</p>
          <p className="font-mono text-2xl font-semibold text-teal-800">{accepted.reference}</p>
          <p className="mt-4 text-xs text-slate-500">
            Keep this number handy — quote it in any email about this request.
          </p>
          <button
            type="button"
            onClick={() => {
              setAccepted(null);
              setForm(EMPTY);
              setToken("");
            }}
            className="mt-6 rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            Submit another request
          </button>
        </div>
      </Shell>
    );
  }

  const field = (key) => ({
    value: form[key],
    onChange: (e) => setForm({ ...form, [key]: e.target.value }),
  });

  return (
    <Shell>
      <form onSubmit={onSubmit} className="space-y-5 rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div>
          <label className="block text-sm font-medium text-slate-700">What do you need?</label>
          <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {[
              { value: "deploy", title: "Deploy trailers", blurb: "Bring ALPR trailers out to a location." },
              { value: "pickup", title: "Pick up trailers", blurb: "Collect trailers that are already on site." },
            ].map((option) => (
              <label
                key={option.value}
                className={`cursor-pointer rounded-xl border p-4 text-sm ${
                  form.request_type === option.value
                    ? "border-teal-500 bg-teal-50 ring-1 ring-teal-500"
                    : "border-slate-300 hover:border-slate-400"
                }`}
              >
                <input
                  type="radio"
                  name="request_type"
                  className="sr-only"
                  value={option.value}
                  checked={form.request_type === option.value}
                  onChange={(e) => setForm({ ...form, request_type: e.target.value })}
                />
                <span className="block font-semibold text-slate-900">{option.title}</span>
                <span className="mt-1 block text-slate-600">{option.blurb}</span>
              </label>
            ))}
          </div>
        </div>

        <Row>
          <Input label="Agency or organization" required minLength={2} maxLength={255} {...field("agency_name")} />
          <Input label="Your name" required minLength={2} maxLength={255} {...field("requester_name")} />
        </Row>
        <Row>
          <Input label="Email" type="email" required maxLength={255} {...field("requester_email")} />
          <Input label="Phone" type="tel" maxLength={64} placeholder="(555) 123-4567" {...field("requester_phone")} />
        </Row>

        <Input
          label={form.request_type === "deploy" ? "Deployment address" : "Pickup address"}
          required
          minLength={5}
          maxLength={512}
          placeholder="123 Main St, Phoenix, AZ 85001"
          hint="Street address, city, state and ZIP so the crew can find the site."
          {...field("address")}
        />

        <Row>
          <Input
            label="Number of trailers"
            type="number"
            min={1}
            max={config?.max_quantity || 50}
            required
            {...field("quantity")}
          />
          <Input
            label="Preferred date"
            type="date"
            min={new Date().toISOString().slice(0, 10)}
            hint="Optional. We will confirm the actual date with you."
            {...field("requested_date")}
          />
        </Row>

        <label className="block text-sm font-medium text-slate-700">
          Anything else we should know?
          <textarea
            rows={4}
            maxLength={2000}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-normal outline-none ring-teal-500 focus:ring-2"
            {...field("notes")}
          />
          <span className="mt-1 block text-xs font-normal text-slate-500">
            Site access, gate codes, point of contact on the day, or anything unusual about the location.
          </span>
        </label>

        {captchaRequired ? <Turnstile siteKey={config.captcha_site_key} onToken={setToken} /> : null}

        {error ? (
          <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>
        ) : null}

        <button
          type="submit"
          disabled={busy || (captchaRequired && !token)}
          className="w-full rounded-lg bg-teal-700 px-4 py-3 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-50"
        >
          {busy ? "Submitting…" : "Submit request"}
        </button>
        <p className="text-xs text-slate-500">
          Submitting this form does not reserve a trailer. The fleet team reviews every request and will
          reply to the email address above.
        </p>
      </form>
    </Shell>
  );
}

function Shell({ children }) {
  return (
    <div className="min-h-screen bg-slate-100">
      <header className="bg-ink-950 px-6 py-10 text-slate-200">
        <div className="mx-auto max-w-3xl">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-teal-400">
            Fleet Command Services
          </p>
          <h1 className="mt-2 text-3xl font-semibold text-white">Request an ALPR trailer</h1>
          <p className="mt-3 max-w-2xl text-sm text-slate-400">
            Tell us where you need trailers and when. No account is required. You will get a reference
            number straight away and a person will follow up by email.
          </p>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-6 py-10">{children}</main>
      <footer className="mx-auto max-w-3xl px-6 pb-10 text-xs text-slate-500">
        Fleet staff can <Link to="/login" className="font-medium text-teal-700 hover:underline">sign in</Link> to
        review requests.
      </footer>
    </div>
  );
}

function Row({ children }) {
  return <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">{children}</div>;
}

function Input({ label, hint, ...props }) {
  return (
    <label className="block text-sm font-medium text-slate-700">
      {label}
      <input
        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-normal outline-none ring-teal-500 focus:ring-2"
        {...props}
      />
      {hint ? <span className="mt-1 block text-xs font-normal text-slate-500">{hint}</span> : null}
    </label>
  );
}
