# Frontend host (Vercel today, probably replaced later)

Moved here from the [speed plan](archive/2026-10/SPEED_PLAN_2026-10-12.md); "#N" below are that plan's items.

Rule: **behavior lives in the backend and the app; the host only serves files and forwards `/api`.**

- **Caching rules are set by the backend** (#2, `backend/app/api/edge_cache.py`), with standard headers (`CDN-Cache-Control`, `Cache-Control`, ETags). A new CDN honors them without code changes. The header name is one setting.
- **No host APIs.** No purge tokens, no edge functions, no host-specific image or blob services. The only Vercel-specific line is the caching opt-in in `vercel.json`.
- **Prebuilt responses in Redis (#9) and the saved copy on the phone (#7) don't care about the host at all**, and they carry most of the speed win. If the edge cache disappeared tomorrow, they still hold.
- **Best long-term shape, once there's a domain:** put the API on its own name (`api.<domain>`) behind a CDN (Cloudflare's free plan is the obvious one), and point the app straight at it instead of through the frontend host's `/api` proxy. Then API caching is independent of whoever hosts the static files, and swapping the frontend host is a DNS change. Costs to plan for:
  - A cross-origin API means CORS preflights for the custom headers (`X-Workspace-Access`, `X-Admin-Session`). Answer `OPTIONS` with `Access-Control-Max-Age: 86400` so a phone pays that once a day.
  - `CORS_ALLOW_ORIGINS` must list the new frontend origin.
  - sslip.io names can't sit behind Cloudflare's proxy, so this waits for a real domain (already needed for the accounts plan's email sender).

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
