import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "analyze_authz_results",
    Path(__file__).resolve().parents[1] / "scripts/analyze_authz_results.py",
)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class AnalyzeAuthzResultsTest(unittest.TestCase):
    def test_stats_keeps_trial_variability(self):
        self.assertEqual(
            analysis.stats([5, 7, 6]),
            {"median": 6.0, "min": 5.0, "max": 7.0},
        )

    def test_slope_is_reported_in_units_per_second(self):
        self.assertEqual(analysis.slope([(0, 10), (10, 20), (20, 30)]), 1)
        self.assertEqual(analysis.slope([(0, 30), (10, 20), (20, 10)]), -1)

    def test_e6_public_does_not_export_dump_hashes(self):
        result = analysis.e6_public(
            {
                "dump_manifest": {"auth": {"bytes": 10, "sha256": "private-fingerprint"}},
                "restored_e1": {},
            }
        )
        self.assertEqual(result["dump_sizes_bytes"], {"auth": 10})
        self.assertNotIn("sha256", str(result))

    def test_public_correctness_results_keep_safe_git_provenance(self):
        provenance = {"git_head": "a" * 40, "git_dirty": False}
        self.assertEqual(
            {key: analysis.e1_public(provenance)[key] for key in provenance},
            provenance,
        )
        self.assertEqual(
            {key: analysis.e2_public(provenance)[key] for key in provenance},
            provenance,
        )
        self.assertEqual(
            {key: analysis.e6_public(provenance)[key] for key in provenance},
            provenance,
        )


if __name__ == "__main__":
    unittest.main()
