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

    def test_k6_nanosecond_timestamp_is_supported_on_python_3_10(self):
        timestamp = report.parse_timestamp('2026-09-12T05:04:41.775263847Z')
        self.assertEqual(timestamp, report.parse_timestamp('2026-09-12T05:04:41.775263Z'))
        self.assertEqual(
            report.parse_timestamp('2026-09-12T05:04:41.93849Z'),
            report.parse_timestamp('2026-09-12T05:04:41.938490Z'),
        )
        self.assertEqual(
            report.parse_timestamp('2026-09-12T05:04:41Z'),
            report.parse_timestamp('2026-09-12T05:04:41.000000Z'),
        )

    def test_resource_summary_uses_measure_window_and_redacts_container_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rows = [
                {'elapsed_s': 0, 'unix_s': 90, 'containers': [{'Name': 'oidc-load-lab-keycloak-1', 'CPUPerc': '199%', 'MemPerc': '90%', 'MemUsage': '2GiB / 3GiB'}], 'db': {'connections': 20, 'active': 10, 'waiting': 9}},
                {'elapsed_s': 10, 'unix_s': 100, 'containers': [{'Name': 'oidc-load-lab-keycloak-1', 'CPUPerc': '50%', 'MemPerc': '10%', 'MemUsage': '300MiB / 3GiB'}, {'Name': 'oidc-load-lab-k6-run-random', 'CPUPerc': '20%', 'MemPerc': '5%', 'MemUsage': '100MiB / 2GiB'}], 'db': {'connections': 12, 'active': 2, 'waiting': 0, 'commits': 10, 'rollbacks': 1, 'blocks_read': 2, 'blocks_hit': 20, 'temp_bytes': 0, 'deadlocks': 0}},
                {'elapsed_s': 20, 'unix_s': 110, 'containers': [{'Name': 'oidc-load-lab-keycloak-1', 'CPUPerc': '70%', 'MemPerc': '12%', 'MemUsage': '400MiB / 3GiB'}], 'db': {'connections': 13, 'active': 3, 'waiting': 1, 'commits': 20, 'rollbacks': 1, 'blocks_read': 5, 'blocks_hit': 50, 'temp_bytes': 0, 'deadlocks': 0}},
            ]
            (folder / 'host.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
            summary = report.resource_summary(folder, 100, 20)
            self.assertEqual(summary['containers']['keycloak']['cpu_pct_peak'], 70)
            self.assertEqual(summary['containers']['k6']['memory_bytes_peak'], 100 * 1024 ** 2)
            self.assertEqual(summary['database']['waiting_peak'], 1)
            self.assertEqual(summary['database']['commits_delta'], 10)
            self.assertNotIn('random', json.dumps(summary))


if __name__ == '__main__':
    unittest.main()
