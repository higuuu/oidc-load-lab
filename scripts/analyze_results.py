#!/usr/bin/env python3
"""Create reviewed, aggregate-only comparison artifacts from local run folders.

Raw samples, absolute timestamps, container names, tokens, and responses are read
locally but are never copied into the public artifacts.
"""
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'results'
PUBLIC = RESULTS / 'public'
RUN_SET = PUBLIC / 'run-set.json'

sys.path.insert(0, str(ROOT / 'scripts'))
from report import container_role, docker_bytes, parse_timestamp, prom_value  # noqa: E402


def fmt(value, digits=2):
    if value is None:
        return None
    return round(value, digits)


def stats(values):
    values = [float(value) for value in values if value is not None]
    return {'median': fmt(median(values)), 'min': fmt(min(values)), 'max': fmt(max(values))}


def load_aggregate(run):
    folder = RESULTS / run
    if folder.parent != RESULTS or not (folder / 'aggregate.json').is_file():
        raise SystemExit(f'Missing aggregate for allowlisted run: {run}')
    return json.loads((folder / 'aggregate.json').read_text())


def public_trial(label, run):
    value = load_aggregate(run)
    flows = {}
    for name, flow in value['flows'].items():
        flows[name] = {
            'planned': flow['planned_attempts'],
            'attempted': flow['attempts'],
            'successful': flow['completed'],
            'success_pct': fmt(flow['success_ratio'] * 100, 3),
            'p95_ms': fmt(flow['p95_ms']),
            'p99_ms': fmt(flow['p99_ms']),
            'samples': flow['latency_samples'],
        }
    containers = value.get('resources', {}).get('containers', {})
    database = value.get('resources', {}).get('database') or {}
    metrics = value.get('resources', {}).get('keycloak_metrics', {})
    observation = value.get('resources', {}).get('observation', {})
    login_rate = value['login_rate'] if 'login' in flows else None
    refresh_rate = value['refresh_rate'] if 'refresh' in flows else None
    resources = {
        role: {
            'cpu_median_pct': fmt(containers.get(role, {}).get('cpu_pct_median')),
            'cpu_peak_pct': fmt(containers.get(role, {}).get('cpu_pct_peak')),
            'memory_median_mib': fmt((containers.get(role, {}).get('memory_bytes_median') or 0) / 1048576),
            'memory_peak_mib': fmt((containers.get(role, {}).get('memory_bytes_peak') or 0) / 1048576),
        }
        for role in ['keycloak', 'db', 'k6'] if role in containers
    }
    return {
        'label': label, 'run': run, 'mode': value['mode'], 'seconds': value['seconds'],
        'login_rate': login_rate, 'refresh_average_rate': refresh_rate,
        'refresh_peak_rate': refresh_rate * 2 if refresh_rate is not None and value['mode'].endswith('burst') else refresh_rate,
        'vus_per_scenario': value['vus'], 'exit_code': value['exit_code'],
        'dropped_all_phases': value['dropped_iterations_all_phases'],
        'complete_samples': value['complete_samples'], 'flows': flows, 'resources': resources,
        'db_active_peak': database.get('active_peak'), 'db_waiting_peak': database.get('waiting_peak'),
        'agroal_awaiting_peak': metrics.get('agroal_awaiting_peak'),
        'gc_pause_delta_s': fmt(metrics.get('gc_pause_seconds_delta'), 3),
        'observation_samples': observation.get('sample_count_measure'),
        'observation_errors': observation.get('errors_measure'),
    }


def group_summary(trials):
    result = {'trial_count': len(trials)}
    for flow in ['login', 'refresh']:
        selected = [trial['flows'][flow] for trial in trials if flow in trial['flows']]
        if selected:
            result[flow] = {
                'p99_ms': stats([row['p99_ms'] for row in selected]),
                'success_pct': stats([row['success_pct'] for row in selected]),
                'samples': {'min': min(row['samples'] for row in selected),
                            'max': max(row['samples'] for row in selected)},
            }
    for role in ['keycloak', 'db', 'k6']:
        values = [trial['resources'].get(role, {}) for trial in trials]
        if any(values):
            result[role + '_cpu_median_pct'] = stats([row.get('cpu_median_pct') for row in values])
            result[role + '_cpu_peak_pct'] = stats([row.get('cpu_peak_pct') for row in values])
    result['db_waiting_peak'] = max((trial['db_waiting_peak'] or 0) for trial in trials)
    result['dropped_all_phases'] = sum(trial['dropped_all_phases'] for trial in trials)
    return result


