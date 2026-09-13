import http from 'k6/http';
import exec from 'k6/execution';
import {Counter, Rate, Trend} from 'k6/metrics';

const mode = __ENV.MODE || 'direct';
const rate = Number(__ENV.RATE || 10);
const duration = Number(__ENV.DURATION || 180);
const warmup = Number(__ENV.WARMUP || 60);
const vus = Number(__ENV.VUS || 200);
const shares = Number(__ENV.SHARES || 5);
if (!['direct', 'fga'].includes(mode)) throw Error('invalid_mode');
if (![5, 50].includes(shares)) throw Error('invalid_shares');

const apiBase = __ENV.API_URL || 'https://api:8443';
const keycloakBase = __ENV.KEYCLOAK_URL || 'https://keycloak:8443';
const issuer = `${keycloakBase}/realms/authz-lab`;
const tokenEndpoint = `${issuer}/protocol/openid-connect/token`;

const attempts = new Counter('authz_attempts');
const completed = new Counter('authz_completed');
const expectedAllow = new Counter('authz_expected_allow');
const expectedDeny = new Counter('authz_expected_deny');
const overPermit = new Counter('authz_over_permit');
const underPermit = new Counter('authz_under_permit');
const unexpected = new Rate('authz_unexpected');
const correct = new Rate('authz_decision_correct');
const apiLatency = new Trend('authz_api_ms', true);
const jwtLatency = new Trend('authz_jwt_ms', true);
const dbLatency = new Trend('authz_db_ms', true);
const fgaLatency = new Trend('authz_fga_ms', true);

export const options = {
  setupTimeout: '20m',
  scenarios: {
    api: {
      executor: 'constant-arrival-rate',
      exec: 'apiLoad',
      rate,
      timeUnit: '1s',
      duration: `${duration + warmup}s`,
      preAllocatedVUs: vus,
      maxVUs: vus,
      gracefulStop: '10s',
    },
  },
  thresholds: {
    'authz_attempts{phase:measure}': ['count>0'],
    'authz_completed{phase:measure}': ['count>=0'],
    'authz_expected_allow{phase:measure}': ['count>0'],
    'authz_expected_deny{phase:measure}': ['count>0'],
    'authz_decision_correct{phase:measure}': ['rate>=0.999'],
    'authz_api_ms{phase:measure}': ['p(99)<300'],
    'authz_api_ms{phase:measure,expected:allow}': ['p(99)<300'],
    'authz_api_ms{phase:measure,expected:deny}': ['p(99)<300'],
    'authz_jwt_ms{phase:measure}': ['p(99)<300'],
    'authz_db_ms{phase:measure}': ['p(99)<300'],
    'authz_fga_ms{phase:measure}': ['p(99)<300'],
    'authz_over_permit{phase:measure}': ['count==0'],
    'authz_under_permit{phase:measure}': ['count>=0'],
    'authz_unexpected{phase:measure}': ['rate<=0.001'],
    dropped_iterations: ['count==0'],
  },
  systemTags: ['method', 'name', 'status', 'scenario', 'expected_response'],
  summaryTrendStats: ['avg', 'med', 'p(50)', 'p(95)', 'p(99)', 'max', 'count'],
};

function tokenParams() {
  return {
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    timeout: '10s',
    responseCallback: http.expectedStatuses(200),
    tags: {name: 'token_setup'},
  };
}

export function setup() {
  if (!__ENV.LAB_PASSWORD) throw Error('missing_password');
  const discovery = http.get(`${issuer}/.well-known/openid-configuration`, {timeout: '10s', tags: {name: 'discovery'}});
  if (discovery.status !== 200 || discovery.json('issuer') !== issuer) throw Error('discovery_failed');
  const tokens = new Array(1000);
  for (let start = 0; start < 1000; start += 25) {
    const batch = [];
    for (let i = start; i < Math.min(1000, start + 25); i++) {
      batch.push({
        method: 'POST',
        url: tokenEndpoint,
        body: {
          grant_type: 'password',
          client_id: 'load-client',
          username: `user-${String(i).padStart(5, '0')}`,
          password: __ENV.LAB_PASSWORD,
        },
        params: tokenParams(),
      });
    }
    const responses = http.batch(batch);
    for (let j = 0; j < responses.length; j++) {
      if (responses[j].status !== 200) throw Error('token_setup_failed');
      const value = responses[j].json('access_token');
      if (!value) throw Error('token_missing');
      tokens[start + j] = value;
    }
  }
  return {tokens};
}

function timing(response, name) {
  const header = response.headers['Server-Timing'] || '';
  const match = new RegExp(`${name};dur=([0-9.]+)`).exec(header);
  return match ? Number(match[1]) : 0;
}

export function apiLoad(data) {
  const phase = Date.now() - exec.scenario.startTime >= warmup * 1000 ? 'measure' : 'warmup';
  const iteration = exec.scenario.iterationInTest;
  const kind = iteration % 10;
  const album = Math.floor(iteration / 10) % 1000 + 1;
  const owner = album - 1;
  const viewer = (owner + 1) % 1000;
  let user = owner;
  let operation = 'view';
  let expected = 'allow';
  if (kind === 4) operation = 'edit';
  else if (kind >= 5 && kind <= 7) user = viewer;
  else if (kind === 8) { user = viewer; operation = 'edit'; expected = 'deny'; }
  else if (kind === 9) { user = (owner + shares + 100) % 1000; expected = 'deny'; }
  const tags = {phase, expected, operation, mode};
  attempts.add(1, tags);
  if (expected === 'allow') expectedAllow.add(1, tags); else expectedDeny.add(1, tags);
  const params = {
    headers: {Authorization: `Bearer ${data.tokens[user]}`, 'Content-Type': 'application/json'},
    timeout: '2s',
    responseCallback: http.expectedStatuses(200, 403),
    tags: {name: `album_${operation}`, expected, phase, mode},
  };
  const response = operation === 'edit'
    ? http.put(`${apiBase}/albums/${album}?mode=${mode}`, JSON.stringify({name: 'Synthetic load update'}), params)
    : http.get(`${apiBase}/albums/${album}?mode=${mode}`, params);
  if (response.status > 0) completed.add(1, tags);
  const expectedStatus = expected === 'allow' ? 200 : 403;
  const isCorrect = response.status === expectedStatus;
  const over = expected === 'deny' && response.status === 200;
  const under = expected === 'allow' && response.status === 403;
  const isUnexpected = response.status === 0 || response.status >= 500 || (!isCorrect && !over && !under);
  correct.add(isCorrect, tags);
  unexpected.add(isUnexpected, tags);
  if (over) overPermit.add(1, tags);
  if (under) underPermit.add(1, tags);
  apiLatency.add(response.timings.duration, tags);
  jwtLatency.add(timing(response, 'jwt'), tags);
  dbLatency.add(timing(response, 'db'), tags);
  fgaLatency.add(timing(response, 'fga'), tags);
}

export function handleSummary(data) {
  return {
    '/results/summary.json': JSON.stringify(data),
    stdout: JSON.stringify({state: data.state, metrics: Object.keys(data.metrics).length}) + '\n',
  };
}
