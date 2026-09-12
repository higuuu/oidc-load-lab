import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('analyze_results', ROOT / 'scripts' / 'analyze_results.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnalyzeResultsTest(unittest.TestCase):
    def test_all_missing_stats_stay_unknown(self):
        self.assertEqual(MODULE.stats([None]), {'median': None, 'min': None, 'max': None})

    def test_missing_wait_observation_does_not_become_zero(self):
        trials = [dict(flows={}, resources={}, db_waiting_peak=v, dropped_all_phases=0) for v in [0, None]]
        self.assertIsNone(MODULE.group_summary(trials)['db_waiting_peak'])

    def test_stats_uses_trial_median_and_range(self):
        self.assertEqual(MODULE.stats([82, 76, 76]), {'median': 76.0, 'min': 76.0, 'max': 82.0})

    def test_burst_rate_has_same_shape_each_minute(self):
        self.assertEqual(MODULE.burst_rate(0, 100), 100)
        self.assertEqual(MODULE.burst_rate(15, 100), 200)
        self.assertEqual(MODULE.burst_rate(30, 100), 100)
        self.assertEqual(MODULE.burst_rate(45, 100), 0)
        self.assertEqual(MODULE.burst_rate(60, 100), 100)

    def test_representative_is_median_p99_not_best(self):
        trials = [
            {'run': 'high', 'flows': {'login': {'p99_ms': 86}}},
            {'run': 'low', 'flows': {'login': {'p99_ms': 75}}},
            {'run': 'middle', 'flows': {'login': {'p99_ms': 78}}},
        ]
        self.assertEqual(MODULE.representative(trials)['run'], 'middle')


if __name__ == '__main__':
    unittest.main()
