import unittest

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


if __name__ == "__main__":
    unittest.main()
