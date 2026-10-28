import unittest
from unittest import mock

from app.api import routes_matches
from app.api.routes_matches import _normalized_alliance_row


class AllianceNormalizationTests(unittest.TestCase):
    def test_normalizes_tba_picks_and_backup(self):
        row = {
            "name": "Alliance 1",
            "picks": ["frc118", "frc148", "frc1477"],
            "backup": {"in": "frc3310", "out": "frc148"},
        }
        normalized = _normalized_alliance_row(row, 1)

        self.assertEqual(normalized["number"], 1)
        self.assertEqual(normalized["captain"]["teamNumber"], 118)
        self.assertEqual(normalized["round1"]["teamNumber"], 148)
        self.assertEqual(normalized["round2"]["teamNumber"], 1477)
        self.assertEqual(normalized["backup"]["teamNumber"], 3310)

    def test_normalizes_scalar_and_object_slot_variants(self):
        row = {
            "number": 2,
            "captain": 1678,
            "round1": "frc254",
            "round2": {"teamNumber": 1323, "teamName": "MadTown Robotics"},
            "alternate": "frc971",
        }
        normalized = _normalized_alliance_row(row, 2)

        self.assertEqual(normalized["number"], 2)
        self.assertEqual(normalized["captain"]["teamNumber"], 1678)
        self.assertEqual(normalized["round1"]["teamNumber"], 254)
        self.assertEqual(normalized["round2"]["teamNumber"], 1323)
        self.assertEqual(normalized["round2"]["name"], "MadTown Robotics")
        self.assertEqual(normalized["backup"]["teamNumber"], 971)


class OffSeasonAllianceTests(unittest.TestCase):
    # Off-season events before alliance selection: TBA says null, FRC Events doesn't know them.
    def _call(self, tba_payload):
        tba = mock.Mock()
        tba.event_alliances.return_value = tba_payload
        first = mock.Mock()
        first.event_alliances.side_effect = RuntimeError("FIRST API returned 404 for /2026/alliances/CTTD")
        with mock.patch.object(routes_matches, "TBAClient", return_value=tba), \
                mock.patch.object(routes_matches, "FIRSTEventsClient", return_value=first), \
                mock.patch.object(routes_matches.settings, "tba_auth_key", "k"), \
                mock.patch.object(routes_matches.settings, "first_frc_api_username", "u"), \
                mock.patch.object(routes_matches.settings, "first_frc_api_auth_key", "k"):
            return routes_matches.get_event_alliances("2026cttd", if_modified_since=None, only_modified_since=None)

    def test_no_alliances_yet_is_an_empty_answer_not_a_502(self):
        for payload in (None, []):
            result = self._call(payload)
            self.assertEqual(result["source"], "tba")
            self.assertEqual(result["alliances"], [])


if __name__ == "__main__":
    unittest.main()
