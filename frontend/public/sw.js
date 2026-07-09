// Asterion service worker — reliable browser notifications + Web Push readiness.
//
// Registered by hooks/useNotifications.tsx. Today it lets the app raise OS
// notifications via registration.showNotification() (more reliable than
// `new Notification()` across browsers) and focuses the app when one is
// clicked. The `push` handler is the seam for true closed-tab Web Push: add
// VAPID keys + a backend /subscribe endpoint and it starts working with no
// further client changes.

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Future: real Web Push. The server would send a JSON payload here.
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "Reminder", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Asterion", {
      body: data.body || "",
      tag: data.nid || data.task_id || undefined,
      data: data,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow("/");
    }),
  );
});
