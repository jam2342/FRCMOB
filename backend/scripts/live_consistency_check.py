#!/usr/bin/env python3
# While events are in play, compares what FRCMOB serves with TBA and FRC Events and logs
# anything that stays wrong past a grace period: matches missing from the schedule (the
# bug that hid NC's finals on 2026-10-18), results stuck behind TBA, results FRC Events
# has that we still don't, score or lineup mismatches, unplayed matches without a
# prediction, failing or slow endpoints.
#
# Standard library only, so the VM's own python runs it from cron, outside the app:
#   */2 * * * * python3 ~/frcmob/backend/scripts/live_consistency_check.py \
#       --env ~/frcmob/.env --dir ~/frcmob-checks >> ~/frcmob-checks/cron.log 2>&1
# Read the day:   python3 .../live_consistency_check.py --dir ~/frcmob-checks --summary
# Does nothing (one TBA call) when no event is in play. Read-only everywhere.
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# How long a problem may last before it's reported. Caches explain shorter ones: live
# results 5 s + the TBA client's 15 s, the schedule top-up runs at most every 3 min.
GRACE_SEC = {
    "endpoint_error": 120,
    "missing_match": 300,
    "result_lag": 90,
    "frc_ahead": 90,
    "score_mismatch": 90,
    "lineup_mismatch": 300,
    "missing_prediction": 300,
}
SLOW_SEC = 3.0
# In play: a match that started (or is due) between 2 h ago and 1 h from now.
IN_PLAY_BEFORE_SEC = 2 * 3600
IN_PLAY_AHEAD_SEC = 3600
USER_AGENT = "frcmob-live-check"


def match_time(m: dict) -> int | None:
    for field in ("actual_time", "predicted_time", "time", "scheduled_time"):
        value = m.get(field)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


def tba_completed(m: dict) -> bool:
    red = ((m.get("alliances") or {}).get("red") or {}).get("score")
    return isinstance(red, int) and red >= 0


def tba_teams(m: dict, color: str) -> list[str]:
    return sorted(str(k).lower() for k in (((m.get("alliances") or {}).get(color) or {}).get("team_keys") or []))


def tba_lineup_known(m: dict) -> bool:
    # TBA lists later playoff rounds before their alliances are decided (2026cacc's
    # finals were up 3 h early on 2026-10-25); nothing can be predicted until it fills them.
    return all(teams and "frc0" not in teams for teams in (tba_teams(m, "red"), tba_teams(m, "blue")))


def prod_teams(m: dict, color: str) -> list[str]:
    return sorted(str(t.get("team_key") or "").lower() for t in (m.get(color) or []) if t.get("team_key"))


def event_in_play(tba_matches: list[dict], now: float) -> bool:
    return any(
        (t := match_time(m)) is not None and now - IN_PLAY_BEFORE_SEC <= t <= now + IN_PLAY_AHEAD_SEC
        for m in tba_matches
    )


def compare_event(
    event_key: str,
    tba_matches: list[dict],
    prod_schedule: dict | None,
    prod_predictions: dict | None,
    frc_played: dict[str, int] | None,
) -> list[dict]:
    # One observation per problem seen right now; the grace period is applied later.
    found: list[dict] = []

    def add(kind: str, match: str | None, detail: str) -> None:
        found.append({"event": event_key, "kind": kind, "match": match, "detail": detail})

    prod = {str(m.get("match_key") or "").lower(): m for m in ((prod_schedule or {}).get("matches") or [])}
    if prod_schedule is not None:
        for t in tba_matches:
            key = str(t.get("key") or "").lower()
            p = prod.get(key)
            if p is None:
                add("missing_match", key, "on TBA, not on our schedule")
                continue
            if tba_completed(t):
                if not p.get("is_completed"):
                    add("result_lag", key, "TBA has the result, we don't")
                else:
                    ours = (p.get("red_score"), p.get("blue_score"))
                    theirs = (t["alliances"]["red"]["score"], t["alliances"]["blue"]["score"])
                    if ours != theirs:
                        add("score_mismatch", key, f"ours {ours[0]}-{ours[1]}, TBA {theirs[0]}-{theirs[1]}")
                for color in ("red", "blue"):
                    if tba_teams(t, color) != prod_teams(p, color):
                        add("lineup_mismatch", key, f"{color}: ours {prod_teams(p, color)}, TBA {tba_teams(t, color)}")
                        break

    if prod_predictions is not None:
        predicted = {
            str(m.get("match_key") or "").lower(): bool((m.get("prediction") or {}).get("available"))
            for m in (prod_predictions.get("matches") or [])
        }
        for t in tba_matches:
            key = str(t.get("key") or "").lower()
            if not tba_completed(t) and tba_lineup_known(t) and not predicted.get(key):
                add("missing_prediction", key, "unplayed match without a prediction")

    if frc_played is not None and prod_schedule is not None:
        # FRC Events numbers playoffs differently from TBA, so compare counts per level:
        # more played matches on FRC Events than finished ones here means a result is stuck.
        done_here = {"Qualification": 0, "Playoff": 0}
        for p in prod.values():
            if p.get("is_completed"):
                done_here["Qualification" if p.get("comp_level") == "qm" else "Playoff"] += 1
        for level, played in frc_played.items():
            if played > done_here.get(level, 0):
                add("frc_ahead", level, f"FRC Events has {played} played, we show {done_here.get(level, 0)}")
    return found


