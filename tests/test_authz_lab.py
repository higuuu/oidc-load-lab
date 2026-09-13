import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "authz_lab", Path(__file__).resolve().parents[1] / "scripts/authz_lab.py"
)
authz_lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authz_lab)


class AuthzLabTest(unittest.TestCase):
    def test_k6_nanosecond_timestamp_is_supported(self):
        self.assertEqual(
            authz_lab.parse_sample_time("2026-09-13T14:17:53.67630796Z"),
            authz_lab.parse_sample_time("2026-09-13T14:17:53.676307Z"),
        )
        self.assertEqual(
            authz_lab.parse_sample_time("2026-09-13T14:17:53+00:00"),
            authz_lab.parse_sample_time("2026-09-13T14:17:53.000000Z"),
        )

    def test_invalid_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            authz_lab.parse_sample_time("not-a-timestamp")


if __name__ == "__main__":
    unittest.main()
