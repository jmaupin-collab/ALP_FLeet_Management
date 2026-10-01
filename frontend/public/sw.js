/* Service worker for Fleet Command browser notifications.
 *
 * This runs outside the page, which is the whole point: a new ALPR request has
 * to be able to reach someone whose tab is in the background or closed, and a
 * React toast cannot do that.
 */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: "Fleet Command", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || "Fleet Command", {
      body: payload.body || "",
      // Same tag for the whole feed, so a burst of requests replaces rather
      // than stacks up a wall of notifications.
      tag: "fleet-command",
      renotify: true,
      data: { link_path: payload.link_path || "/requests" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.link_path || "/requests", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      // Reuse an open Fleet Command tab instead of piling up new ones.
      for (const client of clients) {
        if (client.url.startsWith(self.location.origin) && "focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
