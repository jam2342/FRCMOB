# Speed plan (2026-10-12)

Twenty ways to make FRCMOB faster, each with what the code does today, how to build it, what can go wrong, and how to prove it worked. Based on `main` = `092925c`, production measured on the VM's loopback (no Vercel hop), and the backend's own slow-request log for the last 72 hours.

## Results (same day, PRs #146–#155)

Everything below was measured on production or on a production build against production data, phone settings unless noted (390 px, 150 ms latency, 1.6 Mbps, 4× CPU).

| # | Item | Outcome |
|---|---|---|
| 9 | Server cold paths | **Shipped (#146).** `/events/suggested` 2.9 s → 10 ms (Redis, refreshed in the background). Statbotics breaker shared in Redis with a growing cooldown, one probe at a time, no retries on slow 500s, 2.5 s budget in intel. **Correction:** the app's pages never ask for Statbotics; the 6–19 s cold intel timings were my own probes. The fix matters for background jobs. |
| 20, 10 | Request audit | **Shipped (#147).** Home: 31 requests → 9, last data 10.9 s → 7.0 s. New `GET /matches/schedules` batch (identical to per-event answers). Duplicate suggested-events requests gone (one shared request); live ratings dedupe overlapping calls. Team Center's second intel request was correct behaviour (team not at that event). |
| 11 | Data in the first HTML | **Shipped (#147, #151).** index.html starts the startup request and the opened route's page chunk + CSS. First version waited for the stylesheet (inline scripts do); moved ahead of it: request at ~240 ms (was ~2 s), page chunk at ~260 ms (was ~2.3 s), page data 150–400 ms sooner. |
| 1 | Real-user timings | **Shipped (#148).** LCP/FCP/TTFB/CLS/INP + time to first data per route, counts only, privacy page updated. Admin summary at `/telemetry/vitals/summary`. It already found the Team Center layout shift below. |
| 18 | Send less JSON | **Shipped (#148).** Ratings list 864 KB → 278 KB (`view=summary` drops unused `details`/`evidence`). |
| 2, 3, 8 | Edge cache + ETags | **Shipped (#149).** Verified on Vercel: MISS 0.81 s → HIT 0.18 s; 304 on revalidation (also through Caddy compression); finished events cached 1 h; admin and private routes `private, no-store`; a test classifies every GET route. |
| 4 | Compression | **Shipped (#149).** Caddy zstd/gzip (team history 1.13 MB → 60 KB zstd); Python gzip turned off in prod `.env` (backup `.env.bak-before-caddy-compression-20261012`). orjson not adopted (warm server times already 10–300 ms). |
| 15 | Prefetch on touch | **Shipped (#149).** Team Center, Match Center, Events links; pages and prefetcher share request builders. |
| 7 | Saved copy first | **Shipped (#150).** Live: Team Center revisit after the cache expired shows content in 229 ms (time to first data ≤100 ms), refreshed from the edge. Opt-in for polling pages only. **Correction:** big responses were already saved (Cache Storage); the gap was online use and screen updates. |
| 14 | Preload before matches | **Shipped (#153).** 15 min before My Team's match (app open), and in the service worker when a match alert arrives. |
| 6 | content-visibility | **Shipped (#151)** on phone table cards (an event's 70+ team cards). |
| — | Team Center layout shift | **Shipped (#155).** Found by #1: CLS 0.14 → 0.000 on phones. |
| 5 | CSS diet | **Measured, mostly skipped.** 89% of the global stylesheet is used by some route/width/theme; dead rules ≈ 4 KB on the wire. Tour overlay made lazy instead (#151). |
| 12 | ScoutingPage render cost | **Measured, skipped.** Counter taps: median 40 ms, p90 56 ms at 6× CPU on 250 px (good is < 200 ms). |
| 13 | Virtualize lists | **Measured, skipped.** Largest page ~4,000 DOM nodes. |
| 19 | Python 3.13 | **Draft (#154), not merged.** ~4% faster (noise); 10 of 155 ratings move ≤0.15 points (Python 3.12+ sums floats more exactly). Merge only for the numpy/maintenance case. |
| 16 | Phone-to-phone | **Not built.** Needs new dependencies (QR, scanner) and a sync design; not a speed item. |
| 17 | Recorder on the phone's AI chip | **Not built.** Needs a physical iPhone (deferred). |

What's left on a slow phone's first visit: ~145 KB gzipped of React, router and the API client plus ~1 s of CPU at 4× before a page can ask for its data. The next step there would be a lighter React (Preact) or server-rendered HTML, both large changes.

## What the measurements say

Measured on production at 17:05 UTC, 2026arc unless noted:

| Endpoint | Server time | Raw size | Gzip |
|---|---|---|---|
| `teams/event/{ev}/intel`, **cold** | **19.0 s** | 153 KB | 13 KB |
| `teams/{team}/intel`, **cold** | **5.9–6.8 s** | 25 KB | 6 KB |
| same two, warm | 8–30 ms | | |
| `events/suggested` | 0.5 s here; **2.0–3.4 s, 15 times in 72 h** in the log | 4 KB | 0.8 KB |
| `scouting/event/{ev}/teams/history` | 0.27 s here; **~3 s, 10 times in 72 h** in the log | **1.1 MB** | 67 KB |
| `scouting/event/{ev}/ratings` | 40 ms | **864 KB** (14.6 KB per team) | 65 KB |
| `events/{ev}/schedule-with-synergy` | 213 ms | 233 KB | 20 KB |
| `events/{ev}/predictions` | 320 ms | 167 KB | 52 KB |

Other facts that shape the plan:

- Only **one** backend route sets `Cache-Control` (the match schedule). Every other API call goes browser → Vercel → Phoenix, every time.
- Vercel can cache proxied responses now, but this project predates the April 2026 default, so it has to opt in ([changelog](https://vercel.com/changelog/vercels-cdn-now-respects-cache-control-headers-from-external-origins-by-default), [rewrites docs](https://vercel.com/docs/rewrites)).
- `api.ts` already has a two-level GET cache (memory + localStorage) with stale-while-revalidate, but stale copies are served for only **45 s** past expiry, background refreshes don't reach the page, and bodies over **450 KB** are never saved. So ratings and team history can't be saved outside the offline pack.
- The app uses **HashRouter**: the server never sees `/#/team/254`, so per-route server rendering or link previews aren't possible without changing routers.
- Team Center fires **8** GETs on load; Events fires 9 (including `getApiHealth`).
- Main bundle: JS 308 KB raw / **81 KB brotli**; CSS 356 KB raw / **42 KB brotli**, render-blocking on every route. `shell-foundation.css` 133 KB, `shell-mobile.css` 95 KB, `shell-overrides.css` 35 KB.
- `ScoutingPage.tsx` is **5,217** lines. No list virtualization anywhere.
- Backend: Python 3.11, 2 uvicorn workers on 4 cores (load average 0.05), `GZipMiddleware`, and a `@app.middleware("http")` (BaseHTTPMiddleware) wrapper on every request.
- Traffic is low: ~19,000 requests in 72 h. The server isn't overloaded. The pain comes from cold paths, distance and phone-side work.

---

## 1. Measure real users (web vitals)

**Today:** nothing. Every speed number so far comes from an M2 Mac or the VM.

**Build:**
- Frontend `src/perf/rum.ts`: `web-vitals` (pin an exact version, attribution build) for LCP, INP, CLS, TTFB, FCP. Add one custom metric, **time to first data**: route mount → first successful `apiFetch` resolve for that route. Tag each sample with the route *pattern* (`/team/:key`, never the key), viewport bucket (≤400 / ≤768 / desktop), `navigator.connection.effectiveType`, standalone PWA vs native vs browser, and whether the response came from cache.
- Batch in memory and send once on `visibilitychange → hidden` with `navigator.sendBeacon('/api/telemetry/vitals', blob)`.
- Backend `routes_telemetry.py`: `POST /telemetry/vitals`. Validate hard (metric-name allowlist, numeric bounds, ≤50 samples per beacon) and rate-limit per IP in Redis. Store **aggregates only**: a Redis hash per day `vitals:{yyyymmdd}` with histogram buckets per (route, metric, device class), 30-day TTL. No migration, no raw rows, nothing personal.
- **Trap:** `enforce_write_request_access` blocks anonymous POSTs when `ENFORCE_ADMIN_AUTH_FOR_WRITES=true`. Add this one path to its allowlist, and nothing else.
- Add a `Server-Timing: app;dur=…` header in the existing request middleware so the browser can split server time from network time.
- Report: `scripts/vitals_report.py` prints p50/p75/p95 per route and device class. Optionally an admin card on the ops dashboard.
- Privacy page: one sentence (anonymous performance timings, no identifiers).

**Watch out:** beacons from bots skew results. Drop samples without a real `effectiveType`/viewport, and cap per IP.

**Prove it:** local stack, open three routes, see the hash fill. After deploy, wait 48 h and read the report.

**Size:** ~1 day. **Do first.** It tells you which of the rest actually matter on phones.

## 2. Cache public data at the edge (CDN)

**Today:** no edge caching. A visitor in Boston pays Boston → Vercel → Phoenix on every request.

**Build:**
1. Backend: one **cache policy table** (`app/api/cache_policy.py`) mapping route patterns to a policy, applied by a small pure-ASGI middleware on GET 200 responses only:
   - `public-live` (schedules, live form, predictions, rankings during a running event): `CDN-Cache-Control: max-age=15, stale-while-revalidate=60`
   - `public-event` (ratings, team history, event intel, awards, alliances): 60 s fresh / 10 min stale while the event is running; **1 h fresh / 7 days stale once the event is settled** (regional automation already knows "settled")
   - `public-static` (logos, robot images, suggested events): 1 day
   - everything else (default): `Cache-Control: private, no-store`
   - Browsers get `Cache-Control: no-cache`, so the browser HTTP cache doesn't fight `api.ts`'s own cache. The CDN rule goes in **`CDN-Cache-Control`** (RFC 9213: Vercel, Cloudflare, Netlify, Akamai read it; browsers ignore it), not `Vercel-CDN-Cache-Control`. The header name comes from one setting (`EDGE_CACHE_HEADER`), so a host that only reads `s-maxage` (CloudFront) or `Surrogate-Control` (Fastly) is an env change. See "Staying host-agnostic" at the end.
2. Hard privacy rules, enforced in the middleware rather than per route:
   - Any request carrying `X-Workspace-Access` or an admin session on a route that *could* personalize → `private, no-store`.
   - `refresh=true` → `no-store`.
   - Workspace routes (picklists, pit, rooms, workspaces, on-device sessions, push) are never in the table. A test fails if one is.
   - **Re-check the existing `public, max-age=30` on the schedule route** before turning edge caching on, since it would start being honored.
3. A route-inventory test: every GET route must be listed in the policy table (public or private, explicitly), so a new route can't be cached by accident.
4. Turn on caching at the current host. For Vercel that's `x-vercel-enable-rewrite-caching: 1` for `/api/(.*)` in `vercel.json` (versioned and reviewable, unlike the dashboard toggle). This is the only Vercel-specific line in the item.
5. **No purging, by design.** Short TTLs while an event runs (15–60 s) and 1 h for settled events bound staleness on any host, without a host API token. If purging is ever needed, it goes behind a `purge_event(event_key)` adapter with one implementation per host (Vercel `Vercel-Cache-Tag`, Cloudflare `Cache-Tag`, Fastly `Surrogate-Key`). Don't build it until a real need shows up.

**Watch out:**
- A cached private response would be a data leak. The default-private middleware, the inventory test, and a production check with and without a workspace header are all required.
- 4xx/5xx must never be cached (the policy applies only to 200).
- Host bandwidth doesn't change: Vercel already proxies everything.

**Prove it:** through the SOCKS tunnel (memory: polling the live site gets the Mac's IP challenged), request each policy class twice and expect a cache MISS then HIT (`x-vercel-cache` on Vercel, `cf-cache-status` on Cloudflare). Send the same request with a workspace header and expect no HIT. RUM (#1) TTFB per route before and after.

**Size:** 1–1.5 days. Biggest win for anyone not in the US West.

## 3. ETags on polled endpoints

**Today:** Predictions (60 s), Match Center (5 s), live ratings and team-room polls download the full body every time. `useTeamRoom` already throws away unchanged answers, but only after downloading and parsing them.

**Build:**
- Backend: a pure-ASGI middleware for GET 200 JSON computes a weak ETag (`W/"` + blake2b-8 of the body + `"`). If `If-None-Match` matches, it returns 304 with no body. Put it *before* compression so the tag reflects the body, not the encoding. With #9 the ETag is stored with the prebuilt body, so it costs nothing.
- Frontend `apiFetch`: store `etag` on `CachedResponse`. When refetching a key that has a cached entry, send `If-None-Match`. On 304, reuse the cached body, bump `expiresAtMs`, and tell the caller "unchanged" so polling hooks skip `setState` entirely.
- CDNs (Vercel, Cloudflare and others) pass conditional requests through and answer them from their own cache when they have one.

**Watch out:** the existing `apiFetch` rebuilds every `Response` from an `ArrayBuffer`. 304 has a null body, which is already in `NULL_BODY_STATUSES`, so keep that path intact.

**Prove it:** `curl -H 'If-None-Match: …'` → 304. In Playwright, leave Predictions open 5 minutes and count 304s vs 200s.

**Size:** ~half a day.

## 4. Move compression to Caddy, faster JSON, drop BaseHTTPMiddleware

**Today:** `GZipMiddleware` (level 5) compresses in Python. `request_access_and_logging_middleware` is a `@app.middleware("http")`, which wraps every response in Starlette's BaseHTTPMiddleware (extra task plus body streaming overhead).

**Build:**
- `docker/Caddyfile`: `encode zstd gzip` inside the site block. Then set `RESPONSE_COMPRESSION_ENABLED=false` in the prod `.env`. Caddy core has no brotli encoder; Vercel talks to the origin over gzip anyway.
- Rewrite the access/logging middleware as a pure ASGI middleware. Same behavior, no BaseHTTPMiddleware.
- `orjson`: `FastAPI(default_response_class=ORJSONResponse)`, with a benchmark *first*. Recent FastAPI can serialize `response_model` routes through Pydantic's Rust core, so the win is mostly on routes that return plain dicts (most of them here).

**Watch out:**
- orjson writes datetimes and NaN differently from the stdlib (`NaN` becomes `null`, which is actually more correct JSON). Diff the JSON from the top 10 endpoints before and after.
- If Caddy compression is misconfigured, everything goes out uncompressed. Check `content-encoding` at the browser.

**Prove it:** time the ratings endpoint (864 KB) 50× before and after on the VM. Expect single-digit-ms savings per request.

**Size:** half a day. **Honest value: low.** Warm server times are already 10–300 ms. Do it when touching that code, not as a priority.

## 5. Cut the CSS

**Today:** 356 KB raw / 42 KB brotli of global CSS, render-blocking on every route, including recorder and scouting styles on the Home page.

**Build:**
1. **Coverage sweep:** extend the polish sweep script (26 routes × 250/390/1366 × light/dark) to record `CSS.takeCoverageDelta` via CDP in real Chrome, and merge used ranges across all runs.
2. **Two-gate delete:** a rule is removable only if (a) no run used it **and** (b) none of its class names appear anywhere in `src/`. This stops deleting hover/error/modal/offline states the sweep never triggered.
3. **Move feature CSS to its route:** scouting, recorder, picklist and pit blocks in `shell-features.css` / `shell-mobile.css` become CSS modules imported by the lazy page, so they ship in that chunk.
4. **Fold `shell-overrides.css` into the rules it overrides** (mind the `@layer` order, see memory `frontend-remodel-progress`).

**Watch out:**
- The unlayered `touch-polish.css` beats layered rules. Keep that ordering.
- The static colour/type guards in CI will catch some mistakes, but not all.

**Prove it:** screenshot diff main vs branch on the full sweep. The bar is byte-identical except animation pixels, as in #115. Then compare CSS size and LCP on a throttled first visit.

**Size:** 1–2 days. Realistic target: 30–50% smaller main CSS.

## 6. `content-visibility: auto` for long pages

**Today:** every card on Team Center, Events and Match Center is laid out and painted, even far below the fold.

**Build:** an opt-in utility class, not a global rule:

```css
.cv-auto { content-visibility: auto; contain-intrinsic-size: auto 480px; }
```

Apply it to below-the-fold `SurfaceCard` sections and long tables (the Events team table, team history, prediction lists).

**Watch out:**
- It implies paint containment, so **anything that overflows the card gets clipped**: tooltips, dropdowns or popovers rendered inside the card. Portaled ones are fine. Don't put it on cards with inline popovers.
- `contain-intrinsic-size: auto` remembers the real size after first render, which avoids scrollbar jumps.

**Prove it:** Chrome Performance panel at 4× CPU throttle on Team Center and Events: rendering time on load and scroll, before and after. RUM INP after deploy.

**Size:** 2–3 hours.

## 7. Saved copy first, then refresh (everywhere)

**Today:** a 4-day saved copy exists but is shown only when offline or after a 4 s weak-signal wait. In normal use, anything older than TTL + 45 s means a full network wait. Background refreshes update the cache but not the screen. Bodies over 450 KB (ratings, team history) aren't saved at all.

**Build:**
1. **Move the persistent cache from localStorage to IndexedDB** (`utils/apiResponseDatabase.ts` already exists for native). localStorage is synchronous (blocks the main thread on big `JSON.parse`), has a ~5 MB cap, and can't be read by the service worker (which #14 needs). Lift the 450 KB cap and keep the 300-entry LRU.
2. **Read-through mode:** for routes marked `showSaved` in `apiFetch`, any saved copy ≤4 days old returns immediately (tagged with its age), and revalidation starts at once.
3. **Change notifications:** when the background refresh lands and the body differs (compare ETags from #3), emit `api:updated` on an `EventTarget` keyed by request key.
4. **`useApiResource(key, loader)` hook** subscribes and re-renders. Adopt page by page: Team Center, Events, Home, Strategy first.
5. **Honest UI:** a small "Updated 2 h ago · refreshing" note using the existing freshness copy. Live scores, timers and anything `liveOnly` never use read-through.

**Watch out:**
- Showing an old schedule mid-event. Live-policy routes get a short "show without warning" window (e.g. 2 min). Older copies show with a visible "refreshing" state.
- The IndexedDB migration must keep the offline pack's verify step and 4-day retention working. Rerun its tests and the offline driven flow from memory `frcmob-mobile-qa`.

**Prove it:** Playwright, visit Team Center, come back 10 minutes later on throttled "Slow 4G". Content should paint before the network finishes. RUM "time to first data" for repeat visits.

**Size:** 2–3 days. Second-biggest win after #2/#9 for people who use the app daily.

## 8. Finished events as static files

**Today:** 290 of 299 events are finished and never change, but every request rebuilds or reads them through Python.

**Recommendation:** get this **from #2 instead of new storage**. Settled events get `CDN-Cache-Control: max-age=3600, stale-while-revalidate=604800`, and whichever CDN is in front then is the static file host. A true static export doesn't fit the free tiers: ~299 events × ~10 endpoints × ~200 KB ≈ 600 MB is too big to deploy with the frontend, OCI Object Storage has no free CDN, and R2 needs a domain.

**Only if you want the site to work with the VM down:** a nightly job writes a gzipped "season archive" per event into OCI Object Storage (Always Free 20 GB), and `api.ts` falls back to it on 5xx/timeout. That's resilience, not speed. Park it.

**Size:** zero extra on top of #2 (just the settled-event TTLs).

## 9. Build responses when data changes, not when someone asks

**Today:** this is where the worst measured numbers come from. Cold intel takes 6–19 s, `/events/suggested` 2–3.4 s, team history ~3 s. The hot-intel refresher covers at most 25 events per run.

**Build, in order:**
1. **Find the cold-intel cost first.** Add per-builder timing logs (or `py-spy` once on the VM) to `services/intel/builders.py`. The likely cause is the external calls (Statbotics has been 500ing; TBA media and team calls). Fix: cap external calls on the request path at ~1.5 s and return without that section (the frontend already treats `intel.warnings` as notes). The scheduler fills it in later.
2. **`/events/suggested`:** move the TBA `events(year)` fetch and team-count fill into a 15-minute scheduler job (Redis lock pattern already exists). The route then reads only the DB and Redis.
3. **Response store** (`services/response_store.py`): Redis key `resp:v1:{route}:{params}:{event_version}` → gzipped body + ETag + built_at, with an 8-day TTL on every key (Redis runs `noeviction`, so keys without TTL would eventually fill it). `event_version` is a counter bumped by `post_compute_event()`. Bumping makes old keys unreachable at once, so there are no stale races.
4. **Producer:** after each rebuild (`post_compute_event`, regional tick), prerender that event's public payloads by calling the same builder functions the routes use, plus per-team intel with a concurrency limit of 4.
5. **Reader:** a route checks the store first. A hit returns the stored bytes as-is (`Content-Encoding: gzip`, stored ETag). A miss builds, stores, returns (today's path).

**Watch out:**
- Memory budget: ~250 KB gzipped per event × 300 ≈ 75 MB, well inside 2 GB. Log the store size daily.
- Workspace-dependent payloads must not go in the store, or must be keyed by workspace. Start with public routes only.

**Prove it:**
- Equivalence check (memory `frcmob-equivalence-check`): for 2026arc/casac/mndu, prerendered bytes == freshly built bytes, decompressed and parsed.
- Then the slow-request log: zero intel/suggested/history entries over 72 h.

**Size:** steps 1–2 are half a day. Steps 3–5 are 2 days. **Do 1–2 in the first batch.**

## 10. One request per page

**Today:** Team Center: `getTeamIntel`, `getEventTeamsIntel`, `getEventTeamLiveForm`, `getEventSchedule`, `getTeamHeatmap`, `getTeamShiftPlay`, `getTeamRobotImage`, `getTeamLogo`.

**Recommendation:** measure the waterfall before building a bundle endpoint. Over HTTP/2, parallel requests cost about one round trip in total. Only *chained* requests (B waits for A's answer) cost extra. A combined endpoint also breaks cache sharing: the event schedule is used by five pages, and #2/#7 cache it once.

**Build:**
- Record the network waterfall on production (390 px, Slow 4G) for Team Center, Events, Match Center, Strategy. For every chain, start the second request from what the page already knows (route params, the selected event) instead of the first answer.
- If a page still shows a real multi-step waterfall after that, add `GET /pages/team-center?team=&event=` that runs the builders with `asyncio.gather` and returns sections with per-section errors.
- Logos and robot images (one logo took 1.2 s in the log): `public-static` policy (#2) and 1-day browser caching, since the URL is already versioned (`TEAM_MEDIA_CACHE_VERSION`).

**Size:** waterfall audit 2 hours; fixes vary. **Medium value.**

## 11. Data in the first HTML

**Today:** the browser downloads `index.html` → main JS (81 KB br) → React mounts → Home calls `events/suggested` and schedules. The data request waits for the JS.

**The constraint:** HashRouter means the server can't tell `/#/team/254` from `/`, so per-route prerendering and link previews would need a move to BrowserRouter. That touches native shells, shared links (`shareableAppUrl()`) and legacy redirects. Not worth it for speed alone.

**Build (lite, works with HashRouter):** an inline boot script in `index.html` that starts the first data request in parallel with the JS download:

```html
<script>
  if (location.hash === '' || location.hash === '#/' ) {
    window.__frcmobBoot = { 'events/suggested?…': fetch('/api/events/suggested?…', { headers: { Accept: 'application/json' } }) };
  }
</script>
```

`apiFetch` checks `window.__frcmobBoot[requestKey]` once and consumes it. The URL must match exactly what Home asks for, so derive both from one shared constant and add a test that fails if they drift.

**Skip:** an edge function that injects data into `index.html`. It ties you to one host's function runtime, and with #2 in place the boot fetch is already an edge hit.

**Prove it:** DevTools on a throttled first visit. The suggested-events request should start before `main-*.js` finishes, with no duplicate request.

**Size:** 2–3 hours.

## 12. Make ScoutingPage cheaper to re-render

**Today:** 5,217 lines, 33 `useMemo`/`memo` uses. Every counter tap re-renders the page tree on a 250×450 phone.

**Build:**
1. **Measure:** React Profiler (production profiling build) on the Board at 250×450 with 6× CPU throttle and a scripted 30-tap sequence. Record commit time per tap.
2. **React Compiler** on this one file first (`babel-plugin-react-compiler`, pinned, with a `sources` filter for `pages/ScoutingPage.tsx` and `features/workspace/*`). Turn its lint rules back on for those files to see what it bails out on.
3. **Split the Board:** each phase panel and counter group becomes its own component holding its own tap state, lifted to the page only on Save or phase change. Then a tap re-renders one panel.
4. **Web Worker** only for work measured over ~50 ms on a phone: the synergy fallback, Compare/Alliance Advisor scoring, big filters. Plain `postMessage`, no new dependency.

**Watch out:**
- This page had the passive-effect race fixed in #134. The Compiler changes memoization and effect timing. Rerun the room tests 20× under CPU load (the #134 recipe) before merging.
- `scouting.routeSafety.test.tsx` is known to be timing-sensitive.

**Prove it:** commit time per tap before and after (target <16 ms at 6× throttle). RUM INP on `/scouting`.

**Size:** 2–3 days. Biggest feel improvement for scouts on cheap phones.

## 13. Virtualize long lists

**Today:** no virtualization. Phones already page Predictions 20 at a time.

**Recommendation:** do #6 first. `content-visibility` on rows gets most of the benefit with no JS. Virtualize only lists that still render 200+ rows after that.

**Build:**
- Count DOM nodes per route in the sweep script and flag pages over ~3,000 nodes.
- For those, `@tanstack/react-virtual` (pinned) on the scroll container.
- Never on the Picklist (drag-to-rank breaks with virtualization) or on anything the tutorial targets by selector.

**Size:** half a day to measure, then per list.

## 14. Preload before the next match

**Today:** the app knows My Team's schedule, each scout's next assignment, and `matches.predicted_time`. Match alerts already fire on predicted start.

**Build:**
- **App open:** a `useMatchPreload` hook. 15 minutes before My Team's or the scout's next match, it warms the cache for that match's Match Center data and the 6 teams' intel (through the normal getters, so in-flight dedupe applies). Skip when `saveData` is on or on 2g (reuse `shouldPrefetchRoutes`).
- **App closed:** in the SW's existing `push` handler, `event.waitUntil(prefetch(match, teams).then(showNotification))`, so tapping the alert opens straight into saved data. This **needs #7**, because the SW can't read localStorage, only IndexedDB.
- Don't send extra "silent" pushes. Web push must show a notification, so ride on the existing alert.
- Chromium installed PWAs could add Periodic Background Sync as a bonus. iOS doesn't support it.

**Watch out:** iOS gives the push handler little time, so keep the prefetch small (match + 6 intel, ~150 KB gzipped) with a timeout.

**Prove it:** emulate a live event (memory `frcmob-mobile-qa`). Open the alert offline after it fired, and Match Center should render from saved data.

**Size:** 1 day after #7.

## 15. Start loading on finger-down

**Today:** route chunks prefetch on idle (`routePrefetch.ts`). Data starts only after the click and the route render.

**Build:**
- `usePrefetchOnIntent(loader)` returns `onPointerDown`, `onFocus`, and on desktop an `onMouseEnter` with a 60 ms delay.
- Attach it to team rows, match rows and event cards. The loader calls the same getter the destination page uses, so `inflightGetRequests` dedupes and the page joins the in-flight request.
- Also start the route chunk import on the same intent.
- Same `saveData`/2g guard as #14.

A learned "next page" model isn't worth it at this traffic. A static map (row type → loaders) covers it.

**Prove it:** Playwright, tap a team row on Slow 4G. Time from tap to content, before and after.

**Size:** half a day.

## 16. Phone-to-phone at events

The browser can't do true peer-to-peer offline: WebRTC needs a signaling server, which needs internet.

**Build, in order of value:**
1. **QR transfer of scouting entries:** entry JSON → `CompressionStream('deflate')` → base64url → QR (`qrcode`, pinned). One entry is ~300–600 bytes and fits one code. Several entries → a numbered sequence ("2/5") the receiver collects. Scanning: `BarcodeDetector` where available (Chrome Android), with `zxing-wasm` (pinned) as the fallback for iOS. Imports go through the normal local save + team-room sync, deduped by entry id (re-saves already reuse the id).
2. **Event pack as a file:** "Share event pack" exports the prepared pack (schedules, ratings, predictions, team pages) as one compressed file through the OS share sheet (`@capacitor/share` is already a dependency; Web Share on the web). "Open event pack" imports it into the cache. AirDrop/Nearby Share do the transfer, with no internet needed.
3. True local mesh (Nearby Connections / MultipeerConnectivity plugins): skip. It's a large effort for the native shells only.

**Watch out:** imported packs must be validated (schema plus event key) and labelled as saved data with their age. Never import workspace tokens.

**Size:** QR 2 days, pack file 1 day.

## 17. Recorder on the phone's AI chip

**Today:** v4_s on WebGPU (35 s per match on an M2), v4_n on WASM. iPhones run WASM in WKWebView.

**Build:**
1. **Cheapest experiment first:** Safari 26 / iOS 26 ships WebGPU, so ORT-web's WebGPU path may now run on iPhone. Run the recorder benchmark on a physical iPhone. The simulator uses the Mac GPU, so it doesn't count. **Hardware-gated** (physical phones deferred).
2. **Native detection plugin** (Capacitor):
   - iOS: `ultralytics export format=coreml nms=True` for v4_s.
   - Android: `format=tflite` with the GPU delegate.
   - Don't stream frames from the WebView to native (base64 per frame would eat the gain). Pass the **video file path** instead. Native decodes (AVAssetReader / MediaCodec) at 3 fps, runs the model, and returns all detections as JSON. The TS pipeline (tracking, stitching, identify) stays shared and unchanged.
3. WebNN (ORT-web `webnn` provider) isn't shippable yet. Revisit when Chrome Android ships it by default.

**Watch out:** converted models must pass the **locked holdout** again (`scripts/verify_holdout.py`). fp16 CoreML is usually fine; int8 lost accuracy before (0.525 vs 0.606).

**Size:** experiment 1 hour with a phone; native plugin 3–5 days.

## 18. Send less JSON

**Today:** ratings send 14.6 KB per team (864 KB per event), including `evidence`, `details`, `pros`, `cons` and `subscores` for all 75 teams. Team history is 1.1 MB. Gzip shrinks the transfer 13× (field names compress well), so columnar or msgpack formats buy little. The real cost is on the phone: parsing 1 MB, memory, and the 450 KB save cap.

**Build:**
- Grep which fields each consumer reads. Event-level lists likely need rating, confidence, robot/driver level and maybe subscores. Evidence, details, pros and cons are Team Center detail.
- Add `?view=summary` to ratings and team history (default stays full for compatibility), and switch list consumers to it. Team Center keeps full data for one team, or gets `/ratings/{team}`.
- Team history: check whether `history_limit` and per-match fields can shrink for the Events table view.

**Prove it:** byte counts before and after. Screenshot parity on Events, Strategy, Alliance Advisor, Compare. Parse time in a Performance profile at 4× throttle.

**Size:** 1 day. Likely 80–90% smaller list payloads.

## 19. Python 3.13

**Today:** `python:3.11-slim`. numpy is pinned at 2.1.1, and numpy 2.5 is blocked on 3.12 (ignored in `dependabot.yml`).

**Build:**
- Bump both Dockerfiles and the CI matrix to 3.13 and re-resolve the lockfile (torch 2.14, psycopg binary and numpy 2.1+ all ship 3.13 wheels). Then un-ignore numpy in Dependabot.
- Full backend suite with Postgres + Redis.
- **Equivalence check:** rebuild ratings for 3 events on old vs new, expecting 0 differences.
- Watched deploy.
- Keep it **separate from the SQLAlchemy 2.1 PR (#123)**: one risky backend change per deploy.

**Honest value:** maybe 5–15% faster pure-Python CPU (rating rebuilds, regional tick). Request latency barely moves, since it's DB/external-bound.

**Size:** half a day plus the watched deploy.

## 20. Delete requests

**Build:**
- A Playwright inventory script: for each of the 26 routes at 390 px, first load, it records every `/api` request (URL pattern, bytes, duration, start time, initiator) and writes a table.
- Rules to apply:
  - Below-the-fold sections load when scrolled into view (IntersectionObserver), e.g. Team Center heatmap and shift-play if they load before their tab opens.
  - Tabbed data loads only when its tab opens (Events awards, alliances).
  - Anything fetched and never rendered is removed. Start by checking why Events calls `getApiHealth()`.
  - Home has 4 `getEventSchedule` call sites; confirm they dedupe.

**Size:** inventory 2–3 hours; fixes vary. Pairs with #10's waterfall audit (same script).

---

## Suggested order

| Batch | Items | Why |
|---|---|---|
| 1 (this week) | **#1**, **#9 steps 1–2**, **#20 + #10 audit**, **#11** | Measure, then remove the measured multi-second cold paths. All low risk. |
| 2 | **#2** (+ #8 TTLs), **#3**, **#18** | Edge cache and smaller payloads: fast everywhere and less phone work. #2 needs the privacy guards done carefully. |
| 3 | **#7**, then **#14**, **#15**, **#9 steps 3–5** | Instant repeat visits. #14 depends on #7's IndexedDB. |
| 4 | **#5**, **#6**, **#12**, **#13** | Phone rendering. Let RUM INP data from batch 1 pick what goes first. |
| Opportunistic | #4, #19 | Small gains. Do them when touching that code. |
| Hardware / big | #16, #17 | Real but large. #17 waits on a physical iPhone. |

**Needs your approval before doing:**
- Turning on edge caching at the current host (outward config change).
- The telemetry endpoint and the privacy-page line.

---

## Staying host-agnostic (Vercel will probably be replaced)

Rule for every item above: **behavior lives in the backend and the app; the host only serves files and forwards `/api`.**

- **Caching rules are set by the backend** (#2), with standard headers (`CDN-Cache-Control`, `Cache-Control`, ETags). A new CDN honors them without code changes. The header name is one setting.
- **No host APIs.** No purge tokens, no edge functions, no host-specific image or blob services. The only Vercel-specific line is the caching opt-in in `vercel.json`.
- **#9 (prebuilt responses in Redis) and #7 (saved copy on the phone) don't care about the host at all**, and they carry most of the speed win. If the edge cache disappeared tomorrow, they still hold.
- **Best long-term shape, once there's a domain:** put the API on its own name (`api.<domain>`) behind a CDN (Cloudflare's free plan is the obvious one), and point the app straight at it instead of through the frontend host's `/api` proxy. Then API caching is independent of whoever hosts the static files, and swapping the frontend host is a DNS change. Costs to plan for:
  - A cross-origin API means CORS preflights for the custom headers (`X-Workspace-Access`, `X-Admin-Session`). Answer `OPTIONS` with `Access-Control-Max-Age: 86400` so a phone pays that once a day.
  - `CORS_ALLOW_ORIGINS` must list the new frontend origin.
  - sslip.io names can't sit behind Cloudflare's proxy, so this waits for a real domain (already needed for the accounts plan's email sender).

**Host move checklist** (what `vercel.json` does today, which any replacement must do):
1. SPA fallback: every path → `index.html`.
2. `/api/*` → `https://<backend>/*` proxy, keeping request headers, or the direct `api.<domain>` setup above.
3. `/record.html`: `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp` (the recorder's multi-threaded WASM fails silently without them).
4. `/assets/*`: `Cache-Control: public, max-age=31536000, immutable` **and** `Cross-Origin-Embedder-Policy: require-corp` (a worker started from the isolated page hangs without it).
5. `/fonts/*` 30 days, `/models/*` 7 days with `Content-Type: application/octet-stream`.
6. Build-time check that the ONNX models are present (`ONDEVICE_MODEL_REQUIRED` keys off `VERCEL` today; switch it to a host-neutral env var).
7. Turn on API caching for `/api` at the new host and re-run #2's MISS/HIT and workspace-header checks.
8. Native shells point at the public site URL (`shareableAppUrl()`), so check that after a domain change.
