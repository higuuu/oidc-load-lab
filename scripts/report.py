#!/usr/bin/env python3
"""Produce aggregate-only reports from local k6 samples (no responses/tokens)."""
import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parents[1]


def parse_timestamp(value):
    """Parse k6 RFC3339 timestamps with 1-9 fraction digits on Python 3.10."""
    value = value.replace('Z', '+00:00')
    match = re.fullmatch(r'(.+?)(?:\.(\d+))?([+-]\d{2}:\d{2})', value)
    if not match:
        raise ValueError(f'Invalid RFC3339 timestamp: {value!r}')
    fraction = (match.group(2) or '').ljust(6, '0')[:6]
    value = f'{match.group(1)}.{fraction}{match.group(3)}'
    return datetime.fromisoformat(value).timestamp()


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * p
    low = int(position)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)


def docker_bytes(value):
    match = re.match(r'\s*([0-9.]+)\s*([KMGT]?i?B)', value or '')
    if not match:
        return None
    units = {'B': 1, 'kB': 1000, 'KB': 1000, 'KiB': 1024,
             'MB': 1000 ** 2, 'MiB': 1024 ** 2,
             'GB': 1000 ** 3, 'GiB': 1024 ** 3,
             'TB': 1000 ** 4, 'TiB': 1024 ** 4}
    return float(match.group(1)) * units[match.group(2)]


def container_role(name):
    for role in ['keycloak', 'db', 'k6']:
        if f'-{role}-' in name:
            return role
    return None


def prom_value(text, name, label=None):
    values = []
    for line in text.splitlines():
        if line.startswith('#') or not (line.startswith(name + ' ') or line.startswith(name + '{')):
            continue
        if label and label not in line.split(' ', 1)[0]:
            continue
        try:
            values.append(float(line.rsplit(' ', 1)[1]))
        except (ValueError, IndexError):
            continue
    return sum(values) if values else None


def resource_summary(folder, origin, seconds):
    host = folder / 'host.jsonl'
    if not host.exists() or origin is None:
        return None
    records = [json.loads(line) for line in host.read_text().splitlines() if line.strip()]
    intervals = [records[i]['unix_s'] - records[i - 1]['unix_s'] for i in range(1, len(records))]
    measured = [r for r in records if origin <= r.get('unix_s', 0) <= origin + seconds]
    containers = defaultdict(lambda: {'cpu_pct': [], 'memory_pct': [], 'memory_bytes': []})
    for record in measured:
        for item in record.get('containers', []):
            role = container_role(item.get('Name', ''))
            if not role:
                continue
            try:
                containers[role]['cpu_pct'].append(float(item['CPUPerc'].rstrip('%')))
                containers[role]['memory_pct'].append(float(item['MemPerc'].rstrip('%')))
            except (KeyError, ValueError):
                pass
            value = docker_bytes((item.get('MemUsage') or '').split('/', 1)[0])
            if value is not None:
                containers[role]['memory_bytes'].append(value)
    container_result = {}
    for role, values in sorted(containers.items()):
        container_result[role] = {}
        for field, samples in values.items():
            if samples:
                container_result[role][field + '_median'] = median(samples)
                container_result[role][field + '_peak'] = max(samples)

    db_rows = [r['db'] for r in measured if isinstance(r.get('db'), dict)]
    database = None
    if db_rows:
        database = {f'{name}_peak': max(r.get(name, 0) for r in db_rows)
                    for name in ['connections', 'active', 'waiting']}
        for name in ['commits', 'rollbacks', 'blocks_read', 'blocks_hit', 'temp_bytes', 'deadlocks']:
            database[f'{name}_delta'] = db_rows[-1].get(name, 0) - db_rows[0].get(name, 0)

    snapshots = []
    for record in measured:
        metrics = folder / f'metrics-{int(record.get("elapsed_s", -1)):06}.prom'
        if not metrics.exists():
            continue
        text = metrics.read_text()
        snapshots.append({
            'agroal_active': prom_value(text, 'agroal_active_count'),
            'agroal_awaiting': prom_value(text, 'agroal_awaiting_count'),
            'agroal_available': prom_value(text, 'agroal_available_count'),
            'heap_used_bytes': prom_value(text, 'jvm_memory_used_bytes', 'area="heap"'),
            'gc_pause_seconds': prom_value(text, 'jvm_gc_pause_seconds_sum'),
            'gc_pause_count': prom_value(text, 'jvm_gc_pause_seconds_count'),
            'http_requests': prom_value(text, 'http_server_requests_seconds_count'),
            'password_validations': prom_value(text, 'keycloak_credentials_password_hashing_validations_total'),
        })
    metrics_result = {'sample_count': len(snapshots)}
    for name in ['agroal_active', 'agroal_awaiting', 'agroal_available', 'heap_used_bytes']:
        values = [s[name] for s in snapshots if s[name] is not None]
        if values:
            metrics_result[name + '_peak'] = max(values)
    for name in ['gc_pause_seconds', 'gc_pause_count', 'http_requests', 'password_validations']:
        values = [s[name] for s in snapshots if s[name] is not None]
        if values:
            metrics_result[name + '_delta'] = max(0, values[-1] - values[0])

    return {
        'observation': {
            'sample_count_all': len(records), 'sample_count_measure': len(measured),
            'errors_all': sum(bool(r.get('observation_error')) for r in records),
            'errors_measure': sum(bool(r.get('observation_error')) for r in measured),
            'interval_s_min': min(intervals) if intervals else None,
            'interval_s_median': median(intervals) if intervals else None,
            'interval_s_max': max(intervals) if intervals else None,
        },
        'containers': container_result,
        'database': database,
        'keycloak_metrics': metrics_result,
    }


