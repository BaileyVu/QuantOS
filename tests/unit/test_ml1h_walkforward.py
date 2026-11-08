from datetime import date
import math
import unittest

from quantos.paper.research.ml1h_walkforward import (
    LIGHTGBM_PARAMS, quarterly_folds, walk_forward_scores,
)

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def ms(day):
    return (day.toordinal() - date(1970, 1, 1).toordinal()) * DAY_MS


def quarter_data():
    first_q = 2021 * 4 + 3  # 2021Q4
    cutoff = ms(date(2023, 7, 1))
    times, ends, long_y, short_y, baseline, augmented = [], [], [], [], [], []
    index_of = {}
    for q in range(first_q, 2023 * 4 + 3):  # through 2023Q3; Q3 is excluded by cutoff
        year, q0 = divmod(q, 4)
        start = ms(date(year, q0 * 3 + 1, 1))
        quarter_indexes = []
        for offset in (10, 40, 80):
            at = start + offset * DAY_MS
            idx = len(times)
            times.append(at)
            ends.append(at + 2 * HOUR_MS)
            long_y.append(idx % 2)
            short_y.append(1 - (idx % 2))
            baseline.append([float(idx % 10)])
            augmented.append([float(idx % 10), float((idx + 3) % 7)])
            quarter_indexes.append(idx)
        index_of[q] = quarter_indexes
    # A label reaching the calibration boundary is purged from fitting.
    fit_boundary_row = index_of[first_q + 2][-1]
    ends[fit_boundary_row] = ms(date(2022, 7, 1))
    # A calibration label reaching test start is excluded from calibration.
    cal_boundary_row = index_of[first_q + 3][0]
    ends[cal_boundary_row] = ms(date(2022, 10, 1))
    # One unavailable augmented feature removes that origin from both arms.
    missing_augmented_row = index_of[first_q + 4][0]
    augmented[missing_augmented_row][0] = math.nan
    # Exercise missing/unfilled labels without changing the fold boundaries.
    long_y[index_of[first_q][0]] = math.nan
    return (times, ends, {"LONG": long_y, "SHORT": short_y},
            {"baseline": baseline, "augmented": augmented}, cutoff,
            fit_boundary_row, cal_boundary_row, missing_augmented_row)


class FakeClassifier:
    def fit(self, features, labels):
        self.fit_count = len(features)
        self.classes = set(labels)

    def predict_proba(self, features):
        output = []
        for row in features:
            score = 0.1 + 0.08 * (float(row[0]) % 10)
            output.append([1.0 - score, score])
        return output


class Ml1hWalkForwardTests(unittest.TestCase):
    def test_quarters_use_four_elapsed_quarters_and_strict_label_end_purge(self):
        times, ends, _targets, _matrices, cutoff, fit_purged, cal_purged, _missing = quarter_data()
        folds = quarterly_folds(times, ends, dataset_start_ms=ms(date(2021, 10, 1)),
                                build_end_exclusive_ms=cutoff)
        self.assertEqual([fold.quarter for fold in folds], ["2022Q4", "2023Q1", "2023Q2"])
        first = folds[0]
        self.assertEqual(first.calibration_quarter, "2022Q3")
        self.assertNotIn(fit_purged, first.fit_indices)
        self.assertNotIn(cal_purged, first.calibration_indices)
        self.assertTrue(all(ends[i] < ms(date(2022, 7, 1)) for i in first.fit_indices))
        self.assertTrue(all(ends[i] < first.test_start_ms for i in first.calibration_indices))
        self.assertTrue(all(times[i] < cutoff for fold in folds for i in fold.test_indices))

    def test_scores_and_calibration_are_paired_and_test_targets_are_unused(self):
        times, ends, targets, matrices, cutoff, _fit_purged, _cal_purged, missing = quarter_data()
        made = []

        def factory():
            model = FakeClassifier()
            made.append(model)
            return model

        args = dict(dataset_start_ms=ms(date(2021, 10, 1)), build_end_exclusive_ms=cutoff,
                    model_factory=factory, quarters={"2022Q4"})
        first = walk_forward_scores(matrices, times, targets, ends, **args)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(made), 4)  # 2 arms x 2 directions x one fold
        for output in first:
            self.assertEqual(set(output.scores), {
                "baseline:LONG", "baseline:SHORT", "augmented:LONG", "augmented:SHORT"})
            self.assertEqual(set(output.thresholds), set(output.scores))
            self.assertEqual(set(map(len, output.scores.values())), {len(output.fold.test_indices)})
            self.assertNotIn(missing, output.fold.test_indices)
            for side_scores in output.scores.values():
                self.assertTrue(all(0.1 <= score <= 0.82 for score in side_scores))

        changed = {direction: list(values) for direction, values in targets.items()}
        for output in first:
            for i in output.fold.test_indices:
                changed["LONG"][i] = math.nan
                changed["SHORT"][i] = math.nan
        second = walk_forward_scores(matrices, times, changed, ends, **args)
        self.assertEqual([fold.scores for fold in first], [fold.scores for fold in second])
        self.assertEqual([fold.thresholds for fold in first], [fold.thresholds for fold in second])

    def test_no_constant_calibration_class_fallback(self):
        times, ends, targets, matrices, cutoff, *_ = quarter_data()
        folds = quarterly_folds(times, ends, dataset_start_ms=ms(date(2021, 10, 1)),
                                build_end_exclusive_ms=cutoff)
        for i in folds[0].calibration_indices:
            targets["LONG"][i] = 1
        with self.assertRaisesRegex(ValueError, "both classes"):
            walk_forward_scores(matrices, times, targets, ends,
                                dataset_start_ms=ms(date(2021, 10, 1)),
                                build_end_exclusive_ms=cutoff,
                                model_factory=FakeClassifier)

    def test_completed_fold_callback_and_quarter_filter_support_resume(self):
        times, ends, targets, matrices, cutoff, *_ = quarter_data()
        completed = []
        result = walk_forward_scores(
            matrices, times, targets, ends,
            dataset_start_ms=ms(date(2021, 10, 1)),
            build_end_exclusive_ms=cutoff,
            model_factory=FakeClassifier,
            quarters={"2022Q4"}, fold_callback=completed.append)
        self.assertEqual([item.fold.quarter for item in result], ["2022Q4"])
        self.assertEqual(completed, list(result))
        resumed = walk_forward_scores(
            matrices, times, targets, ends,
            dataset_start_ms=ms(date(2021, 10, 1)),
            build_end_exclusive_ms=cutoff,
            model_factory=FakeClassifier,
            quarters={"2023Q1"})
        self.assertEqual([item.fold.quarter for item in resumed], ["2023Q1"])
        self.assertEqual(dict(LIGHTGBM_PARAMS), {
            "n_estimators": 200, "num_leaves": 31, "learning_rate": 0.05,
            "min_child_samples": 500, "subsample": 0.7, "subsample_freq": 1,
            "colsample_bytree": 0.8, "n_jobs": 2, "random_state": 17,
            "verbosity": -1,
        })


if __name__ == "__main__":
    unittest.main()
