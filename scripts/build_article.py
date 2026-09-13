#!/usr/bin/env python3
"""Verify published aggregates and draw article figures without private run files."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median

from analyze_results import group_summary

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'results/public/comparison-summary.json'
ARTICLE = ROOT / 'article'
ORDER = ['login-only', 'refresh-only', 'steady-mixed', 'mixed-burst']
LABELS = ['Login only', 'Refresh only', 'Steady mixed', 'Mixed burst']


def verify(data):
    assert set(data['comparison']) == set(ORDER)
    total = 0
    observation_errors = {}
    for name in ORDER:
        rows = data['comparison'][name]
        assert len(rows) == 3, name
        assert group_summary(rows) == data['summary'][name], name
        for row in rows:
            assert row['seconds'] == 180 and row['vus_per_scenario'] == 100
            assert row['exit_code'] == 0 and row['dropped_all_phases'] == 0
            assert row['complete_samples']
            assert isinstance(row['observation_errors'], int) and row['observation_errors'] >= 0
            if row['observation_errors']:
                observation_errors[row['label']] = row['observation_errors']
            assert row['observation_samples'] > 0
            for flow, values in row['flows'].items():
                expected_rate = 20 if flow == 'login' else 100
                assert values['planned'] == expected_rate * 180
                assert values['successful'] == values['attempted'] == values['samples']
                assert values['success_pct'] == 100
                assert values['attempted'] in [values['planned'], values['planned'] + 1]
            total += 1
    return {'reviewed_pr_head': '10fe2ac519ff23fd4505907040a37f35be854f4f',
            'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'comparison_trials_checked': total,
            'observation_errors_reported': observation_errors,
            'evidence_level': 'published per-run aggregates; raw samples and manifests unavailable in this checkout',
            'checks': ['group summaries recomputed from all 12 published trials',
                       'counts, success, duration, VUs, dropped and observation presence reconciled']}


def tables(data):
    text = ['# 公開集約JSONから再計算した記事用数値', '',
            'CPUは各runの観測中央値をさらに3試行で集約。p99は各runのp99を3試行で集約。', '',
            '| 条件 | login p99 ms 中央値 [範囲] | refresh p99 ms 中央値 [範囲] | KC CPU % 中央値 [範囲] |',
            '|---|---:|---:|---:|']
    def cell(v):
        return f'{v["median"]:g} [{v["min"]:g}–{v["max"]:g}]'
    for name in ORDER:
        s = data['summary'][name]
        text.append('| ' + name + ' | ' + ' | '.join(cell(s[f]['p99_ms']) if f in s else '—' for f in ['login', 'refresh']) + ' | ' + cell(s['keycloak_cpu_median_pct']) + ' |')
    text += ['', '| run | login p99 ms | refresh p99 ms | KC CPU median % | DB CPU median % | k6 CPU median % |', '|---|---:|---:|---:|---:|---:|']
    for name in ORDER:
        for row in data['comparison'][name]:
            cells = [row['label']] + [str(row['flows'][f]['p99_ms']) if f in row['flows'] else '—' for f in ['login', 'refresh']]
            cells += [str(row['resources'][r]['cpu_median_pct']) for r in ['keycloak', 'db', 'k6']]
            text.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(text) + '\n'


def figures(data):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11, 'svg.hashsalt': 'oidc-article'})
    out = ARTICLE / 'figures'
    out.mkdir(exist_ok=True)
    preview = ARTICLE / '.preview'
    preview.mkdir(exist_ok=True)
    colors = ['#2563eb', '#d97706', '#7c3aed', '#dc2626']
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout='constrained')
    for i, name in enumerate(ORDER):
        rows = data['comparison'][name]
        cpu = [r['resources']['keycloak']['cpu_median_pct'] for r in rows]
        for ax, values in [(axes[0], [r['flows']['login']['p99_ms'] for r in rows if 'login' in r['flows']]), (axes[1], cpu)]:
            if values:
                ax.scatter([i-.06, i, i+.06], values, s=48, color=colors[i], zorder=3)
                med = median(values)
                ax.hlines(med, i-.2, i+.2, color='#111827', linewidth=2)
                ax.annotate(f'{med:g}', (i, max(values)), xytext=(0, 9), textcoords='offset points', ha='center')
    for ax in axes:
        ax.set_xticks(range(4), LABELS, rotation=15)
        ax.set_xlim(-.5, 3.5)
        ax.grid(axis='y', alpha=.25)
        ax.spines[['top', 'right']].set_visible(False)
    axes[0].set(title='Login tail latency', ylabel='Per-run p99 (ms)', ylim=(0, 105))
    axes[0].text(1, 8, 'No login flow', ha='center', fontsize=9, color='#666')
    axes[1].set(title='Keycloak CPU consumption', ylabel='Per-run sampled CPU median (%)', ylim=(0, 215))
    axes[1].axhline(200, color='#777', linestyle='--', linewidth=1)
    axes[1].text(.1, 202, 'Configured limit: 2 CPUs', color='#666', fontsize=9)
    fig.suptitle('Similar login p99, different sampled CPU usage\n20 logins/s; refresh 100/s average; 180s; 3 trials per condition', fontsize=14)
    fig.savefig(out/'latency-and-cpu.svg', metadata={'Date': None})
    fig.savefig(preview/'latency-and-cpu.png', dpi=180)
    plt.close(fig)
    # Show the unexplained exploration discrepancy rather than hide it.
    exploration = next(r for r in data['exploration']['login'] if r['login_rate'] == 20)
    rows = [exploration] + data['comparison']['login-only']
    labels = ['Exploration', 'Main r1', 'Main r2', 'Main r3']
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.3), layout='constrained')
    for ax, values, title, unit in [
        (axes[0], [r['flows']['login']['p99_ms'] for r in rows], 'Login p99', 'ms'),
        (axes[1], [r['resources']['keycloak']['cpu_median_pct'] for r in rows], 'Sampled Keycloak CPU median', '%')]:
        ax.bar(labels, values, color=['#94a3b8', '#2563eb', '#2563eb', '#2563eb'], width=.55)
        for i, value in enumerate(values): ax.text(i, value + max(values)*.025, f'{value:g}', ha='center')
        ax.set(title=title, ylabel=unit, ylim=(0, max(values)*1.25))
        ax.spines[['top','right']].set_visible(False)
        ax.grid(axis='y', alpha=.2)
    fig.suptitle('Same nominal 20 logins/s: an unexplained exploration discrepancy\nExploration used local fixes; main trials used a recorded clean commit', fontsize=13)
    fig.savefig(out/'exploration-discrepancy.svg', metadata={'Date': None})
    fig.savefig(preview/'exploration-discrepancy.png', dpi=180)
    plt.close(fig)
    for path in out.glob('*.svg'):
        path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines()) + '\n')


def reader_figures(data):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import font_manager
    import matplotlib.pyplot as plt
    available = {font.name for font in font_manager.fontManager.ttflist}
    family = next((name for name in ['Hiragino Sans', 'Noto Sans CJK JP', 'IPAexGothic'] if name in available), None)
    if family is None:
        raise SystemExit('Install a Japanese font: Hiragino Sans, Noto Sans CJK JP or IPAexGothic.')
    plt.rcParams.update({'font.family': family, 'font.size': 12, 'svg.hashsalt': 'oidc-reader'})
    out = ARTICLE / 'figures'
    preview = ARTICLE / '.preview'
    out.mkdir(exist_ok=True)
    preview.mkdir(exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.8), layout='constrained')
    labels = ['A：ログインだけ', 'B：ログイン＋更新']
    for index, name in enumerate(['login-only', 'steady-mixed']):
        rows = data['comparison'][name]
        values_by_panel = [[row['flows']['login']['p99_ms'] for row in rows],
                           [row['resources']['keycloak']['cpu_median_pct'] for row in rows]]
        for ax, values in zip(axes, values_by_panel):
            ax.scatter([index-.04, index, index+.04], values, s=50,
                       color=['#2563eb', '#d97706'][index], zorder=3)
            ax.hlines(median(values), index-.16, index+.16, color='#222', linewidth=2)
            ax.annotate(f'中央値 {median(values):.0f}', (index, max(values)),
                        xytext=(0, 12), textcoords='offset points', ha='center')
    for ax in axes:
        ax.set_xticks([0, 1], labels)
        ax.set_xlim(-.5, 1.5)
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', alpha=.2)
    axes[0].set(title='ログインの応答時間は近い', ylabel='ログイン p99（ms）', ylim=(0, 110))
    axes[1].set(title='CPUの観測値には差がある', ylabel='CPU使用率（%）', ylim=(0, 225))
    axes[1].axhline(200, color='#777', linestyle='--', linewidth=1)
    axes[1].text(-.4, 204, '今回の上限：200%（2 CPU）', fontsize=10, color='#555')
    fig.suptitle('ログインは毎秒20回で固定。Bだけ毎秒100回の更新を追加', fontsize=14)
    fig.savefig(out/'reader-comparison.svg', metadata={'Date': None})
    fig.savefig(preview/'reader-comparison.png', dpi=180)
    plt.close(fig)
    exploration = next(row for row in data['exploration']['login'] if row['login_rate'] == 20)
    rows = [exploration] + data['comparison']['login-only']
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), layout='constrained')
    for ax, values, title, unit in [
        (axes[0], [r['flows']['login']['p99_ms'] for r in rows], 'ログインの応答時間', 'p99（ms）'),
        (axes[1], [r['resources']['keycloak']['cpu_median_pct'] for r in rows], 'CPUの観測値', 'CPU使用率の中央値（%）')]:
        ax.bar(['予備実験', 'A：1回目', 'A：2回目', 'A：3回目'], values,
               color=['#94a3b8', '#2563eb', '#2563eb', '#2563eb'], width=.55)
        for index, value in enumerate(values):
            ax.text(index, value + max(values)*.025, f'{value:g}', ha='center', fontsize=11)
        ax.set(title=title, ylabel=unit, ylim=(0, max(values)*1.25))
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', alpha=.2)
    fig.suptitle('同じ毎秒20ログインでも、予備実験では異なる値が出た\n原因は未解明。容量を判断する前に再確認が必要', fontsize=13)
    fig.savefig(out/'reader-discrepancy.svg', metadata={'Date': None})
    fig.savefig(preview/'reader-discrepancy.png', dpi=180)
    plt.close(fig)
    for path in out.glob('reader-*.svg'):
        path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines()) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--figures', action='store_true', help='Requires matplotlib; creates SVG and local PNG previews')
    parser.add_argument('--reader-figures', action='store_true', help='Create focused Japanese article figures')
    args = parser.parse_args()
    data = json.loads(SOURCE.read_text())
    audit = verify(data)
    ARTICLE.mkdir(exist_ok=True)
    (ARTICLE/'evidence-check.json').write_text(json.dumps(audit, indent=2) + '\n')
    (ARTICLE/'generated-results.md').write_text(tables(data))
    if args.figures:
        figures(data)
    if args.reader_figures:
        reader_figures(data)
    print('Verified all 12 published comparison trials; article tables generated. Raw measurements not replayed.')


if __name__ == '__main__':
    main()
