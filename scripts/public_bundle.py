#!/usr/bin/env python3
"""Copy only reviewed source paths to a clean directory for a future public repo."""
import argparse
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    '.gitignore', '.dockerignore', 'Containerfile', 'compose.yaml', 'README.md', 'LICENSE',
    'load/config.mjs', 'load/oidc.js',
    'scripts/lab.py', 'scripts/report.py', 'scripts/public_bundle.py',
    'tests/config.test.mjs', 'tests/protocol.test.mjs', 'tests/test_report.py',
    'docs/experiment.md', 'docs/architecture.md', 'docs/publication.md', 'docs/validation.md',
]
PATTERNS = {
    'private-key': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'jwt': re.compile(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}'),
    'personal-path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'cloud-key': re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'),
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
        print('Exported allowlisted source to dist/oidc-load-lab. Runtime data/results excluded.')
    else:
        print(f'Checked {len(FILES)} allowlisted source files. No known secret patterns detected.')


if __name__ == '__main__':
    main()
