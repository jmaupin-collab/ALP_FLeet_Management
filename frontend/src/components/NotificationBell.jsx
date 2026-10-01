import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api.js";
import { enableNotifications, permission, pushSupported, showLocalNotification } from "../lib/push.js";
import { NOTIFICATIONS_CHANGED, formatDateTime } from "../lib/requests.jsx";

// Three ways to hear about a new request, in order of how much attention the
// user is paying:
//
//   looking at the app   -> in-app toast
//   tab open, elsewhere  -> system notification raised by the service worker
//   app closed entirely  -> Web Push, delivered by the server
//
// The unread feed underneath is the source of truth; the other two are prompts.

const POLL_MS = 30000;
const TOAST_MS = 8000;

export default function NotificationBell() {
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState(null);
  const [permissionState, setPermissionState] = useState(permission());
  const seen = useRef(null);
  const wrapRef = useRef(null);

  const poll = useCallback(async () => {
    try {
      const feed = await api("/notifications?limit=25");
      setItems(feed.items);
      setUnread(feed.unread_count);

      const fresh = feed.items.filter((item) => !item.read_at);
      // The first poll establishes a baseline. Without it, every sign-in would
      // announce a backlog the user has already dealt with.
      if (seen.current === null) {
        seen.current = new Set(fresh.map((item) => item.id));
        return;
      }
      const arrived = fresh.filter((item) => !seen.current.has(item.id));
      seen.current = new Set(fresh.map((item) => item.id));
      if (arrived.length === 0) return;

      const latest = arrived[0];
      // hasFocus(), not just visibilityState: a browser window sitting behind
      // Slack still reports itself as "visible", and an in-app toast the user
      // cannot see is the same as no notification at all. Anything short of
      // actually looking at this page gets a system notification instead.
      if (document.visibilityState === "visible" && document.hasFocus()) {
        setToast(latest);
      } else {
        showLocalNotification({
          title: arrived.length === 1 ? latest.title : `${arrived.length} new notifications`,
          body: latest.body,
          linkPath: latest.link_path,
        });
      }
    } catch {
      // A failed poll is not worth surfacing; the next one is 30 seconds away.
    }
  }, []);

  useEffect(() => {
    poll();
    const timer = setInterval(poll, POLL_MS);
    // Coming back to the tab should show the current state immediately.
    const onVisible = () => document.visibilityState === "visible" && poll();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener(NOTIFICATIONS_CHANGED, poll);
    // Already granted is not the same as already subscribed: permission can
    // survive from an earlier session while the push registration is missing,
    // and the enable button never shows in that state. Re-running it is
    // idempotent and does not re-prompt.
    if (pushSupported && Notification.permission === "granted") enableNotifications();
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener(NOTIFICATIONS_CHANGED, poll);
    };
  }, [poll]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(event) {
      if (!wrapRef.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  async function openItem(item) {
    setOpen(false);
    setToast(null);
    if (!item.read_at) {
      try {
        await api(`/notifications/${item.id}/read`, { method: "POST" });
      } catch {
        /* navigating matters more than the read receipt */
      }
      poll();
    }
    if (item.link_path) navigate(item.link_path);
  }

  async function markAllRead() {
    await api("/notifications/read-all", { method: "POST" });
    poll();
  }

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((on) => !on)}
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        className="relative rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
      >
        <span aria-hidden>🔔</span>
        {unread > 0 ? (
          <span className="absolute -right-1 -top-1 min-w-[1.25rem] rounded-full bg-rose-600 px-1 text-center text-[11px] font-semibold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="absolute right-0 z-40 mt-2 w-96 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <p className="text-sm font-semibold text-slate-900">Notifications</p>
            {unread > 0 ? (
              <button type="button" onClick={markAllRead} className="text-xs font-medium text-teal-700 hover:underline">
                Mark all read
              </button>
            ) : null}
          </div>

          {permissionState === "default" && pushSupported ? (
            <div className="border-b border-slate-100 bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-600">
                Get told about new requests even when Fleet Command is not the tab you are looking at.
              </p>
              <button
                type="button"
                onClick={async () => setPermissionState(await enableNotifications())}
                className="mt-2 rounded-lg bg-teal-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-teal-800"
              >
                Turn on browser notifications
              </button>
            </div>
          ) : null}
          {permissionState === "denied" ? (
            <p className="border-b border-slate-100 bg-slate-50 px-4 py-3 text-xs text-slate-600">
              Browser notifications are blocked for this site. Re-enable them in your browser settings if you
              want alerts while the tab is in the background.
            </p>
          ) : null}

          <ul className="max-h-96 divide-y divide-slate-100 overflow-y-auto">
            {items.length === 0 ? (
              <li className="px-4 py-8 text-center text-sm text-slate-500">Nothing yet.</li>
            ) : null}
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => openItem(item)}
                  className={`block w-full px-4 py-3 text-left hover:bg-slate-50 ${
                    item.read_at ? "" : "bg-teal-50/40"
                  }`}
                >
                  <p className="text-sm font-medium text-slate-900">{item.title}</p>
                  <p className="mt-0.5 text-xs text-slate-600">{item.body}</p>
                  <p className="mt-1 text-[11px] text-slate-400">{formatDateTime(item.created_at)}</p>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {toast ? (
        <div className="fixed bottom-6 right-6 z-50 w-80 rounded-xl border border-slate-200 bg-white p-4 shadow-xl">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm font-semibold text-slate-900">{toast.title}</p>
            <button
              type="button"
              onClick={() => setToast(null)}
              aria-label="Dismiss"
              className="text-slate-400 hover:text-slate-700"
            >
              ×
            </button>
          </div>
          <p className="mt-1 text-xs text-slate-600">{toast.body}</p>
          {toast.link_path ? (
            <button
              type="button"
              onClick={() => openItem(toast)}
              className="mt-3 text-xs font-semibold text-teal-700 hover:underline"
            >
              Open request
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
