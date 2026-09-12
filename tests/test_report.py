import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('report', Path(__file__).resolve().parents[1] / 'scripts/report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTest(unittest.TestCase):
    def test_failed_and_warmup_work_is_not_counted_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'manifest.json').write_text(json.dumps(dict(mode='login', seconds=60, vus=2, login_rate=1, refresh_rate=1, exit_code=99)))
            points = []
            for metric, value, phase in [('lab_attempts', 1, 'warmup'), ('lab_completed', 1, 'warmup'), ('lab_attempts', 1, 'measure'), ('lab_latency_ms', 10, 'measure'), ('lab_completed', 1, 'measure'), ('lab_attempts', 1, 'measure'), ('lab_latency_ms', 10000, 'measure'), ('dropped_iterations', 3, 'measure')]:
                points.append(json.dumps(dict(type='Point', metric=metric, data=dict(value=value, time='2026-01-01T00:00:00Z', tags=dict(flow='login', phase=phase, ignored_sensitive='not-exported')))))
            (folder / 'samples.json').write_text('\n'.join(points))
            result = report.aggregate(folder)
            self.assertEqual(result['flows']['login']['attempts'], 2)
            self.assertEqual(result['flows']['login']['completed'], 1)
            self.assertEqual(result['flows']['login']['success_ratio'], .5)
            self.assertEqual(result['dropped_iterations_all_phases'], 3)
            self.assertNotIn('ignored_sensitive', json.dumps(result))
            self.assertNotIn('2026-01-01', json.dumps(result))
            self.assertEqual(result['timeline'][0]['completed_per_s'], .2)
            self.assertIn('<svg', report.plot(result))


if __name__ == '__main__':
    unittest.main()
