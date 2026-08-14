import unittest

from app.services.ml import model_eval


class ModelEvalTests(unittest.TestCase):
    def test_binary_log_loss_and_brier(self):
        targets = [1.0, 0.0, 1.0, 0.0]
        probs = [0.9, 0.1, 0.8, 0.2]
        logloss = model_eval.binary_log_loss(targets, probs)
        brier = model_eval.brier_score(targets, probs)
        self.assertIsNotNone(logloss)
        self.assertIsNotNone(brier)
        assert logloss is not None
        assert brier is not None
        self.assertLess(logloss, 0.3)
        self.assertLess(brier, 0.06)

    def test_mae_and_rmse(self):
        actual = [10.0, 12.0, 14.0]
        predicted = [9.0, 13.0, 15.0]
        mae = model_eval.mean_absolute_error(actual, predicted)
        rmse = model_eval.root_mean_squared_error(actual, predicted)
        self.assertAlmostEqual(mae or 0.0, 1.0, places=6)
        self.assertAlmostEqual(rmse or 0.0, 1.0, places=6)

    def test_spearman_rank_correlation(self):
        corr_same = model_eval.spearman_rank_correlation([1, 2, 3, 4], [10, 20, 30, 40])
        corr_reverse = model_eval.spearman_rank_correlation([1, 2, 3, 4], [40, 30, 20, 10])
        self.assertAlmostEqual(corr_same or 0.0, 1.0, places=6)
        self.assertAlmostEqual(corr_reverse or 0.0, -1.0, places=6)

    def test_time_split_is_ordered(self):
        rows = [
            {"ts": 30, "v": "c"},
            {"ts": 10, "v": "a"},
            {"ts": 20, "v": "b"},
            {"ts": 40, "v": "d"},
            {"ts": 50, "v": "e"},
        ]
        train, val = model_eval.time_split(rows, timestamp_fn=lambda row: row["ts"], train_ratio=0.6)
        self.assertEqual([row["v"] for row in train], ["a", "b", "c"])
        self.assertEqual([row["v"] for row in val], ["d", "e"])


if __name__ == "__main__":
    unittest.main()
