import unittest

from app.services.quality_gate import evaluate_summary_quality_gate


class QualityGateTests(unittest.TestCase):
    def test_rejects_low_coverage(self):
        accepted, reason, coverage, detections = evaluate_summary_quality_gate(
            {
                "throughput_metrics": {"coverage_score": 0.08},
                "sampling": {"detections": 40},
            },
            min_coverage_score=0.2,
            min_detections=8,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "coverage_score_below_threshold")
        self.assertEqual(coverage, 0.08)
        self.assertEqual(detections, 40)

    def test_rejects_low_detections(self):
        accepted, reason, coverage, detections = evaluate_summary_quality_gate(
            {
                "throughput_metrics": {"coverage_score": 0.45},
                "sampling": {"detections": 4},
            },
            min_coverage_score=0.2,
            min_detections=8,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "detections_below_threshold")
        self.assertEqual(coverage, 0.45)
        self.assertEqual(detections, 4)

    def test_accepts_good_summary(self):
        accepted, reason, coverage, detections = evaluate_summary_quality_gate(
            {
                "throughput_metrics": {"coverage_score": 0.44},
                "sampling": {"detections": 18},
            },
            min_coverage_score=0.2,
            min_detections=8,
        )
        self.assertTrue(accepted)
        self.assertEqual(reason, "accepted")
        self.assertEqual(coverage, 0.44)
        self.assertEqual(detections, 18)

    def test_accepts_when_metrics_absent(self):
        accepted, reason, coverage, detections = evaluate_summary_quality_gate(
            {},
            min_coverage_score=0.2,
            min_detections=8,
        )
        self.assertTrue(accepted)
        self.assertEqual(reason, "accepted")
        self.assertIsNone(coverage)
        self.assertIsNone(detections)


if __name__ == "__main__":
    unittest.main()
