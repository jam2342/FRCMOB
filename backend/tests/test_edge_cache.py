from __future__ import annotations

import re
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api import edge_cache
from app.core.config import settings

# Every GET route the app serves must be in exactly one of these. A new route that
# isn't fails test_every_get_route_is_classified, so nothing is cached by accident.
PUBLIC_ROUTES = {
    "/events/search", "/events/suggested", "/events/{event_key}/awards", "/events/{event_key}/predictions",
    "/events/{event_key}/rankings", "/events/{event_key}/schedule-with-synergy",
    "/events/{event_key}/pick-values", "/events/{event_key}/forecast",
    "/events/{event_key}/alliance-outlook", "/events/{event_key}/selection-model",
    "/matches/event/{event_key}/alliances", "/matches/event/{event_key}/live-stream",
    "/matches/event/{event_key}/schedule", "/matches/event/{event_key}/team-live-form", "/matches/schedules",
    "/matches/{match_key}/phases", "/scouting/event/{event_key}/ratings", "/scouting/event/{event_key}/teams/history",
    "/scouting/team/{team_key}/breakdown", "/teams/event/{event_key}/intel", "/teams/search",
    "/teams/{team_key}/intel", "/teams/{team_key}/logo", "/teams/{team_key}/robot-image",
    "/teams/{team_key}/rating-history",
    "/tracks/heatmap/{team_key}", "/tracks/shift-play/{team_key}",
}
PRIVATE_ROUTES = {
    "/", "/health", "/health/deep", "/automation/regional/season/{season}/status", "/maintenance/ops/dashboard",
    "/picklists", "/picklists/{picklist_id}", "/pit-scouting", "/push/public-key",
    "/scouting/auto-drafts/{event_key}/{match_key}/{team_key}", "/scouting/insights/coverage",
    "/scouting/insights/entries-export", "/scouting/rooms/team/{event_key}", "/scouting/rooms/{room_key}",
    "/scouting/rooms/{room_key}/entries", "/telemetry/vitals/summary", "/telemetry/csp/summary",
    "/tracks/on-device-sessions", "/workspaces/admin/list", "/workspaces/me",
    "/workspaces/signup-policy",
}
SAMPLES = {"event_key": "2026arc", "team_key": "frc254", "match_key": "2026arc_qm1"}


def _get_routes(app: FastAPI):
    def walk(routes, prefix=""):
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                yield from walk(route.original_router.routes, prefix + (getattr(route.include_context, "prefix", "") or ""))
            elif isinstance(route, APIRoute) and "GET" in route.methods:
                yield prefix + route.path
    return set(walk(app.routes))


def _sample_path(template: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: SAMPLES.get(m.group(1), "x1"), template)


class RouteInventoryTests(unittest.TestCase):
    def test_every_get_route_is_classified(self):
        from app.main import app

        routes = _get_routes(app)
        self.assertEqual(routes - PUBLIC_ROUTES - PRIVATE_ROUTES, set(), "classify new GET routes in test_edge_cache.py")
        self.assertEqual(PUBLIC_ROUTES & PRIVATE_ROUTES, set())

    def test_public_routes_get_a_policy_and_private_ones_never_do(self):
        for template in PUBLIC_ROUTES:
            self.assertIsNotNone(edge_cache.policy_for(_sample_path(template), ""), template)
            self.assertIsNotNone(edge_cache.policy_for("/api" + _sample_path(template), ""), template)
        for template in PRIVATE_ROUTES:
            self.assertIsNone(edge_cache.policy_for(_sample_path(template), ""), template)