def svg_header(width, height, title, subtitle):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#fbfcfe"/>',
        '<g font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" fill="#172033">',
        f'<text x="55" y="34" font-size="20" font-weight="700">{title}</text>',
        f'<text x="55" y="56" font-size="12" fill="#556176">{subtitle}</text>',
    ]


def panel(parts, x, y, width, height, title, maximum, ticks=4, suffix=''):
    parts.append(f'<text x="{x}" y="{y - 12}" font-size="14" font-weight="600">{title}</text>')
    for i in range(ticks + 1):
        value = maximum * i / ticks
        py = y + height - height * i / ticks
        parts.append(f'<path d="M{x} {py:.1f} H{x + width}" stroke="#dfe4ec"/>')
        tick = f'{value:.2f}'.rstrip('0').rstrip('.') if maximum < 1 else f'{value:.0f}'
        parts.append(f'<text x="{x - 8}" y="{py + 4:.1f}" text-anchor="end" font-size="10" fill="#667085">{tick}{suffix}</text>')


def plot_variability(groups):
    width, height = 1120, 650
    parts = svg_header(width, height, 'OIDC workload variability (3 trials per condition)',
                       'Dots are individual runs; horizontal bars are medians. Success ratios are shown in the lower panels.')
    definitions = [
        ('Login p99 (ms)', 'login', 'p99_ms', 100, 55, 100),
        ('Refresh p99 (ms)', 'refresh', 'p99_ms', 10, 590, 100),
        ('Login success (%)', 'login', 'success_pct', 100, 55, 390),
        ('Refresh success (%)', 'refresh', 'success_pct', 100, 590, 390),
    ]
    colors = {'login-only': '#2563eb', 'refresh-only': '#d97706', 'steady-mixed': '#7c3aed', 'mixed-burst': '#dc2626'}
    for title, flow, field, maximum, x, y in definitions:
        panel(parts, x, y, 470, 175, title, maximum, suffix='%' if field == 'success_pct' else '')
        eligible = [(name, trials) for name, trials in groups.items() if any(flow in trial['flows'] for trial in trials)]
        step = 470 / (len(eligible) + 1)
        for index, (name, trials) in enumerate(eligible, 1):
            px = x + step * index
            values = [trial['flows'][flow][field] for trial in trials]
            for offset, value in zip([-7, 0, 7], values):
                py = y + 175 - value / maximum * 175
                parts.append(f'<circle cx="{px + offset:.1f}" cy="{py:.1f}" r="4.5" fill="{colors[name]}"/>')
            med = median(values)
            py = y + 175 - med / maximum * 175
            parts.append(f'<path d="M{px - 16:.1f} {py:.1f} H{px + 16:.1f}" stroke="#111827" stroke-width="2"/>')
            parts.append(f'<text x="{px:.1f}" y="{y + 195}" text-anchor="middle" font-size="10">{name}</text>')
            label_y = py + 21 if field == 'success_pct' else py - 8
            parts.append(f'<text x="{px:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="10" fill="#344054">med {med:g}</text>')
    parts += ['<text x="55" y="630" font-size="11" fill="#667085">Thresholds used in this lab: success >= 99%, login p99 &lt; 2,000 ms, refresh p99 &lt; 500 ms. They are not production SLOs.</text>', '</g></svg>']
    return '\n'.join(parts)


