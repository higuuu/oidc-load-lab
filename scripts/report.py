#!/usr/bin/env python3
"""Produce aggregate-only reports from local k6 samples (no responses/tokens)."""
import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * p
    low = int(position)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)


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
                timed.append((datetime.fromisoformat(data['time'].replace('Z', '+00:00')).timestamp(), tags['flow'], name, v))
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
