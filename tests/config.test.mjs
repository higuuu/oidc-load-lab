import test from 'node:test';
import assert from 'node:assert/strict';
import {configuration} from '../load/config.mjs';

test('equal refresh work for steady and burst, equal seeded sessions across comparisons', () => {
  const normal = configuration({MODE: 'mixed'});
  const burst = configuration({MODE: 'mixed-burst'});
  const stages = burst.scenarios.refresh.stages.slice(1);
  let last = burst.refreshRate, area = 0;
  for (const stage of stages) { area += (last + stage.target) / 2 * parseInt(stage.duration); last = stage.target; }
  assert.equal(area, normal.duration * normal.refreshRate);
  for (const mode of ['login', 'refresh', 'mixed', 'refresh-burst', 'mixed-burst']) {
    const c = configuration({MODE: mode});
    assert.equal(c.sessions, normal.sessions);
    assert.ok(c.sessions < c.users);
    assert.ok(Object.values(c.scenarios).reduce((n, s) => n + s.maxVUs, 0) <= c.sessions);
  }
});
test('invalid scenarios cannot silently run a different load', () => {
  for (const env of [{MODE: 'other'}, {MODE: 'mixed-burst', SECONDS: '61'}, {VUS: '251'}, {LOGIN_RATE: 'NaN'}, {SECONDS: '0'}]) assert.throws(() => configuration(env));
});
