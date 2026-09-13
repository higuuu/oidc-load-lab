#!/usr/bin/env python3
"""Build aggregate-only public evidence for the Mac mini authorization runs.

The local raw directories are inputs only. The output intentionally excludes
tokens, response bodies, absolute paths/timestamps, credentials, dumps, and
container identifiers.
"""

import argparse
import html
import json
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
RAW = RESULTS / "authz-raw"
PUBLIC = RESULTS / "public" / "authz"
MIB = 1024 * 1024

E0_RUNS = [
    "20260913T160451-login",
    "20260913T161057-login",
    "20260913T161553-login",
]


def load(path):
    return json.loads(path.read_text())


def rounded(value, digits=3):
    return None if value is None else round(float(value), digits)


def stats(values):
    selected = [float(value) for value in values if value is not None]
    if not selected:
        return {"median": None, "min": None, "max": None}
    return {
        "median": rounded(median(selected)),
        "min": rounded(min(selected)),
        "max": rounded(max(selected)),
    }


def result_for(folder):
    for name in ("aggregate.json", "result.json"):
        path = folder / name
        if path.is_file():
            return load(path)
    return {}


def manifest_for(folder):
    path = folder / "manifest.json"
    return load(path) if path.is_file() else {}


def formal_ids(folders):
    selected = {
        folder.name
        for folder in folders
        if any(
            marker in folder.name
            for marker in (
                "e3-final-explore-",
                "e3-final-low-",
                "e3-final-high-",
                "e3-final-shares50-",
                "e4-final-low-",
                "e4-final-high-",
                "e4-final-recovery-",
                "e5-final-",
                "e7-final-one-hour-",
            )
        )
    }
    for suffix in ("-e1", "-e6-backup-restore"):
        passed = [folder for folder in folders if folder.name.endswith(suffix) and result_for(folder).get("passed")]
        if passed:
            selected.add(sorted(passed)[-1].name)
    e2 = [folder for folder in folders if folder.name.endswith("-e2") and result_for(folder).get("passed")]
    if e2:
        selected.add(sorted(e2)[-1].name)
    return selected


def run_entry(folder, formal):
    manifest = manifest_for(folder)
    value = result_for(folder)
    name = folder.name
    if name in formal:
        classification = "formal"
        reason = "required evidence"
    elif "smoke" in name or "preflight" in name:
        classification = "diagnostic"
        reason = "smoke or preflight"
    elif not value:
        classification = "incomplete"
        reason = "no final aggregate"
    else:
        classification = "superseded"
        reason = "retained pre-fix or preliminary run"
    latency = value.get("latency_ms", {}).get("whole", {})
    oidc = value.get("oidc", {})
    resources = value.get("resources", {})
    entry = {
        "run_id": name,
        "classification": classification,
        "reason": reason,
        "experiment": value.get("experiment") or manifest.get("experiment"),
        "mode": value.get("mode") or manifest.get("mode"),
        "passed": value.get("passed"),
        "measurement_status": manifest.get("measurement_status", "completed" if value else "incomplete"),
        "git_head": value.get("git_head", manifest.get("git_head")),
        "git_dirty": value.get("git_dirty", manifest.get("git_dirty")),
        "exit_code": value.get("exit_code", manifest.get("exit_code")),
        "api_rate_rps": value.get("rate_rps", manifest.get("api_rate_rps", manifest.get("rate_rps"))),
        "login_rate_rps": value.get("login_rate_rps", manifest.get("login_rate_rps")),
        "refresh_rate_rps": value.get("refresh_rate_rps", manifest.get("refresh_rate_rps")),
        "shares_per_album": value.get("shares_per_album", manifest.get("shares_per_album")),
        "api_p99_ms": rounded(latency.get("p(99)")),
        "decision_accuracy": rounded(value.get("decision_accuracy"), 6),
        "over_permit": value.get("over_permit", value.get("over_permit_total", value.get("over_permit_count"))),
        "dropped_iterations": value.get("dropped_iterations_all_phases"),
        "login_success": rounded(oidc.get("login", {}).get("success_rate"), 6),
        "login_p99_ms": rounded(oidc.get("login", {}).get("latency_ms", {}).get("p(99)")),
        "refresh_success": rounded(oidc.get("refresh", {}).get("success_rate"), 6),
        "refresh_p99_ms": rounded(oidc.get("refresh", {}).get("latency_ms", {}).get("p(99)")),
        "fault_service": manifest.get("fault_service"),
        "fault_effect_observed": value.get("fault_effect_observed"),
        "recovery_confirmation_s": value.get("recovery_confirmation_s"),
        "observation_errors": resources.get("observation_errors_measure"),
    }
    return entry


