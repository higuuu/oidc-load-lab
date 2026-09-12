export function configuration(env) {
  const mode = env.MODE || 'smoke';
  if (!['smoke', 'login', 'refresh', 'mixed', 'refresh-burst', 'mixed-burst'].includes(mode)) throw Error('Unknown mode');
  function number(key, fallback, min, max) {
    const n = Number(env[key] || fallback);
    if (!Number.isInteger(n) || n < min || n > max) throw Error(`Invalid ${key}`);
    return n;
  }
  const rate = number('LOGIN_RATE', 5, 1, 500);
  const refreshRate = number('REFRESH_RATE', 20, 1, 2000);
  const duration = number('SECONDS', 180, 60, 1800);
  const vus = number('VUS', 100, 2, 250);
  const users = number('USERS', 1000, 1000, 1000);
  const warmup = 60;
  const scenarios = {};
  const common = {timeUnit: '1s', preAllocatedVUs: vus, maxVUs: vus, gracefulStop: '35s'};
  const hasLogin = mode === 'login' || mode.startsWith('mixed');
  const hasRefresh = mode.startsWith('refresh') || mode.startsWith('mixed');
  if (hasLogin) scenarios.login = {...common, executor: 'constant-arrival-rate', exec: 'loginLoad', rate, duration: `${duration + warmup}s`};
  if (hasRefresh) {
    if (mode.endsWith('burst')) {
      if (duration % 60) throw Error('Burst SECONDS must be a multiple of 60');
      const stages = [{duration: `${warmup}s`, target: refreshRate}];
      // Each measured minute: 20 -> 40 -> 20 -> 0 -> 20 (mean 20).
      for (let i = 0; i < duration / 60; i++) stages.push(
        {duration: '15s', target: 2 * refreshRate}, {duration: '15s', target: refreshRate},
        {duration: '15s', target: 0}, {duration: '15s', target: refreshRate});
      scenarios.refresh = {...common, executor: 'ramping-arrival-rate', exec: 'refreshLoad', startRate: refreshRate, stages};
    } else scenarios.refresh = {...common, executor: 'constant-arrival-rate', exec: 'refreshLoad', rate: refreshRate, duration: `${duration + warmup}s`};
  }
  if (mode === 'smoke') scenarios.smoke = {executor: 'shared-iterations', vus: 1, iterations: 1, exec: 'smoke', maxDuration: '60s'};
  // idInTest is unique across scenarios. Seed one independent session for every possible VU.
  const sessions = mode === 'smoke' ? 0 : vus * 2;
  return {mode, rate, refreshRate, duration, vus, users, warmup, scenarios, sessions, hasLogin, hasRefresh};
}
