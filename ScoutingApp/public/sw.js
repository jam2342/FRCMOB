/// <reference lib="webworker" />

/**
 * FRCMOB Service Worker
 *
 * Strategy:
 *  - App shell (HTML, JS, CSS, images): Cache-first, falling back to network.
 *  - API GET requests: Network-first, falling back to cache (stale data offline).
 *  - API mutations (POST/PUT/DELETE): Always network; failures are handled
 *    by the in-app offline queue (offlineQueue.ts).
 *
 * The service worker does NOT attempt to queue mutations itself — that is
 * handled at the application layer with full retry + idempotency support.
 */

const CACHE_NAME = "frcmob-v5";

/** Static asset extensions that should be aggressively cached. */
const STATIC_EXTENSIONS = /\.(js|css|woff2?|ttf|eot|svg|png|jpe?g|gif|ico|webp|json)$/i;

/** Paths that should never be cached by the service worker. */
const NEVER_CACHE = /\/(api|ws)\//;
const DEV_RUNTIME_PATHS = /^(\/@vite\/|\/src\/|\/node_modules\/|\/@fs\/|\/__vite_ping)/;

// ────────────────────────────────────────────────────────────────

function safeCachePut(request, response) {
  caches
    .open(CACHE_NAME)
    .then((cache) => cache.put(request, response))
    .catch(() => {
      // Ignore cache write failures (unsupported schemes, quota, private mode).
    });
}

self.addEventListener("install", (event) => {
  // Activate immediately — don't wait for old clients to close.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Claim all open tabs so the SW controls them without a reload.
  event.waitUntil(
    caches.keys().then((names) => {
      return Promise.all(
        names
          .filter((n) => n !== CACHE_NAME)
          .map((n) => caches.delete(n)),
      ).then(() => self.clients.claim());
    }),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // Only handle GET requests — mutations go straight through.
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Extensions and custom schemes are not cacheable in Cache Storage.
  if (url.protocol !== "http:" && url.protocol !== "https:") return;
  // Restrict caching to same-origin app shell/assets.
  if (url.origin !== self.location.origin) return;

  // Don't intercept API or WebSocket calls — the app's own cache layer
  // (localStorage + in-memory stale-while-revalidate) handles those.
  if (NEVER_CACHE.test(url.pathname)) return;

  // Do not cache Vite dev runtime/module paths.
  if (DEV_RUNTIME_PATHS.test(url.pathname)) return;

  // For navigation requests (HTML), try network first so we always load
  // the latest app shell when online.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const clone = response.clone();
          safeCachePut(request, clone);
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match("/")))
    );
    return;
  }

  // Static assets: cache-first (they have content hashes in filenames).
  if (STATIC_EXTENSIONS.test(url.pathname)) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            safeCachePut(request, clone);
          }
          return response;
        });
      }),
    );
    return;
  }
});

// ── Web Push notifications ──────────────────────────────────────

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: "FRCMOB", body: event.data ? event.data.text() : "" };
  }
  const title = payload.title || "FRCMOB";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body || "",
      tag: payload.tag || undefined,
      icon: "/Heading.png",
      badge: "/Heading.png",
      data: { url: payload.url || "/home" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const rawUrl = (event.notification.data && event.notification.data.url) || "/home";
  // App routes use a HashRouter, so in-app paths live behind "/#".
  const target = /^https?:\/\//.test(rawUrl) ? rawUrl : `/#${rawUrl}`;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.focus();
          if ("navigate" in client && !/^https?:\/\//.test(rawUrl)) {
            client.navigate(target).catch(() => {});
          }
          return;
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