def representative(trials, flow='login'):
    ordered = sorted(trials, key=lambda row: (row['flows'][flow]['p99_ms'], row['run']))
    return ordered[len(ordered) // 2]


def burst_rate(second, rate):
    position = second % 60
    if position < 15:
        return rate * (1 + position / 15)
    if position < 30:
        return rate * (2 - (position - 15) / 15)
    if position < 45:
        return rate * (1 - (position - 30) / 15)
    return rate * ((position - 45) / 15)


def timeline_for(run, flow):
    aggregate = load_aggregate(run)
    return [row for row in aggregate['timeline'] if row['flow'] == flow and row['second'] < aggregate['seconds']]


def polyline(points, x, y, width, height, seconds, maximum):
    return ' '.join(f'{x + sec / seconds * width:.1f},{y + height - value / maximum * height:.1f}' for sec, value in points)


def plot_traffic(steady, burst):
    width, height = 1120, 700
    parts = svg_header(width, height, 'Scheduled, attempted, and completed arrivals',
                       f'Median-p99 representatives: steady {steady["run"]}; burst {burst["run"]}. 5-second buckets.')
    for row_index, (condition, trial) in enumerate([('steady mixed', steady), ('mixed burst', burst)]):
        for column_index, flow in enumerate(['login', 'refresh']):
            x, y = 55 + column_index * 535, 105 + row_index * 285
            maximum = 25 if flow == 'login' else 220
            panel(parts, x, y, 470, 190, f'{condition}: {flow} flows/s', maximum)
            timeline = timeline_for(trial['run'], flow)
            rate = trial['login_rate'] if flow == 'login' else trial['refresh_average_rate']
            scheduled = [(second, burst_rate(second + 2.5, rate) if condition == 'mixed burst' and flow == 'refresh' else rate)
                         for second in range(0, trial['seconds'], 5)]
            series = [
                (scheduled, '#111827', '5 4'),
                ([(row['second'], row['attempted_per_s']) for row in timeline], '#2563eb', ''),
                ([(row['second'], row['completed_per_s']) for row in timeline], '#059669', ''),
            ]
            for points, color, dash in series:
                dash_attr = f' stroke-dasharray="{dash}"' if dash else ''
                parts.append(f'<polyline points="{polyline(points, x, y, 470, 190, trial["seconds"], maximum)}" fill="none" stroke="{color}" stroke-width="2"{dash_attr}/>')
            parts.append(f'<text x="{x}" y="{y + 212}" font-size="10">0s</text><text x="{x + 470}" y="{y + 212}" text-anchor="end" font-size="10">180s</text>')
    parts += [
        '<path d="M55 666 H85" stroke="#111827" stroke-width="2" stroke-dasharray="5 4"/><text x="92" y="670" font-size="11">scheduled (bucket-center target)</text>',
        '<path d="M310 666 H340" stroke="#2563eb" stroke-width="2"/><text x="347" y="670" font-size="11">attempted starts</text>',
        '<path d="M480 666 H510" stroke="#059669" stroke-width="2"/><text x="517" y="670" font-size="11">successful completions</text>',
        '<text x="715" y="670" font-size="11" fill="#667085">Last partial bucket is omitted.</text>', '</g></svg>']
    return '\n'.join(parts)


def measurement_origin(folder):
    times = []
    with (folder / 'samples.json').open() as handle:
        for line in handle:
            row = json.loads(line)
            tags = row.get('data', {}).get('tags') or {}
            if row.get('type') == 'Point' and row.get('metric') == 'lab_attempts' and tags.get('phase') == 'measure':
                times.append(parse_timestamp(row['data']['time']))
    if not times:
        raise SystemExit(f'No measured attempts in {folder.name}')
    return min(times)


def resource_series(run):
    folder = RESULTS / run
    aggregate = load_aggregate(run)
    origin = measurement_origin(folder)
    output = {name: [] for name in ['keycloak_cpu', 'db_cpu', 'k6_cpu', 'keycloak_mem', 'db_mem', 'k6_mem',
                                           'db_active', 'db_waiting', 'gc_pause']}
    gc_origin = None
    for line in (folder / 'host.jsonl').read_text().splitlines():
        record = json.loads(line)
        second = record.get('unix_s', 0) - origin
        if second < 0 or second > aggregate['seconds']:
            continue
        for item in record.get('containers', []):
            role = container_role(item.get('Name', ''))
            if role not in ['keycloak', 'db', 'k6']:
                continue
            try:
                output[role + '_cpu'].append((second, float(item['CPUPerc'].rstrip('%'))))
            except (KeyError, ValueError):
                pass
            memory = docker_bytes((item.get('MemUsage') or '').split('/', 1)[0])
            if memory is not None:
                output[role + '_mem'].append((second, memory / 1048576))
        if isinstance(record.get('db'), dict):
            output['db_active'].append((second, record['db'].get('active', 0)))
            output['db_waiting'].append((second, record['db'].get('waiting', 0)))
        metrics = folder / f'metrics-{int(record.get("elapsed_s", -1)):06}.prom'
        if metrics.exists():
            pause = prom_value(metrics.read_text(), 'jvm_gc_pause_seconds_sum')
            if pause is not None:
                gc_origin = pause if gc_origin is None else gc_origin
                output['gc_pause'].append((second, max(0, pause - gc_origin)))
    return output


def plot_resources(steady, burst):
    width, height = 1120, 1090
    parts = svg_header(width, height, 'Runtime resources on the same relative time axis',
                       f'Solid=steady {steady["run"]}; dashed=burst {burst["run"]}. CPU 100% means one Docker CPU core.')
    steady_values, burst_values = resource_series(steady['run']), resource_series(burst['run'])
    panels = [
        ('Keycloak CPU (%)', ['keycloak_cpu'], 200),
        ('Database and k6 CPU (%)', ['db_cpu', 'k6_cpu'], 25),
        ('Container memory (MiB)', ['keycloak_mem', 'db_mem', 'k6_mem'], 1500),
        ('Database active / waiting connections', ['db_active', 'db_waiting'], 5),
        ('Keycloak GC pause, cumulative within sampled window (s)', ['gc_pause'], .30),
    ]
    colors = {'keycloak_cpu': '#7c3aed', 'db_cpu': '#d97706', 'k6_cpu': '#2563eb',
              'keycloak_mem': '#7c3aed', 'db_mem': '#d97706', 'k6_mem': '#2563eb',
              'db_active': '#059669', 'db_waiting': '#dc2626', 'gc_pause': '#475467'}
    labels = {'keycloak_cpu': 'Keycloak', 'db_cpu': 'DB', 'k6_cpu': 'k6', 'keycloak_mem': 'Keycloak',
              'db_mem': 'DB', 'k6_mem': 'k6', 'db_active': 'DB active', 'db_waiting': 'DB waiting',
              'gc_pause': 'GC pause'}
    for index, (title, names, maximum) in enumerate(panels):
        x, y = 70, 100 + index * 190
        panel(parts, x, y, 980, 125, title, maximum)
        for name in names:
            for values, dash in [(steady_values, ''), (burst_values, '6 4')]:
                points = values[name]
                if points:
                    dash_attr = f' stroke-dasharray="{dash}"' if dash else ''
                    parts.append(f'<polyline points="{polyline(points, x, y, 980, 125, 180, maximum)}" fill="none" stroke="{colors[name]}" stroke-width="2"{dash_attr}/>')
        legend_x = x + 600
        for offset, name in enumerate(names):
            parts.append(f'<path d="M{legend_x + offset * 125} {y - 14} h22" stroke="{colors[name]}" stroke-width="3"/><text x="{legend_x + 27 + offset * 125}" y="{y - 10}" font-size="10">{labels[name]}</text>')
        parts.append(f'<text x="{x}" y="{y + 145}" font-size="10">0s</text><text x="{x + 980}" y="{y + 145}" text-anchor="end" font-size="10">180s</text>')
    parts += ['<path d="M70 1060 h28" stroke="#475467" stroke-width="2"/><text x="105" y="1064" font-size="11">steady</text>',
              '<path d="M185 1060 h28" stroke="#475467" stroke-width="2" stroke-dasharray="6 4"/><text x="220" y="1064" font-size="11">burst</text>',
              '<text x="335" y="1064" font-size="11" fill="#667085">Observation cadence is approximately 7s; missing samples are not filled with zero.</text>', '</g></svg>']
    return '\n'.join(parts)


def main():
    specification = json.loads(RUN_SET.read_text())
    trials = {}
    for group, entries in specification['comparison'].items():
        trials[group] = [public_trial(entry['label'], entry['run']) for entry in entries]
    exploration = {flow: [public_trial(entry['label'], entry['run']) for entry in entries]
                   for flow, entries in specification['exploration'].items()}
    support = {name: [public_trial(entry['label'], entry['run']) for entry in entries]
               for name, entries in specification['support'].items()}
    output = {
        'status': 'reviewed local experiment aggregates; not production capacity evidence',
        'method': {'comparison_trials_per_condition': 3, 'warmup_seconds': 60, 'measure_seconds': 180,
                   'time_axis': 'seconds since first measured attempt',
                   'cpu_note': 'Docker CPU percentage; 100% is one CPU core'},
        'support': support, 'exploration': exploration, 'comparison': trials,
        'summary': {group: group_summary(rows) for group, rows in trials.items()},
    }
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / 'comparison-summary.json').write_text(json.dumps(output, indent=2, allow_nan=False) + '\n')
    (PUBLIC / 'condition-variability.svg').write_text(plot_variability(trials) + '\n')
    steady = representative(trials['steady-mixed'])
    burst = representative(trials['mixed-burst'])
    (PUBLIC / 'traffic-timeline.svg').write_text(plot_traffic(steady, burst) + '\n')
    (PUBLIC / 'resource-timeline.svg').write_text(plot_resources(steady, burst) + '\n')
    print(f'Wrote reviewed aggregate artifacts to {PUBLIC.relative_to(ROOT)}/')


if __name__ == '__main__':
    main()