def advance(state: dict, observations: list[dict], now: float) -> tuple[list[dict], list[dict]]:
    # Problems seen for the first time start a clock; ones past their grace are reported
    # once; ones no longer seen are resolved. Returns (newly reported, resolved).
    open_issues: dict = state.setdefault("open", {})
    seen_now = set()
    reported = []
    for obs in observations:
        ident = f"{obs['event']}|{obs['kind']}|{obs['match']}"
        seen_now.add(ident)
        issue = open_issues.setdefault(ident, {**obs, "first_seen": now, "reported": False})
        issue["detail"] = obs["detail"]
        if not issue["reported"] and now - issue["first_seen"] >= GRACE_SEC.get(obs["kind"], 0):
            issue["reported"] = True
            reported.append({**issue, "age_sec": round(now - issue["first_seen"])})
    resolved = []
    for ident in [i for i in open_issues if i not in seen_now]:
        issue = open_issues.pop(ident)
        if issue["reported"]:
            resolved.append({**issue, "lasted_sec": round(now - issue["first_seen"])})
    return reported, resolved


class Http:
    def __init__(self, tba_key: str, frc_user: str, frc_key: str, frc_base: str, state: dict):
        self.tba_key = tba_key
        self.frc_auth = "Basic " + base64.b64encode(f"{frc_user}:{frc_key}".encode()).decode() if frc_user and frc_key else None
        self.frc_base = frc_base.rstrip("/")
        self.cache = state.setdefault("http_cache", {})  # url -> {etag/last_modified, body}

    def get(self, url: str, headers: dict | None = None, conditional: bool = False) -> tuple[int, object, float]:
        headers = {"User-Agent": USER_AGENT, **(headers or {})}
        cached = self.cache.get(url) if conditional else None
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        started = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                body = json.load(r)
                if conditional:
                    self.cache[url] = {"etag": r.headers.get("ETag"), "last_modified": r.headers.get("Last-Modified"), "body": body}
                return r.status, body, time.time() - started
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached:
                return 200, cached["body"], time.time() - started
            return exc.code, None, time.time() - started
        except Exception:  # noqa: BLE001 - timeouts and resets count as a failed check
            return 0, None, time.time() - started

    def tba(self, path: str):
        return self.get(f"https://www.thebluealliance.com/api/v3{path}", {"X-TBA-Auth-Key": self.tba_key}, conditional=True)

    def frc(self, path: str):
        if not self.frc_auth:
            return 0, None, 0.0
        return self.get(f"{self.frc_base}{path}", {"Authorization": self.frc_auth}, conditional=True)