def aggregate(folder):
    manifest = json.loads((folder / 'manifest.json').read_text())
    flows = defaultdict(lambda: {'attempts': 0, 'completed': 0, 'latency': [], 'success_latency': []})
    dropped = 0
    timed = []
    # Only fixed custom metrics and numeric values survive this allowlist.
    with (folder / 'samples.json').open() as f:
        for line in f:
            row = json.loads(line)
            if row.get('type') != 'Point':
                continue
            name, data = row['metric'], row['data']
            if name == 'dropped_iterations':
                dropped += data['value']
            tags = data.get('tags') or {}
            if tags.get('phase') != 'measure' or tags.get('flow') not in ['login', 'refresh', 'smoke']:
                continue
            v = data['value']
            if not isinstance(v, (int, float)):
                continue
            target = flows[tags['flow']]
            if data.get('time') and name in ['lab_attempts', 'lab_completed', 'lab_latency_ms']:
                timed.append((parse_timestamp(data['time']), tags['flow'], name, v))
            if name == 'lab_attempts':
                target['attempts'] += v
            elif name == 'lab_completed':
                target['completed'] += v
            elif name == 'lab_latency_ms':
                target['latency'].append(v)
            elif name == 'lab_success_latency_ms':
                target['success_latency'].append(v)
    result = {'mode': manifest['mode'], 'seconds': manifest['seconds'], 'vus': manifest['vus'],
              'login_rate': manifest['login_rate'], 'refresh_rate': manifest['refresh_rate'],
              'exit_code': manifest['exit_code'], 'dropped_iterations_all_phases': dropped,
              'flows': {}, 'interpretation': 'unreviewed'}
    for flow, values in sorted(flows.items()):
        n, good = values['attempts'], values['completed']
        planned = 1 if flow == 'smoke' else manifest['seconds'] * manifest[flow + '_rate']
        result['flows'][flow] = {
            'planned_attempts': planned, 'attempts': n, 'completed': good,
            'success_ratio': good / n if n else None,
            'achieved_flows_per_second': good / manifest['seconds'] if flow != 'smoke' else None,
            'p95_ms': percentile(values['latency'], .95),
            'p99_ms': percentile(values['latency'], .99),
            'successful_p99_ms': percentile(values['success_latency'], .99),
            'latency_samples': len(values['latency']),
        }
    result['complete_samples'] = bool(flows) and all(v['attempts'] == v['latency_samples'] for v in result['flows'].values())
    # Timestamps stay private. Public time axis is seconds since first measured sample.
    origin = None
    if timed:
        origin = min(t[0] for t in timed)
        bins = defaultdict(lambda: {'attempts': 0, 'completed': 0, 'latency': []})
        for timestamp, flow, name, value in timed:
            bucket = int((timestamp - origin) // 5) * 5
            b = bins[bucket, flow]
            if name == 'lab_attempts': b['attempts'] += value
            if name == 'lab_completed': b['completed'] += value
            if name == 'lab_latency_ms': b['latency'].append(value)
        result['timeline'] = [{'second': sec, 'flow': flow, 'attempted_per_s': b['attempts'] / 5,
                               'completed_per_s': b['completed'] / 5, 'p99_ms': percentile(b['latency'], .99)}
                              for (sec, flow), b in sorted(bins.items())]
        # Completion timestamp buckets differ from start buckets under backlog.
        result['timeline_note'] = '5s buckets; attempts at start, latency/success at completion; last bucket may be partial.'
    resources = resource_summary(folder, origin, manifest['seconds'])
    if resources:
        result['resources'] = resources
    return result


def plot(result):
    timeline = result.get('timeline', [])
    if not timeline:
        return None
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="560" viewBox="0 0 960 560">',
             '<rect width="960" height="560" fill="white"/>',
             '<g font-family="sans-serif" font-size="13" fill="#222">',
             '<text x="55" y="26">Local OIDC measurements — 5s buckets, unreviewed</text>']
    xmax = max(result['seconds'], max(p['second'] for p in timeline) + 5)
    colors = {'login': '#2563eb', 'refresh': '#d97706', 'smoke': '#059669'}
    for top, field, title in [(60, 'completed_per_s', 'Successful flows/s'), (310, 'p99_ms', 'p99 latency at completion (ms)')]:
        extra = [p['attempted_per_s'] for p in timeline] if field == 'completed_per_s' else []
        ymax = max([p[field] or 0 for p in timeline] + extra + [1]) * 1.1
        parts.append(f'<text x="55" y="{top - 10}">{title}</text>')
        for i in range(5):
            y = top + 180 - 45 * i
            parts.append(f'<path d="M55 {y} H915" stroke="#ddd"/><text x="4" y="{y}">{ymax * i / 4:.0f}</text>')
        for flow, color in colors.items():
            points = ' '.join(f'{55 + p["second"] / xmax * 860:.1f},{top + 180 - (p[field] or 0) / ymax * 180:.1f}' for p in timeline if p['flow'] == flow and p[field] is not None)
            if points:
                parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
            if field == 'completed_per_s':
                attempt_points = ' '.join(f'{55 + p["second"] / xmax * 860:.1f},{top + 180 - p["attempted_per_s"] / ymax * 180:.1f}' for p in timeline if p['flow'] == flow)
                if attempt_points:
                    parts.append(f'<polyline points="{attempt_points}" fill="none" stroke="{color}" stroke-dasharray="4 4"/>')
        parts.append(f'<text x="55" y="{top + 202}">0s</text><text x="860" y="{top + 202}">{xmax}s</text>')
    parts += ['<text x="55" y="545" fill="#2563eb">login</text><text x="130" y="545" fill="#d97706">refresh</text><text x="220" y="545">solid: completed / dashed: attempted</text>', '</g></svg>']
    return '\n'.join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', help='Folder name under results/')
    args = parser.parse_args()
    folder = (ROOT / 'results' / args.run).resolve()
    if folder.parent != (ROOT / 'results').resolve():
        parser.error('Use one run folder name, no external paths.')
    result = aggregate(folder)
    (folder / 'aggregate.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    svg = plot(result)
    if svg:
        (folder / 'timeline.svg').write_text(svg)
    rows = ['# Local run review', '', '**Unreviewed measurements. Not production capacity evidence.**', '',
            '| Flow | Planned | Attempted | Successful | Success % | Completed/s | p95 ms | p99 ms |',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
    for flow, v in result['flows'].items():
        def fmt(x):
            return 'n/a' if x is None else f'{x:.2f}'
        rows.append(f'| {flow} | {v["planned_attempts"]} | {v["attempts"]} | {v["completed"]} | {fmt(v["success_ratio"] * 100 if v["success_ratio"] is not None else None)} | {fmt(v["achieved_flows_per_second"])} | {fmt(v["p95_ms"])} | {fmt(v["p99_ms"])} |')
    rows += ['', f'Dropped iterations (including warmup): {result["dropped_iterations_all_phases"]}',
             f'Complete latency samples: {result["complete_samples"]}; exit code: {result["exit_code"]}', '',
             'Inspect private host.jsonl and metrics files for generator saturation, DB/CPU/GC and observation gaps.',
             'Requests still finishing after the arrival window are counted; achieved/s is completed work per scheduled window.']
    (folder / 'report.md').write_text('\n'.join(rows) + '\n')
    print('\n'.join(rows))


if __name__ == '__main__':
    main()
