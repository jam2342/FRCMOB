from __future__ import annotations

from app.db import models
from app.services.ml.shadow import holdout_event_keys, split_tag_for_event
from tests.conftest import DBTestCase


class HoldoutSplitTests(DBTestCase):
    def _event(self, key: str, last_match_time: int) -> None:
        self.db.add(models.Event(event_key=key, name=key, year=2026))
        self.db.add(models.Match(
            match_key=f"{key}_qm1", event_key=key, comp_level="qm", set_number=1, match_number=1,
            time=last_match_time,
        ))

    def test_newest_fifth_of_a_single_season_is_held_out(self):
        # All one season: a whole-year holdout would have left nothing to train on.
        keys = {f"2026ev{i:02d}" for i in range(10)}
        for i, key in enumerate(sorted(keys)):
            self._event(key, 1_700_000_000 + i * 86_400)
        self.db.commit()

        holdout = holdout_event_keys(self.db, keys)
        self.assertEqual(holdout, {"2026ev08", "2026ev09"})
        self.assertEqual(split_tag_for_event("2026ev00", holdout), "train")
        self.assertEqual(split_tag_for_event("2026ev09", holdout), "holdout")

    def test_too_few_events_hold_nothing_out(self):
        keys = {"2026a", "2026b"}
        for i, key in enumerate(sorted(keys)):
            self._event(key, 1_700_000_000 + i)
        self.db.commit()
        self.assertEqual(holdout_event_keys(self.db, keys), set())