def run_once(http: Http, api: str, state: dict, now: float, only_events: list[str] | None) -> dict:
    year = datetime.fromtimestamp(now, timezone.utc).year
    today = datetime.fromtimestamp(now, timezone.utc).date()
    if only_events:
        candidates = only_events
    else:
        _, events, _ = http.tba(f"/events/{year}/simple")
        candidates = []
        for e in events or []:
            try:
                start = datetime.fromisoformat(e["start_date"]).date()
                end = datetime.fromisoformat(e["end_date"]).date()
            except (KeyError, ValueError, TypeError):
                continue
            # A day either side: an evening in Hawaii is already tomorrow in UTC.
            if (start - today).days <= 1 and (today - end).days <= 1:
                candidates.append(e["key"])

    observations: list[dict] = []
    checked = []
    for key in candidates:
        status, tba_matches, _ = http.tba(f"/event/{key}/matches")
        if status != 200 or not isinstance(tba_matches, list):
            continue
        if not only_events and not event_in_play(tba_matches, now):
            continue
        checked.append(key)
        responses = {}
        for name, path in (("schedule", f"/matches/event/{key}/schedule"),
                           ("predictions", f"/events/{key}/schedule-with-synergy"),
                           ("rankings", f"/events/{key}/rankings")):
            status, body, took = http.get(f"{api}{path}")
            responses[name] = body if status == 200 else None
            if status != 200:
                observations.append({"event": key, "kind": "endpoint_error", "match": name, "detail": f"HTTP {status} {path}"})
            elif took > SLOW_SEC:
                # Logged when seen, not as an issue: one slow answer isn't a problem to chase.
                state.setdefault("slow", []).append({"t": round(now), "event": key, "path": path, "sec": round(took, 1)})
        frc_played = None
        _, event, _ = http.tba(f"/event/{key}")
        code = (event or {}).get("first_event_code") if isinstance(event, dict) else None
        if code:
            frc_played = {}
            for level in ("Qualification", "Playoff"):
                status, body, _ = http.frc(f"/{year}/matches/{str(code).upper()}?tournamentLevel={level}")
                if status != 200 or not isinstance(body, dict):
                    frc_played = None
                    break
                frc_played[level] = sum(
                    1 for m in body.get("Matches") or [] if isinstance(m.get("scoreRedFinal"), int)
                )
        observations += compare_event(key, tba_matches, responses["schedule"], responses["predictions"], frc_played)

    reported, resolved = advance(state, observations, now)
    state["slow"] = state.get("slow", [])[-200:]
    return {"t": round(now), "checked": checked, "open": len(state["open"]), "reported": reported, "resolved": resolved}


def read_env(path: str | None) -> dict[str, str]:
    values = dict(os.environ)
    if path and Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, _, value = line.partition("=")
                values.setdefault(name.strip(), value.strip().strip('"').strip("'"))
    return values


def summary(directory: Path, day: str) -> int:
    log = directory / f"live-{day}.jsonl"
    if not log.exists():
        print(f"No checks logged for {day}.")
        return 0
    runs = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    checked = sorted({e for r in runs for e in r.get("checked", [])})
    reported = [i for r in runs for i in r.get("reported", [])]
    resolved = {f"{i['event']}|{i['kind']}|{i['match']}": i for r in runs for i in r.get("resolved", [])}
    print(f"{day}: {len(runs)} runs, {len(checked)} events checked ({', '.join(checked) or 'none in play'})")
    if not reported:
        print("No problems lasted past their grace period.")
    for issue in reported:
        ident = f"{issue['event']}|{issue['kind']}|{issue['match']}"
        end = resolved.get(ident)
        status = f"resolved after {end['lasted_sec']} s" if end else "still open at last run"
        print(f"- {issue['event']} {issue['kind']} {issue['match']}: {issue['detail']} ({status})")
    return 1 if reported else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(Path.home() / "frcmob-checks"), help="state and daily logs")
    parser.add_argument("--env", help="a .env holding TBA_AUTH_KEY and FIRST_FRC_API_* (not printed)")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="FRCMOB backend base URL")
    parser.add_argument("--events", help="comma-separated event keys to check regardless of schedule")
    parser.add_argument("--summary", nargs="?", const=datetime.now(timezone.utc).strftime("%Y%m%d"), help="print a day's report (YYYYMMDD)")
    args = parser.parse_args()
    directory = Path(args.dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    if args.summary:
        return summary(directory, args.summary)

    env = read_env(args.env)
    if not env.get("TBA_AUTH_KEY"):
        print("TBA_AUTH_KEY is missing (pass --env).", file=sys.stderr)
        return 2
    state_path = directory / "state.json"
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    http = Http(env["TBA_AUTH_KEY"], env.get("FIRST_FRC_API_USERNAME", ""), env.get("FIRST_FRC_API_AUTH_KEY", ""),
                env.get("FIRST_FRC_API_BASE_URL", "https://frc-api.firstinspires.org/v3.0"), state)
    now = time.time()
    result = run_once(http, args.api.rstrip("/"), state, now, args.events.split(",") if args.events else None)
    state_path.write_text(json.dumps(state))
    if result["checked"] or result["reported"] or result["resolved"]:
        with (directory / f"live-{datetime.fromtimestamp(now, timezone.utc):%Y%m%d}.jsonl").open("a") as log:
            log.write(json.dumps(result) + "\n")
    for issue in result["reported"]:
        print(f"PROBLEM {issue['event']} {issue['kind']} {issue['match']}: {issue['detail']}")
    for issue in result["resolved"]:
        print(f"resolved {issue['event']} {issue['kind']} {issue['match']} after {issue['lasted_sec']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
