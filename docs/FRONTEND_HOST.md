# Frontend host (Cloudflare at frcmob.com; Vercel until the old address is retired)

## Cloudflare (frcmob.com, since 2026-11-10)

- **Site:** the Worker `frcmob-web` (`ScoutingApp/wrangler.jsonc`) is static assets only, no Worker code, so requests aren't metered. `npm run build:cloudflare` builds `dist/` and writes `_headers` from `vercel.json` (`scripts/cloudflare-assets.mjs`), so both hosts send the same headers. `.github/workflows/web-deploy.yml` publishes after CI passes on `main`, with the `CLOUDFLARE_WORKERS_TOKEN` secret (Workers Scripts: Edit only) in the `production` environment.
- **Routes on the zone** (set once, not by the deploy):
  - `frcmob.com/*` goes to `frcmob-web`.
  - `frcmob.com/api/*`, `frcmob.com/models/*` and `frcmob.com/assets/ort-wasm-simd-threaded.jsep-*` have no Worker, so Cloudflare proxies them to the VM. Caddy strips `/api` and proxies to the backend, so the API is same-origin as it was behind Vercel's rewrite, and room sockets work through it too.
- **DNS:** `frcmob.com`, `www` and `api` are proxied A records to the VM. Caddy redirects `www` to the apex. SSL mode is Full (strict), with a Cloudflare Origin CA certificate on the VM ([BACKEND_HOST.md](BACKEND_HOST.md)).
- **Cache rules:** `/api/*` follows the origin's headers (`CDN-Cache-Control` from `edge_cache.py`) and bypasses the cache when there are none. `/models/*` and the engine file are cached as the origin says (a week, a year).
- **Files over Cloudflare's 25 MiB per-file limit** come from the VM's model-files folder (`MODEL_FILES_TOKEN` in the VM's `.env`):
  - the detector weights
  - onnxruntime's WebGPU engine `ort-wasm-simd-threaded.jsep-<hash>.wasm` (28 MiB)

  The engine's name changes whenever `onnxruntime-web` changes. When it does, copy `ScoutingApp/dist/assets/<that file>` into the folder; `web-deploy.yml` refuses to publish until `https://frcmob.com/assets/<that file>` answers. The build fails if anything else crosses 25 MiB.
- **Rate limit:** one rule (the free plan's only one): `frcmob.com/api/*` requests that reach the server, 300 per 10 s per IP, then blocked for 10 s. Generous because a venue's Wi-Fi puts a whole crowd behind one IP. `api.frcmob.com` isn't covered: Vercel's old `/api` rewrite arrives there from a handful of Vercel IPs.
- **The old address:** `scouting-app-iryg.vercel.app` still builds from `main`; its `/api` rewrite goes to `api.frcmob.com`. The app there hands people over to frcmob.com (`src/platform/hostMove.ts`), but only once that browser has nothing left to sync (queued edits, recordings not yet uploaded, pit drafts), since browser storage belongs to each address. Logins don't carry over: teams rejoin with their join code once.
- **Analytics:** Cloudflare Web Analytics (`src/perf/webAnalytics.ts`), only on frcmob.com, off under DNT/GPC. Vercel Web Analytics stays only on `*.vercel.app`.


Moved here from the [speed plan](archive/2026-10/SPEED_PLAN_2026-10-12.md); "#N" below are that plan's items.

Rule: **behavior lives in the backend and the app; the host only serves files and forwards `/api`.**

- **Caching rules are set by the backend** (#2, `backend/app/api/edge_cache.py`), with standard headers (`CDN-Cache-Control`, `Cache-Control`, ETags). A new CDN honors them without code changes. The header name is one setting.
- **No host APIs.** No purge tokens, no edge functions, no host-specific image or blob services. The only Vercel-specific line is the caching opt-in in `vercel.json`.
- **Prebuilt responses in Redis (#9) and the saved copy on the phone (#7) don't care about the host at all**, and they carry most of the speed win. If the edge cache disappeared tomorrow, they still hold.
- **Why `/api` stayed same-origin on Cloudflare** instead of the app calling `api.frcmob.com` directly: the service worker (offline pit photos, push-alert prefetch) and the boot prefetch all assume `/api` on the page's own origin, and a cross-origin API would add CORS preflights for the custom headers. Cloudflare routes `frcmob.com/api/*` to the VM with no Worker, so it costs nothing per request.

## Host move checklist

What `vercel.json` does today, which any replacement must do:

1. SPA fallback: every path → `index.html`.
2. `/api/*` → `https://<backend>/*` proxy, keeping request headers, or the direct `api.<domain>` setup above.
3. `/record.html`: `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp` (the recorder's multi-threaded WASM fails silently without them).
4. `/assets/*`: `Cache-Control: public, max-age=31536000, immutable` **and** `Cross-Origin-Embedder-Policy: require-corp` (a worker started from the isolated page hangs without it).
5. `/fonts/*` 30 days, `/models/*` 7 days with `Content-Type: application/octet-stream`.
6. Build-time check that the ONNX models are present (`ONDEVICE_MODEL_REQUIRED` keys off `VERCEL` today; switch it to a host-neutral env var), and `ONDEVICE_MODEL_SOURCE` set so `scripts/fetch-models.mjs` can download them (they aren't in git).
7. Turn on API caching for `/api` at the new host and re-run the MISS/HIT and workspace-header checks from #2.
8. Native shells point at the public site URL (`shareableAppUrl()`), so check that after a domain change.
9. Every path: `Content-Security-Policy: frame-ancestors 'none'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (camera/microphone self). The rest of the CSP travels in the HTML (`build/contentSecurityPolicy.ts`); if the backend's address changes, update `NATIVE_WS_URL` or the room socket is blocked.
10. Turnstile: add the new hostname to the widget and to `TURNSTILE_ALLOWED_HOSTNAMES`, or workspace signup breaks.
