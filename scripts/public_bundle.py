#!/usr/bin/env python3
"""Copy only explicitly reviewed paths to a clean public directory."""
import argparse
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = [
    '.gitignore', '.dockerignore', 'Containerfile', 'compose.yaml', 'README.md', 'LICENSE',
    'load/config.mjs', 'load/oidc.js',
    'scripts/lab.py', 'scripts/report.py', 'scripts/analyze_results.py', 'scripts/public_bundle.py',
    'tests/config.test.mjs', 'tests/protocol.test.mjs', 'tests/test_report.py', 'tests/test_analyze_results.py',
    'docs/experiment.md', 'docs/architecture.md', 'docs/publication.md', 'docs/validation.md',
    'docs/mac-mini-handoff.md',
    'scripts/build_article.py',
]
RESULT_FILES = [
    'results/environment.md', 'results/experiment-ledger.md', 'results/final-report.md',
    'results/public/run-set.json', 'results/public/comparison-summary.json',
    'results/public/condition-variability.svg', 'results/public/traffic-timeline.svg',
    'results/public/resource-timeline.svg',
    'article/README.md', 'article/qiita.md', 'article/oidc-measurement.md', 'article/pr1-review.md',
    'article/generated-results.md', 'article/evidence-check.json', 'article/readability-review.md',
    'article/figures/reader-comparison.svg', 'article/figures/reader-discrepancy.svg',
    'article/figures/latency-and-cpu.svg', 'article/figures/exploration-discrepancy.svg',
]
FILES = SOURCE_FILES + RESULT_FILES
PATTERNS = {
    'private-key': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'jwt': re.compile(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}'),
    'personal-path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'cloud-key': re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'),
}
RESULT_PATTERNS = {
    'email-address': re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
    'bearer-value': re.compile(r'(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9]'),
    'credential-assignment': re.compile(
        r'(?i)["\']?(?:password|client_secret|access_token|refresh_token|authorization_code|cookie|session_id)'
        r'["\']?\s*[:=]\s*["\'][^"\'\n]{4,}'
    ),
}


def inspect():
    failures = []
    for name in FILES:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            failures.append(f'{name}: missing file or symlink')
            continue
        text = path.read_text()
        for kind, pattern in PATTERNS.items():
            if pattern.search(text):
                failures.append(f'{name}: {kind}')  # Never print matched secret values.
        if name in RESULT_FILES:
            for kind, pattern in RESULT_PATTERNS.items():
                if pattern.search(text):
                    failures.append(f'{name}: {kind}')
    if failures:
        raise SystemExit('\n'.join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true')
    args = parser.parse_args()
    inspect()
    if args.export:
        target = ROOT / 'dist' / 'oidc-load-lab'
        target.mkdir(parents=True, exist_ok=False)
        for name in FILES:
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        print('Exported allowlisted files to dist/oidc-load-lab. Secrets and raw run directories excluded.')
    else:
        print(f'Checked {len(FILES)} allowlisted public files. No known secret patterns detected.')


if __name__ == '__main__':
    main()
