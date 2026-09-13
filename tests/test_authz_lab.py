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

    def test_restore_api_helper_forwards_idempotency_key(self):
        captured = {}
        original_request = authz_lab.request
        try:
            def fake_request(url, **kwargs):
                captured.update({"url": url, **kwargs})
                return 403, {"error": "forbidden"}, {}

            authz_lab.request = fake_request
            authz_lab.api_at(
                19444,
                "/albums/1/shares",
                "token",
                method="POST",
                body={"user_id": 100},
                mode="fga",
                idem="00000000-0000-4000-8000-000000000001",
            )
        finally:
            authz_lab.request = original_request
        self.assertEqual(
            captured["headers"]["Idempotency-Key"],
            "00000000-0000-4000-8000-000000000001",
        )


if __name__ == "__main__":
    unittest.main()
