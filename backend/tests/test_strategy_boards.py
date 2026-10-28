"""Strategy boards: a match's plan on the field, private to a workspace, versioned like picklists."""
from app.api.routes_strategy_boards import MAX_STROKES, router as strategy_boards_router
from app.db import models
from app.services.workspace_limits import delete_abandoned_empty_workspaces
from tests.test_picklists_pit_push_insights import _EndpointTestCase
from tests.workspace_helpers import seed_workspace

MATCH = "2026mndu_qm12"


class StrategyBoardTests(_EndpointTestCase):
    routers = (strategy_boards_router,)

    def put(self, version, strokes, headers=None):
        return self.client.put(f"/strategy-boards/{MATCH}", json={"version": version, "strokes": strokes}, headers=headers)

    def test_an_undrawn_board_is_empty_at_version_zero(self):
        body = self.client.get(f"/strategy-boards/{MATCH}").json()
        self.assertEqual(body["board"], {"match_key": MATCH, "strokes": [], "version": 0, "updated_by": None, "updated_at": None})

    def test_draw_save_and_read_back(self):
        saved = self.put(0, [
            {"tool": "pen", "color": "ours", "points": [[0.1, 0.2], [0.15, 0.25], [0.2, 0.3]]},
            {"tool": "arrow", "color": "theirs", "points": [[0.5, 0.5], [0.6, 0.55], [0.7, 0.6]]},
        ])
        self.assertEqual(saved.status_code, 200, saved.text)
        board = saved.json()["board"]
        self.assertEqual(board["version"], 1)
        # An arrow keeps only its two ends.
        self.assertEqual(board["strokes"][1]["points"], [[0.5, 0.5], [0.7, 0.6]])
        self.assertTrue(board["updated_by"])
        self.assertEqual(self.client.get(f"/strategy-boards/{MATCH}").json()["board"]["strokes"], board["strokes"])
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.StrategyBoard).one().event_key, "2026mndu")

    def test_a_stale_version_gets_the_current_board_back(self):
        self.put(0, [{"tool": "zone", "color": "note", "points": [[0.1, 0.1], [0.3, 0.3]]}])
        stale = self.put(0, [])
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.json()["conflict"], True)
        self.assertEqual(len(stale.json()["board"]["strokes"]), 1)
        cleared = self.put(1, [])
        self.assertEqual((cleared.json()["board"]["version"], cleared.json()["board"]["strokes"]), (2, []))

    def test_junk_is_dropped_and_points_are_clamped(self):
        strokes = [
            {"tool": "laser", "color": "ours", "points": [[0, 0], [1, 1]]},
            {"tool": "pen", "color": "#ff0000", "points": [[0, 0]]},
            {"tool": "pen", "color": "ours", "points": [[-3, 0.12345], ["x", 1], [2, "NaN"], [0.5]]},
            "nope",
        ] + [{"tool": "pen", "color": "note", "points": [[0.5, 0.5]]}] * (MAX_STROKES + 10)
        board = self.put(0, strokes).json()["board"]
        self.assertEqual(board["strokes"][0], {"tool": "pen", "color": "ours", "points": [[0.0, 0.123]]})
        self.assertEqual(len(board["strokes"]), MAX_STROKES - 3)

    def test_bad_match_keys_are_refused(self):
        for key in ("2026mndu", "nope_qm1", "2026mndu_qm1;drop"):
            self.assertEqual(self.client.get(f"/strategy-boards/{key}").status_code, 400)

    def test_another_workspace_never_sees_the_plan(self):
        self.put(0, [{"tool": "pen", "color": "ours", "points": [[0.2, 0.2]]}])
        with self.SessionLocal() as db:
            _workspace, _member, other_headers = seed_workspace(db, name="Other team")
        other = self.client.get(f"/strategy-boards/{MATCH}", headers=other_headers).json()["board"]
        self.assertEqual((other["strokes"], other["version"]), ([], 0))

    def test_a_workspace_with_a_plan_is_not_cleaned_up_as_empty(self):
        self.put(0, [{"tool": "pen", "color": "ours", "points": [[0.2, 0.2]]}])
        with self.SessionLocal() as db:
            from datetime import datetime, timedelta, timezone
            old = datetime.now(timezone.utc) - timedelta(days=400)
            for workspace in db.query(models.TeamWorkspace):
                workspace.created_at = old
            for member in db.query(models.TeamWorkspaceMember):
                member.joined_at = old
                member.last_seen_at = None
            db.commit()
            delete_abandoned_empty_workspaces(db)
            self.assertEqual(db.query(models.TeamWorkspace).count(), 1)
