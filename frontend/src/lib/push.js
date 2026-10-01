// Browser notification plumbing: service worker registration, permission, and
// Web Push subscription.
//
// Every function degrades quietly. An unsupported browser, a denied permission,
// or a server with no VAPID keys configured all end with in-app notifications
// still working, because those are the source of truth.

import { api } from "./api.js";

export const pushSupported =
  typeof window !== "undefined" &&
  "serviceWorker" in navigator &&
  "PushManager" in window &&
  "Notification" in window;

/** VAPID keys travel as base64url; PushManager wants raw bytes. */
function urlBase64ToUint8Array(base64) {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(padded);
  return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}

/**
 * Register the worker and wait for it to actually take control.
 *
 * register() resolves while the worker is still installing. Both subscribing to
 * push and showing a notification need an *active* worker, so waiting on
 * serviceWorker.ready is the difference between this working and failing
 * silently the first time a user turns notifications on.
 */
export async function registerWorker() {
  if (!pushSupported) return null;
  try {
    await navigator.serviceWorker.register("/sw.js");
    return await navigator.serviceWorker.ready;
  } catch (err) {
    console.error("Service worker registration failed:", err);
    return null;
  }
}

export function permission() {
  return pushSupported ? Notification.permission : "unsupported";
}

/**
 * Ask for permission and, when the server has VAPID keys, register this browser
 * for Web Push. Returns the resulting permission state.
 *
 * Permission alone is worth having: it lets the bell raise a system notification
 * while the tab is merely backgrounded, even if push is not configured.
 */
export async function enableNotifications() {
  if (!pushSupported) return "unsupported";
  const granted = await Notification.requestPermission();
  if (granted !== "granted") return granted;

  const registration = await registerWorker();
  if (!registration) return granted;

  try {
    const { enabled, public_key: publicKey } = await api("/notifications/push/key");
    if (!enabled || !publicKey) {
      console.info("Web Push is not configured on the server; using local notifications only.");
      return granted;
    }
    const existing = await registration.pushManager.getSubscription();
    const subscription =
      existing ||
      (await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      }));
    const raw = subscription.toJSON();
    await api("/notifications/push/subscribe", {
      method: "POST",
      body: JSON.stringify({ endpoint: raw.endpoint, keys: raw.keys }),
    });
  } catch (err) {
    // Logged rather than swallowed: a silent failure here looks identical to
    // working, right up until the notification that matters does not arrive.
    console.error("Could not register this browser for push notifications:", err);
  }
  return granted;
}

/**
 * Raise a system notification for something that just arrived.
 *
 * Used when the tab is alive but not in front. Push covers the closed-tab case
 * from the server side, so this deliberately does nothing when the user is
 * already looking at the page — they will see the toast instead.
 */
export async function showLocalNotification({ title, body, linkPath }) {
  if (!pushSupported || Notification.permission !== "granted") return false;
  try {
    // ready, not getRegistration(): the latter can hand back a registration
    // whose worker has not activated yet, and showNotification then throws.
    const registration = await registerWorker();
    if (!registration) return false;
    await registration.showNotification(title, {
      body,
      tag: "fleet-command",
      renotify: true,
      data: { link_path: linkPath || "/requests" },
    });
    return true;
  } catch (err) {
    console.error("Could not display a system notification:", err);
    return false;
  }
}
