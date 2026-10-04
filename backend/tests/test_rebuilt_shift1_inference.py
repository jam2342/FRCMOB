import unittest

from app.services.scoring import breakdown


class RebuiltHubActivityWindowsTests(unittest.TestCase):
    def test_infers_shift1_active_alliance_from_auto_fuel_count(self):
        payload = {
            "score_breakdown": {
                "red": {"hubScore": {"autoCount": 14}},
                "blue": {"hubScore": {"autoCount": 9}},
            }
        }
        shift1_active_alliance, source = breakdown._infer_rebuilt_shift1_active_alliance(payload)
        self.assertEqual(shift1_active_alliance, "blue")
        self.assertEqual(source, "auto_fuel_count")


if __name__ == "__main__":
    unittest.main()
