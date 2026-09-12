#!/usr/bin/env python3
"""Local synthetic OIDC lab. Python standard library only."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ['docker', 'compose', '--project-directory', str(ROOT), '-f', str(ROOT / 'compose.yaml')]


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def initialize():
    if (ROOT / '.env').exists():
        raise SystemExit('Already initialized. Existing credentials preserved.')
    runtime = ROOT / '.runtime' / 'import'
    runtime.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(32)
    env = f'DB_PASSWORD={secrets.token_urlsafe(32)}\nADMIN_PASSWORD={secrets.token_urlsafe(32)}\nLAB_PASSWORD={password}\n'
    fd = os.open(ROOT / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(env)
    realm = {
        'realm': 'oidc-lab', 'enabled': True, 'sslRequired': 'none',
        'registrationAllowed': False, 'resetPasswordAllowed': False,
        'bruteForceProtected': False, 'eventsEnabled': False,
        'accessTokenLifespan': 300, 'ssoSessionIdleTimeout': 7200,
        'ssoSessionMaxLifespan': 14400,
        'revokeRefreshToken': True, 'refreshTokenMaxReuse': 0,
        'passwordPolicy': 'hashAlgorithm(argon2) and hashIterations(5)',
        'clients': [{
            'clientId': 'load-client', 'protocol': 'openid-connect',
            'publicClient': True, 'standardFlowEnabled': True,
            'directAccessGrantsEnabled': False, 'implicitFlowEnabled': False,
            'serviceAccountsEnabled': False,
            'redirectUris': ['http://127.0.0.1:18081/callback'],
            'attributes': {'pkce.code.challenge.method': 'S256'},
        }],
        'users': [{'username': f'user-{i:05}', 'enabled': True,
                   'email': f'user-{i:05}@example.invalid', 'emailVerified': True,
                   'firstName': 'Synthetic', 'lastName': f'User{i:05}',
                   'requiredActions': [],
                   'credentials': [{'type': 'password', 'value': password, 'temporary': False}]}
                  for i in range(1000)],
    }
    # Container must read the synthetic import; parent directory and .gitignore isolate it.
    (ROOT / '.runtime').chmod(0o700)
    (runtime / 'realm.json').write_text(json.dumps(realm))
    (ROOT / 'results').mkdir(exist_ok=True)
    print('Generated local-only credentials and 1,000 synthetic users. No values printed.')


def ready():
    for _ in range(180):
        try:
            with urllib.request.urlopen('http://127.0.0.1:19000/health/ready', timeout=3) as r:
                if r.status == 200:
                    with urllib.request.urlopen('http://127.0.0.1:18080/realms/oidc-lab/.well-known/openid-configuration', timeout=3) as d:
                        if json.load(d)['issuer'] == 'http://keycloak:8080/realms/oidc-lab':
                            return
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit('Readiness timed out. Inspect local docker compose logs; do not publish raw logs.')


def observe(stop, folder, origin):
    """Private raw evidence. No SQL text, env inspection or user/session rows."""
    with (folder / 'host.jsonl').open('w') as out:
        while not stop.is_set():
            record = {'elapsed_s': round(time.monotonic() - origin, 3), 'unix_s': time.time()}
            try:
                p = subprocess.run(COMPOSE + ['ps', '--all', '--status', 'running', '-q'], capture_output=True, text=True, timeout=8, check=True)
                ids = p.stdout.split()
                if ids:
                    s = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}'] + ids,
                                       capture_output=True, text=True, timeout=10, check=True)
                    record['containers'] = [json.loads(line) for line in s.stdout.splitlines()]
                sql = "SELECT json_build_object('connections',(SELECT count(*) FROM pg_stat_activity WHERE datname='keycloak'),'active',(SELECT count(*) FROM pg_stat_activity WHERE datname='keycloak' AND state='active'),'waiting',(SELECT count(*) FROM pg_stat_activity WHERE datname='keycloak' AND wait_event_type='Lock'),'commits',xact_commit,'rollbacks',xact_rollback,'blocks_read',blks_read,'blocks_hit',blks_hit,'temp_bytes',temp_bytes,'deadlocks',deadlocks) FROM pg_stat_database WHERE datname='keycloak';"
                p = subprocess.run(COMPOSE + ['exec', '-T', 'db', 'psql', '-U', 'lab', '-d', 'keycloak', '-Atc', sql],
                                   capture_output=True, text=True, timeout=8, check=True)
                record['db'] = json.loads(p.stdout)
                with urllib.request.urlopen('http://127.0.0.1:19000/metrics', timeout=5) as r:
                    (folder / f'metrics-{int(record["elapsed_s"]):06}.prom').write_bytes(r.read())
            except Exception:
                record['observation_error'] = True
            out.write(json.dumps(record) + '\n')
            out.flush()
            stop.wait(5)


def run(args):
    if not (ROOT / '.env').exists():
        raise SystemExit('Run init first.')
    root = ROOT / 'results'
    root.mkdir(exist_ok=True)
    folder = root / (time.strftime('%Y%m%dT%H%M%S') + '-' + args.mode)
    folder.mkdir()  # Refuse collision; never overwrite a run.
    # Docker image writes as non-root. Desktop bind mounts normally map the owner;
    # explicit user 0 makes the same commands usable under Linux too.
    print('Resetting ONLY oidc-load-lab synthetic database for a comparable initial state.', flush=True)
    command(COMPOSE + ['down', '--volumes', '--remove-orphans'])
    command(COMPOSE + ['up', '-d', '--build', 'db', 'keycloak'])
    ready()
    manifest = {'mode': args.mode, 'login_rate': args.login_rate, 'refresh_rate': args.refresh_rate,
                'seconds': args.seconds, 'vus': args.vus, 'users': 1000,
                'keycloak': '26.7.3', 'postgres': '17.6', 'k6': '1.8.1',
                'measurement_status': 'started'}
    p = command(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    manifest['git_head'] = p.stdout.strip()
    p = command(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, capture_output=True, text=True)
    manifest['git_dirty'] = bool(p.stdout.strip())
    # No hostname, user name, paths, env or full docker inspect in metadata.
    p = command(['docker', 'info', '--format',
                 '{"architecture":"{{.Architecture}}","cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}}}'],
                capture_output=True, text=True)
    manifest['docker_vm'] = json.loads(p.stdout)
    manifest['architecture'] = manifest['docker_vm']['architecture']
    image_ids = {}
    image_architectures = {}
    for name in ['oidc-load-lab-keycloak:26.7.3', 'postgres:17.6', 'grafana/k6:1.8.1']:
        p = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', name], capture_output=True, text=True)
        image_ids[name] = p.stdout.strip() if p.returncode == 0 else 'not-pulled-yet'
        p = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Architecture}}', name], capture_output=True, text=True)
        image_architectures[name] = p.stdout.strip() if p.returncode == 0 else 'not-pulled-yet'
    manifest['image_ids'] = image_ids
    manifest['image_architectures'] = image_architectures
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    stop = threading.Event()
    watcher = threading.Thread(target=observe, args=(stop, folder, time.monotonic()), daemon=True)
    watcher.start()
    cmd = COMPOSE + ['run', '--rm', '--user', '0:0', '--no-deps', '-v', f'{folder}:/results',
                     '-e', f'MODE={args.mode}', '-e', f'LOGIN_RATE={args.login_rate}',
                     '-e', f'REFRESH_RATE={args.refresh_rate}', '-e', f'SECONDS={args.seconds}',
                     '-e', f'VUS={args.vus}', 'k6', 'run', '--out', 'json=/results/samples.json', '/scripts/oidc.js']
    print('Running. Console/raw telemetry stay in ignored results/.', flush=True)
    code = 1
    try:
        with (folder / 'console.log').open('w') as log:
            code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    finally:
        stop.set()
        watcher.join(timeout=40)
        manifest['exit_code'] = code
        manifest['measurement_status'] = 'finished' if code == 0 else 'failed_or_threshold_exceeded'
        p = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'grafana/k6:1.8.1'], capture_output=True, text=True)
        if p.returncode == 0:
            manifest['image_ids']['grafana/k6:1.8.1'] = p.stdout.strip()
        p = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Architecture}}', 'grafana/k6:1.8.1'], capture_output=True, text=True)
        if p.returncode == 0:
            manifest['image_architectures']['grafana/k6:1.8.1'] = p.stdout.strip()
        (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(f'Run {folder.name}: exit={code}. Run report to review evidence; nonzero is not a capacity result by itself.')
    return code


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    for action in ['init', 'check', 'stop']:
        sub.add_parser(action)
    r = sub.add_parser('run')
    r.add_argument('mode', choices=['smoke', 'login', 'refresh', 'mixed', 'refresh-burst', 'mixed-burst'])
    r.add_argument('--login-rate', type=int, default=5)
    r.add_argument('--refresh-rate', type=int, default=20)
    r.add_argument('--seconds', type=int, default=180)
    r.add_argument('--vus', type=int, default=100)
    args = p.parse_args()
    if args.action == 'init':
        initialize()
    elif args.action == 'check':
        command(['docker', 'compose', 'version'])
        command(COMPOSE + ['config', '--quiet'])
        command(['node', '--test', 'tests/config.test.mjs', 'tests/protocol.test.mjs'], cwd=ROOT)
        command([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=ROOT)
        command([sys.executable, 'scripts/public_bundle.py'], cwd=ROOT)
    elif args.action == 'stop':
        command(COMPOSE + ['down'])
    else:
        if not (1 <= args.login_rate <= 500 and 1 <= args.refresh_rate <= 2000 and 2 <= args.vus <= 250 and 60 <= args.seconds <= 1800):
            p.error('Rates/VUs/duration out of supported range. See README.')
        if args.mode.endswith('burst') and args.seconds % 60:
            p.error('Burst duration must be a multiple of 60 seconds.')
        lock_path = ROOT / '.runtime' / 'run.lock'
        lock_path.parent.mkdir(exist_ok=True)
        with lock_path.open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit('Another lab run is active. Concurrent runs would reset its DB.')
            return run(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
