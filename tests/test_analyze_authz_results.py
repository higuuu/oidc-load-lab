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

    def test_authz_trial_derives_completed_only_from_complete_classification(self):
        value = {
            "started": 100,
            "sent": 100,
            "completed": 0,
            "correct_decisions": 100,
            "unexpected_or_timeout": 0,
        }
        original_result = analysis.result_for
        original_manifest = analysis.manifest_for
        try:
            analysis.result_for = lambda _: value
            analysis.manifest_for = lambda _: {}
            trial = analysis.authz_trial(Path("synthetic"))
        finally:
            analysis.result_for = original_result
            analysis.manifest_for = original_manifest
        self.assertEqual(trial["completed"], 100)
        self.assertEqual(
            trial["completed_basis"],
            "derived_from_started_equals_sent_equals_correct_with_no_unexpected",
        )


if __name__ == "__main__":
    unittest.main()