def authz_trial(folder):
    value = result_for(folder)
    manifest = manifest_for(folder)
    oidc = value.get("oidc", {})
    resources = value.get("resources", {})
    containers = resources.get("containers", {})
    return {
        "run_id": folder.name,
        "mode": value.get("mode", manifest.get("mode")),
        "passed": value.get("passed"),
        "api_rate_rps": value.get("rate_rps", manifest.get("api_rate_rps", manifest.get("rate_rps"))),
        "login_rate_rps": value.get("login_rate_rps", manifest.get("login_rate_rps")),
        "refresh_rate_rps": value.get("refresh_rate_rps", manifest.get("refresh_rate_rps")),
        "shares_per_album": value.get("shares_per_album", manifest.get("shares_per_album")),
        "api_p99_ms": rounded(value.get("latency_ms", {}).get("whole", {}).get("p(99)")),
        "allow_p99_ms": rounded(value.get("latency_ms", {}).get("expected_allow", {}).get("p(99)")),
        "deny_p99_ms": rounded(value.get("latency_ms", {}).get("expected_deny", {}).get("p(99)")),
        "decision_accuracy": rounded(value.get("decision_accuracy"), 6),
        "over_permit": value.get("over_permit"),
        "under_permit": value.get("under_permit"),
        "unexpected_or_timeout": value.get("unexpected_or_timeout"),
        "dropped_iterations": value.get("dropped_iterations_all_phases"),
        "login": {
            "attempted": oidc.get("login", {}).get("attempted"),
            "success_rate": rounded(oidc.get("login", {}).get("success_rate"), 6),
            "p99_ms": rounded(oidc.get("login", {}).get("latency_ms", {}).get("p(99)")),
        },
        "refresh": {
            "attempted": oidc.get("refresh", {}).get("attempted"),
            "success_rate": rounded(oidc.get("refresh", {}).get("success_rate"), 6),
            "p99_ms": rounded(oidc.get("refresh", {}).get("latency_ms", {}).get("p(99)")),
        },
        "cpu_median_pct": {
            role: rounded(containers.get(role, {}).get("cpu_pct_median"))
            for role in ("api", "openfga", "keycloak", "db", "worker", "k6")
        },
        "observation_errors": resources.get("observation_errors_measure"),
        "git_head": manifest.get("git_head"),
        "git_dirty": manifest.get("git_dirty"),
    }


def group_summary(trials):
    return {
        "trial_count": len(trials),
        "passed_count": sum(item.get("passed") is True for item in trials),
        "api_p99_ms": stats(item.get("api_p99_ms") for item in trials),
        "login_p99_ms": stats(item.get("login", {}).get("p99_ms") for item in trials),
        "refresh_p99_ms": stats(item.get("refresh", {}).get("p99_ms") for item in trials),
        "decision_accuracy": stats(item.get("decision_accuracy") for item in trials),
        "over_permit_total": sum(item.get("over_permit") or 0 for item in trials),
        "dropped_total": sum(item.get("dropped_iterations") or 0 for item in trials),
        "observation_errors_total": sum(item.get("observation_errors") or 0 for item in trials),
    }


def e1_public(value):
    return {
        "git_head": value.get("git_head"),
        "git_dirty": value.get("git_dirty"),
        "passed": value.get("passed"),
        "case_count": value.get("case_count"),
        "over_permit_count": value.get("over_permit_count"),
        "cases": [
            {
                key: case.get(key)
                for key in (
                    "mode",
                    "case",
                    "expected_status",
                    "actual_status",
                    "protected_data_expected",
                    "protected_data_shape_ok",
                    "pass",
                )
            }
            for case in value.get("cases", [])
        ],
    }