class PolicyTests(unittest.TestCase):
    def tearDown(self) -> None:
        edge_cache._settled = set()

    def test_refresh_requests_are_never_cached(self):
        self.assertIsNone(edge_cache.policy_for("/matches/event/2026arc/schedule", "refresh=true"))
        self.assertEqual(edge_cache.policy_for("/matches/event/2026arc/schedule", "refresh=false"), edge_cache.POLICIES["live"])

    def test_a_teams_own_recordings_are_never_cached(self):
        self.assertIsNone(edge_cache.policy_for("/tracks/heatmap/frc254", "event_key=2026arc&include_workspace=true"))
        self.assertIsNone(edge_cache.policy_for("/api/tracks/shift-play/frc254", "event_key=2026arc&include_workspace=true"))
        self.assertIsNotNone(edge_cache.policy_for("/tracks/heatmap/frc254", "event_key=2026arc"))

    def test_private_query_spellings_and_duplicate_parameters_are_never_cached(self):
        for value in ("1", "yes", "on", "TRUE", "false", "", "false&include_workspace=true"):
            for path in ("/tracks/heatmap/frc254", "/tracks/shift-play/frc254"):
                self.assertIsNone(edge_cache.policy_for(path, "event_key=2026arc&include_workspace=" + value))

    def test_finished_events_are_kept_longer(self):
        edge_cache._settled = {"2026arc"}
        self.assertEqual(edge_cache.policy_for("/events/2026arc/predictions", ""), edge_cache.SETTLED)
        self.assertEqual(edge_cache.policy_for("/teams/frc254/intel", "event_key=2026arc"), edge_cache.SETTLED)
        self.assertEqual(edge_cache.policy_for("/events/2026casac/predictions", ""), edge_cache.POLICIES["live"])
        self.assertEqual(edge_cache.policy_for("/teams/frc254/logo", "event_key=2026arc"), edge_cache.POLICIES["static"])


class MiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()

        @app.get("/events/{event_key}/rankings")
        def rankings(event_key: str):
            return {"event_key": event_key, "rankings": [1, 2, 3]}

        @app.get("/workspaces/me")
        def me():
            return {"workspace": "secret"}

        @app.get("/matches/event/{event_key}/schedule")
        def schedule(event_key: str):
            return JSONResponse({"missing": True}, status_code=404)

        @app.get("/media/pit_photos/x.jpg")
        def photo():
            return PlainTextResponse("jpeg-bytes")

        app.add_middleware(edge_cache.EdgeCacheMiddleware)
        patcher = mock.patch.object(edge_cache, "_maybe_refresh_settled")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)

    def test_public_data_gets_cdn_rule_and_an_etag_and_revalidates_to_304(self):
        resp = self.client.get("/events/2026arc/rankings")
        self.assertEqual(resp.headers["cache-control"], "no-cache")
        self.assertEqual(resp.headers["cdn-cache-control"], "max-age=5, stale-while-revalidate=15")
        etag = resp.headers["etag"]
        self.assertTrue(etag.startswith('W/"'))
        again = self.client.get("/events/2026arc/rankings", headers={"If-None-Match": etag})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b"")
        self.assertEqual(again.headers["etag"], etag)
        changed = self.client.get("/events/2026casac/rankings", headers={"If-None-Match": etag})
        self.assertEqual(changed.status_code, 200)

    def test_private_routes_and_admin_requests_are_never_stored(self):
        private = self.client.get("/workspaces/me")
        self.assertEqual(private.headers["cache-control"], "private, no-store")
        self.assertNotIn("cdn-cache-control", private.headers)
        admin = self.client.get("/events/2026arc/rankings", headers={"X-Admin-Session": "token"})
        self.assertEqual(admin.headers["cache-control"], "private, no-store")
        self.assertNotIn("cdn-cache-control", admin.headers)
        # security.py also takes an admin session as a Bearer token.
        bearer = self.client.get("/events/2026arc/rankings", headers={"Authorization": "Bearer token"})
        self.assertEqual(bearer.headers["cache-control"], "private, no-store")
        self.assertNotIn("cdn-cache-control", bearer.headers)

    def test_errors_and_non_json_are_left_alone_or_private(self):
        missing = self.client.get("/matches/event/2026arc/schedule")
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn("cdn-cache-control", missing.headers)
        self.assertNotIn("etag", missing.headers)
        photo = self.client.get("/media/pit_photos/x.jpg")
        self.assertEqual(photo.headers["cache-control"], "private, no-store")

    def test_header_name_is_configurable_for_other_hosts(self):
        with mock.patch.object(settings, "edge_cache_header", "Surrogate-Control"):
            resp = self.client.get("/events/2026arc/rankings")
        self.assertIn("surrogate-control", resp.headers)
        self.assertNotIn("cdn-cache-control", resp.headers)

    def test_workspace_and_room_credentials_always_disable_shared_caching(self):
        for header in ("X-Workspace-Access", "X-Room-Access-Token"):
            response = self.client.get("/events/2026arc/rankings", headers={header: "private-credential"})
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            self.assertNotIn("cdn-cache-control", response.headers)


if __name__ == "__main__":
    unittest.main()
