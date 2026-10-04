/// <reference lib="webworker" />

/**
 * FRCMOB Service Worker
 *
 * Strategy:
 *  - Navigations (HTML): network first, cached copy when offline.
 *  - Static assets (hashed JS/CSS, fonts, images) and on-device models: cache first.
 *  - API and websocket traffic is never touched here; the app's own cache layer
 *    (memory + localStorage) and offline queue (offlineQueue.ts) handle it.
 */

// v7: the recorder page is cross-origin isolated, and a worker it starts refuses any
// script without a COEP header -- assets cached before /assets/* carried one would hang
// the detector's thread pool, so they are dropped.
const CACHE_NAME = "frcmob-v10";

/** Static asset extensions that should be aggressively cached. `wasm`/`mjs` are the
 *  detector's runtime (onnxruntime-web, ~27 MB): without them in the cache the model
 *  loads but nothing can run it, and recording exists for venues without signal. */
const STATIC_EXTENSIONS = /\.(js|mjs|wasm|css|woff2?|ttf|eot|svg|png|jpe?g|gif|ico|webp|json)$/i;
/** The on-device detector. Recording exists for venues without signal, so the model
 *  must come from the cache once it has been loaded online. Model files are named by
 *  version, so a cached copy never goes stale. */
const MODEL_EXTENSIONS = /\/models\/[^/]+\.onnx$/i;

/** Paths that should never be cached by the service worker. */
const NEVER_CACHE = /\/(api|ws)\//;
const DEV_RUNTIME_PATHS = /^(\/@vite\/|\/src\/|\/node_modules\/|\/@fs\/|\/__vite_ping)/;

// ────────────────────────────────────────────────────────────────

function safeCachePut(request, response, cacheName = CACHE_NAME) {
  return caches
    .open(cacheName)
    .then((cache) => cache.put(request, response))
    .catch(() => {
      // Ignore cache write failures (unsupported schemes, quota, private mode).
    });
}

self.addEventListener("install", (event) => {
  // Install the two entry documents and their immediate JS/CSS before replacing
  // the previous worker. If any critical fetch fails, the old worker stays active.
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    const assets = new Set([
      "/", "/record.html", "/manifest.json", "/Heading.png", "/Heading.webp",
      "/fonts/ibm-plex-sans-latin.woff2", "/fonts/ibm-plex-sans-latin-ext.woff2",
    ]);
    for (const path of ["/", "/record.html"]) {
      const response = await fetch(path, { cache: "reload" });
      if (!response.ok) throw new Error(`Offline shell ${path}: ${response.status}`);
      const html = await response.clone().text();
      for (const match of html.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g)) assets.add(match[1]);
      await cache.put(path, response);
    }
    for (const path of assets) {
      if (path === "/" || path === "/record.html") continue;
      const response = await fetch(path, { cache: "reload" });
      if (!response.ok) throw new Error(`Offline shell ${path}: ${response.status}`);
      await cache.put(path, response);
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  // Claim all open tabs so the SW controls them without a reload.
  event.waitUntil(
    caches.keys().then((names) => {
      // Keep the previous asset cache for tabs still running its hashed JS.
      // Saved API responses live in a separate cache and are never touched here.
      const assetCaches = names.filter((n) => /^frcmob-v\d+$/.test(n)).sort((a, b) => Number(b.slice(8)) - Number(a.slice(8)));
      const keep = new Set([CACHE_NAME, ...assetCaches.slice(0, 2)]);
      return Promise.all(
        assetCaches
          .filter((n) => !keep.has(n))
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
          if (response.status >= 500) throw new Error(`Navigation failed: ${response.status}`);
          if (response.ok) {
            const clone = response.clone();
            event.waitUntil(safeCachePut(request, clone));
          }
          return response;
        })
        .catch(async () => {
          const current = await caches.open(CACHE_NAME);
          // Recorder navigations must keep their COOP/COEP document, including
          // when an unseen query string misses the exact cached request.
          const shell = url.pathname === "/record.html" ? "/record.html" : "/";
          return (await current.match(request)) || (await current.match(shell)) || caches.match(shell);
        })
    );
    return;
  }

  // Static assets and models: cache-first (content-hashed or versioned names).
  if (STATIC_EXTENSIONS.test(url.pathname) || MODEL_EXTENSIONS.test(url.pathname)) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            event.waitUntil(safeCachePut(request, clone, MODEL_EXTENSIONS.test(url.pathname) || url.pathname.endsWith(".wasm") ? "frcmob-offline-models-v1" : CACHE_NAME));
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