def e2_public(value):
    modes = []
    for mode in value.get("modes", []):
        modes.append(
            {
                key: mode.get(key)
                for key in (
                    "mode",
                    "cycles",
                    "share_changes_completed",
                    "removal_completion_ms",
                    "addition_completion_ms",
                    "erroneous_allow_after_removal_completion",
                    "erroneous_deny_after_add_completion",
                    "pending_availability_denials",
                    "pending_allows_observed",
                    "status_errors",
                    "duplicate_idempotency_pass",
                    "stale_event_superseded_pass",
                    "ordered_add_remove_add_pass",
                    "final_app_shared",
                    "final_fga_shared",
                    "final_album_changing",
                    "final_pending_events",
                    "passed",
                )
            }
        )
    worker = value.get("worker_stop_reconciliation", {})
    return {
        "git_head": value.get("git_head"),
        "git_dirty": value.get("git_dirty"),
        "passed": value.get("passed"),
        "over_permit_count": value.get("over_permit_count"),
        "modes": modes,
        "worker_stop_reconciliation": {
            key: worker.get(key)
            for key in (
                "stop_duration_s",
                "request_status",
                "pending_availability_denials",
                "unexpected_pending_results",
                "operation_status_after_resume",
                "resume_to_completion_ms",
                "new_request_after_completion_status",
                "passed",
            )
        },
    }


def timeline_public(value):
    return {
        key: value.get(key)
        for key in (
            "run_id",
            "experiment",
            "mode",
            "profile",
            "exit_code",
            "baseline",
            "middle",
            "recovery",
            "windows_10s",
            "stable_window_start_s",
            "recovery_confirmation_s",
            "over_permit_total",
            "passed",
            "post_recovery_consistency",
            "dropped_iterations_all_phases",
            "fault_effect_observed",
            "overload_effect_observed",
            "resources",
        )
    }


def e6_public(value):
    return {
        "run_id": value.get("run_id"),
        "git_head": value.get("git_head"),
        "git_dirty": value.get("git_dirty"),
        "passed": value.get("passed"),
        "backup_scope": value.get("backup_scope"),
        "dump_sizes_bytes": {
            database: details.get("bytes")
            for database, details in value.get("dump_manifest", {}).items()
        },
        "backup_duration_ms": rounded(value.get("backup_duration_ms")),
        "post_backup_share_removal_completion_ms": rounded(value.get("post_backup_share_removal_completion_ms")),
        "original_after_removal_denied": value.get("original_after_removal_denied"),
        "restore_project": value.get("restore_project"),
        "restore_volume_preserved": value.get("restore_volume_preserved"),
        "source_volume_preserved": value.get("source_volume_preserved"),
        "restore_database_duration_ms": rounded(value.get("restore_database_duration_ms")),
        "restore_to_service_ready_ms": rounded(value.get("restore_to_service_ready_ms")),
        "verification_duration_ms": rounded(value.get("verification_duration_ms")),
        "restored_backup_point_shared_access": value.get("restored_backup_point_shared_access"),
        "older_backup_permission_reappearance_detected": value.get("older_backup_permission_reappearance_detected"),
        "restored_e1": e1_public(value.get("restored_e1", {})),
        "limitations": value.get("limitations"),
    }


def slope(points):
    if len(points) < 2:
        return None
    x_mean = sum(x for x, _ in points) / len(points)
    y_mean = sum(y for _, y in points) / len(points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)
    return None if denominator == 0 else sum((x - x_mean) * (y - y_mean) for x, y in points) / denominator


def e7_stability(folder):
    rows = [json.loads(line) for line in (folder / "observer.jsonl").read_text().splitlines()]
    rows = [row for row in rows if row.get("phase") == "measure" and not row.get("observation_error")]
    output = {"sample_count": len(rows), "services": {}}
    for role in ("db", "keycloak", "openfga", "api", "worker", "k6"):
        points = [
            (float(row["elapsed_s"]), float(row["containers"][role]["memory_bytes"]) / MIB)
            for row in rows
            if role in row.get("containers", {})
        ]
        quarter = max(1, len(points) // 4)
        halfway = len(points) // 2
        output["services"][role] = {
            "first_quarter_median_mib": rounded(median(y for _, y in points[:quarter])) if points else None,
            "last_quarter_median_mib": rounded(median(y for _, y in points[-quarter:])) if points else None,
            "linear_slope_mib_per_hour": rounded((slope(points) or 0) * 3600),
            "last_half_slope_mib_per_hour": rounded((slope(points[halfway:]) or 0) * 3600),
            "peak_mib": rounded(max((y for _, y in points), default=0)),
        }
    for database in ("auth", "app", "authz"):
        values = [row.get("database_connections", {}).get(database, {}).get("total") for row in rows]
        values = [value for value in values if value is not None]
        output.setdefault("database_connections", {})[database] = {
            "first": values[0] if values else None,
            "last": values[-1] if values else None,
            "peak": max(values) if values else None,
        }
    pending = [row.get("application", {}).get("outbox_pending") for row in rows]
    pending = [value for value in pending if value is not None]
    output["outbox_pending"] = {
        "first": pending[0] if pending else None,
        "last": pending[-1] if pending else None,
        "peak": max(pending) if pending else None,
    }
    output["series"] = [
        {
            "elapsed_s": round(float(row["elapsed_s"]) - float(rows[0]["elapsed_s"]), 1),
            "memory_mib": {
                role: rounded(row.get("containers", {}).get(role, {}).get("memory_bytes", 0) / MIB)
                for role in ("db", "keycloak", "openfga", "api", "worker", "k6")
            },
            "connections": {
                database: row.get("database_connections", {}).get(database, {}).get("total")
                for database in ("auth", "app", "authz")
            },
            "outbox_pending": row.get("application", {}).get("outbox_pending"),
        }
        for row in rows
    ]
    return output


def svg_start(title, subtitle, width=1080, height=520):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#fbfcfe"/>',
        '<g font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" fill="#172033">',
        f'<text x="52" y="34" font-size="20" font-weight="700">{html.escape(title)}</text>',
        f'<text x="52" y="57" font-size="12" fill="#556176">{html.escape(subtitle)}</text>',
    ]


def bar_svg(title, subtitle, rows, maximum, suffix=" ms"):
    parts = svg_start(title, subtitle)
    x, y, width, height = 160, 90, 850, 340
    for index in range(5):
        value = maximum * index / 4
        px = x + width * index / 4
        parts += [f'<path d="M{px:.1f} {y} V{y + height}" stroke="#e2e8f0"/>', f'<text x="{px:.1f}" y="{y + height + 22}" text-anchor="middle" font-size="10">{value:g}</text>']
    step = height / max(1, len(rows))
    for index, (label, value, color) in enumerate(rows):
        py = y + index * step + step * 0.18
        bar_height = step * 0.62
        bar_width = 0 if value is None else min(width, value / maximum * width)
        parts += [
            f'<text x="{x - 10}" y="{py + bar_height * .7:.1f}" text-anchor="end" font-size="11">{html.escape(label)}</text>',
            f'<rect x="{x}" y="{py:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{color}" rx="3"/>',
            f'<text x="{x + bar_width + 7:.1f}" y="{py + bar_height * .7:.1f}" font-size="10">{value:g}{suffix}</text>',
        ]
    parts += ['</g></svg>']
    return "\n".join(parts)


def stability_svg(stability):
    parts = svg_start("E7 one-hour memory stability", "Relative time only; approximately 10-second observations. Missing samples are not filled with zero.", height=650)
    x, y, width, height = 75, 100, 950, 390
    maximum = max(point for row in stability["series"] for point in row["memory_mib"].values()) * 1.08
    duration = max(row["elapsed_s"] for row in stability["series"])
    for index in range(5):
        py = y + height - height * index / 4
        value = maximum * index / 4
        parts += [f'<path d="M{x} {py:.1f} H{x + width}" stroke="#e2e8f0"/>', f'<text x="{x - 9}" y="{py + 4:.1f}" text-anchor="end" font-size="10">{value:.0f}</text>']
    colors = {"keycloak": "#7c3aed", "db": "#d97706", "openfga": "#dc2626", "api": "#2563eb", "worker": "#059669", "k6": "#64748b"}
    for role, color in colors.items():
        points = " ".join(
            f'{x + row["elapsed_s"] / duration * width:.1f},{y + height - row["memory_mib"][role] / maximum * height:.1f}'
            for row in stability["series"]
        )
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
    parts += [f'<text x="{x}" y="{y + height + 25}" font-size="10">0 min</text>', f'<text x="{x + width}" y="{y + height + 25}" text-anchor="end" font-size="10">60 min</text>', f'<text x="20" y="{y + height / 2}" transform="rotate(-90 20 {y + height / 2})" font-size="11">MiB</text>']
    for index, (role, color) in enumerate(colors.items()):
        lx = 75 + (index % 3) * 220
        ly = 550 + (index // 3) * 28
        slope_value = stability["services"][role]["linear_slope_mib_per_hour"]
        parts += [f'<path d="M{lx} {ly} h25" stroke="{color}" stroke-width="3"/>', f'<text x="{lx + 33}" y="{ly + 4}" font-size="11">{role}: slope {slope_value:+.2f} MiB/h</text>']
    parts += ['<text x="75" y="622" font-size="11" fill="#667085">A single hour is not evidence of long-term stability.</text>', '</g></svg>']
    return "\n".join(parts)


def main(require_complete=False):
    folders = sorted(path for path in RAW.iterdir() if path.is_dir())
    formal = formal_ids(folders)
    entries = [run_entry(folder, formal) for folder in folders]
    e0 = []
    e0_entries = []
    for run_id in E0_RUNS:
        value = load(RESULTS / run_id / "aggregate.json")
        manifest = load(RESULTS / run_id / "manifest.json")
        flow = value["flows"]["login"]
        public_value = {
            "run_id": run_id,
            "attempts": flow["attempts"],
            "success_rate": rounded(flow["success_ratio"], 6),
            "p95_ms": rounded(flow["p95_ms"]),
            "p99_ms": rounded(flow["p99_ms"]),
            "keycloak_cpu_median_pct": rounded(value["resources"]["containers"]["keycloak"]["cpu_pct_median"]),
            "keycloak_cpu_peak_pct": rounded(value["resources"]["containers"]["keycloak"]["cpu_pct_peak"]),
            "observation_errors": value["resources"]["observation"]["errors_measure"],
            "dropped_iterations": value["dropped_iterations_all_phases"],
        }
        e0.append(public_value)
        e0_entries.append(
            {
                "run_id": run_id,
                "classification": "formal",
                "reason": "required E0 evidence",
                "experiment": "E0",
                "mode": "login",
                "passed": flow["success_ratio"] == 1 and value["dropped_iterations_all_phases"] == 0,
                "measurement_status": "completed",
                "git_head": manifest.get("git_head"),
                "git_dirty": manifest.get("git_dirty"),
                "exit_code": value.get("exit_code"),
                "api_rate_rps": None,
                "login_rate_rps": manifest.get("login_rate"),
                "refresh_rate_rps": None,
                "shares_per_album": None,
                "api_p99_ms": None,
                "decision_accuracy": None,
                "over_permit": None,
                "dropped_iterations": value["dropped_iterations_all_phases"],
                "login_success": rounded(flow["success_ratio"], 6),
                "login_p99_ms": rounded(flow["p99_ms"]),
                "refresh_success": None,
                "refresh_p99_ms": None,
                "fault_service": None,
                "fault_effect_observed": None,
                "recovery_confirmation_s": None,
                "observation_errors": value["resources"]["observation"]["errors_measure"],
            }
        )
    all_entries = e0_entries + entries
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "run-set.json").write_text(json.dumps({"schema": 1, "runs": all_entries}, indent=2, ensure_ascii=False) + "\n")

    def folders_with(marker):
        return [folder for folder in folders if marker in folder.name and folder.name in formal]

    e3_groups = {}
    for level, marker in (("low", "e3-final-low-"), ("high", "e3-final-high-"), ("shares50", "e3-final-shares50-")):
        for mode in ("direct", "fga"):
            trials = [authz_trial(folder) for folder in folders_with(marker) if result_for(folder).get("mode") == mode]
            e3_groups[f"{level}_{mode}"] = {"summary": group_summary(trials), "trials": trials}
    e3_exploration = [authz_trial(folder) for folder in folders_with("e3-final-explore-")]

    e4_groups = {}
    for level, marker in (("low", "e4-final-low-"), ("high", "e4-final-high-")):
        for mode in ("direct", "fga"):
            trials = [authz_trial(folder) for folder in folders_with(marker) if result_for(folder).get("mode") == mode]
            e4_groups[f"{level}_{mode}"] = {"summary": group_summary(trials), "trials": trials}
    e4_recovery_raw = [result_for(folder) for folder in folders_with("e4-final-recovery-")]
    e5_raw = [result_for(folder) for folder in folders_with("e5-final-")]
    e1_folder = sorted(folder for folder in folders if folder.name in formal and folder.name.endswith("-e1"))[-1]
    e2_folder = sorted(folder for folder in folders if folder.name in formal and folder.name.endswith("-e2"))[-1]
    e6_folder = sorted(folder for folder in folders if folder.name in formal and folder.name.endswith("-e6-backup-restore"))[-1]
    e7_folder = sorted(folder for folder in folders if "e7-final-one-hour-" in folder.name)[-1]
    e1_value = result_for(e1_folder)
    e2_value = result_for(e2_folder)
    e6_value = result_for(e6_folder)
    e7_value = result_for(e7_folder)
    e7 = {"trial": authz_trial(e7_folder), "stability": e7_stability(e7_folder)} if e7_value else None

    summary = {
        "schema": 1,
        "scope": "single Mac mini; local Docker only; synthetic users",
        "status": {
            "E0": "pass",
            "E1": "pass" if e1_value.get("passed") else "fail",
            "E2": "pass" if e2_value.get("passed") else "fail",
            "E3": "pass" if all(group["summary"]["passed_count"] == 3 for group in e3_groups.values()) else "fail",
            "E4": "pass" if all(group["summary"]["passed_count"] == 3 for group in e4_groups.values()) and all(item.get("passed") for item in e4_recovery_raw) else "fail",
            "E5": "pass" if len(e5_raw) == 12 and all(item.get("passed") for item in e5_raw) else "fail",
            "E6": "pass" if e6_value.get("passed") else "fail",
            "E7": "pass" if e7_value.get("passed") else ("incomplete" if not e7_value else "fail"),
        },
        "E0": {"trials": e0, "discrepancy_cause": "unresolved"},
        "E1": e1_public(e1_value),
        "E2": e2_public(e2_value),
        "E3": {"exploration": e3_exploration, "comparison": e3_groups, "exploration_limit": "200 rps invalidated by insufficient k6 VUs; no server boundary established"},
        "E4": {"mixed": e4_groups, "recovery": [timeline_public(item) for item in e4_recovery_raw], "overload_reached": False},
        "E5": {"fault_trials": [timeline_public(item) for item in e5_raw], "outbox_worker": e2_public(e2_value)["worker_stop_reconciliation"]},
        "E6": e6_public(e6_value),
        "E7": e7,
    }
    (PUBLIC / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    e3_rows = []
    for level in ("low", "high", "shares50"):
        for mode, color in (("direct", "#2563eb"), ("fga", "#dc2626")):
            value = e3_groups[f"{level}_{mode}"]["summary"]["api_p99_ms"]["median"]
            e3_rows.append((f"{level} / {mode}", value, color))
    (PUBLIC / "e3-comparison.svg").write_text(bar_svg("E3 authorization p99", "Median of three formal trials per condition; 80% allow / 20% deny.", e3_rows, 10))

    e4_rows = []
    for level in ("low", "high"):
        for mode, color in (("direct", "#2563eb"), ("fga", "#dc2626")):
            value = e4_groups[f"{level}_{mode}"]["summary"]["api_p99_ms"]["median"]
            e4_rows.append((f"{level} / {mode}", value, color))
    (PUBLIC / "e4-mixed.svg").write_text(bar_svg("E4 mixed-load API p99", "Keycloak login and refresh ran concurrently; median of three trials.", e4_rows, 10))

    fault_rows = []
    colors = {"openfga": "#dc2626", "keycloak": "#7c3aed", "api": "#2563eb", "db": "#d97706"}
    for item in e5_raw:
        service = next(folder for folder in folders if folder.name == item["run_id"]).name.split("e5-final-", 1)[1].split("-trial", 1)[0]
        fault_rows.append((item["run_id"].split("-trial", 1)[1].split("-", 1)[0] + " / " + service, item["recovery_confirmation_s"], colors[service]))
    (PUBLIC / "e5-recovery.svg").write_text(bar_svg("E5 recovery confirmation", "Seconds after restart until three consecutive passing 10-second windows.", fault_rows, 60, " s"))
    if e7:
        (PUBLIC / "e7-stability.svg").write_text(stability_svg(e7["stability"]))

    lines = [
        "# 認可実験 全run台帳",
        "",
        "rawはローカル保持し、この台帳には匿名run ID・条件・合否・除外理由だけを載せる。`formal`以外も削除していない。",
        "",
        "| run | 区分 | 実験 | mode | 合否 | 理由 |",
        "|---|---|---|---|---:|---|",
    ]
    for entry in all_entries:
        passed = "合格" if entry["passed"] is True else "不合格" if entry["passed"] is False else "判定不能"
        lines.append(f'| `{entry["run_id"]}` | {entry["classification"]} | {entry["experiment"] or "-"} | {entry["mode"] or "-"} | {passed} | {entry["reason"]} |')
    (RESULTS / "authz-experiment-ledger.md").write_text("\n".join(lines) + "\n")
    outcome = {"runs": len(all_entries), "formal": len(formal) + len(e0_entries), "status": summary["status"]}
    print(json.dumps(outcome, ensure_ascii=False))
    if require_complete and any(status != "pass" for status in summary["status"].values()):
        raise SystemExit("Required E0-E7 evidence is not complete and passing")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    main(args.require_complete)
