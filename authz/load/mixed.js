import http from 'k6/http';
import crypto from 'k6/crypto';
import encoding from 'k6/encoding';
import exec from 'k6/execution';
import {Counter, Rate, Trend} from 'k6/metrics';

const mode = __ENV.MODE || 'direct';
const apiRate = Number(__ENV.API_RATE || 25);
const loginRate = Number(__ENV.LOGIN_RATE || 1);
const refreshRate = Number(__ENV.REFRESH_RATE || 5);
const duration = Number(__ENV.DURATION || 180);
const warmup = Number(__ENV.WARMUP || 60);
const profile = __ENV.PROFILE || 'standard';
const baselineSeconds = Number(__ENV.BASELINE_SECONDS || 120);
const stressSeconds = Number(__ENV.STRESS_SECONDS || 60);
const recoverySeconds = Number(__ENV.RECOVERY_SECONDS || 120);
const stressApiRate = Number(__ENV.STRESS_API_RATE || apiRate * 2);
const shares = Number(__ENV.SHARES || 5);
const sessionCount = Number(__ENV.SESSIONS || 100);
if (!['direct', 'fga'].includes(mode)) throw Error('invalid_mode');
if (![5, 50].includes(shares)) throw Error('invalid_shares');

const apiBase = __ENV.API_URL || 'https://api:8443';
const keycloakBase = __ENV.KEYCLOAK_URL || 'https://keycloak:8443';
const issuer = `${keycloakBase}/realms/authz-lab`;
const endpoint = `${issuer}/protocol/openid-connect`;
const redirect = 'https://127.0.0.1:18446/callback';

const authzAttempts = new Counter('authz_attempts');
const authzCompleted = new Counter('authz_completed');
const expectedAllow = new Counter('authz_expected_allow');
const expectedDeny = new Counter('authz_expected_deny');
const overPermit = new Counter('authz_over_permit');
const underPermit = new Counter('authz_under_permit');
const authzUnexpected = new Rate('authz_unexpected');
const decisionCorrect = new Rate('authz_decision_correct');
const apiLatency = new Trend('authz_api_ms', true);
const jwtLatency = new Trend('authz_jwt_ms', true);
const dbLatency = new Trend('authz_db_ms', true);
const fgaLatency = new Trend('authz_fga_ms', true);
const oidcAttempts = new Counter('oidc_attempts');
const oidcCompleted = new Counter('oidc_completed');
const oidcErrors = new Counter('oidc_errors');
const oidcSuccess = new Rate('oidc_success');
const oidcLatency = new Trend('oidc_latency_ms', true);

const totalDuration = `${duration + warmup}s`;
function arrival(name, execName, rate, seconds, startTime, preAllocatedVUs, maxVUs) {
  const value = {
    executor: 'constant-arrival-rate', exec: execName, rate, timeUnit: '1s', duration: `${seconds}s`,
    preAllocatedVUs, maxVUs, gracefulStop: '10s',
  };
  if (startTime) value.startTime = `${startTime}s`;
  return value;
}
function scenarios() {
  const loginVUs = Math.max(10, loginRate * 4);
  const refreshVUs = Math.max(20, refreshRate * 2);
  if (profile === 'timeline') {
    const recoveryStart = baselineSeconds + stressSeconds;
    const total = recoveryStart + recoverySeconds;
    return {
      api_baseline_before: arrival('api_baseline_before', 'apiLoad', apiRate, baselineSeconds, 0, 200, 200),
      api_stress: arrival('api_stress', 'apiLoad', stressApiRate, stressSeconds, baselineSeconds, 200, 200),
      api_baseline_after: arrival('api_baseline_after', 'apiLoad', apiRate, recoverySeconds, recoveryStart, 200, 200),
      login: arrival('login', 'loginLoad', loginRate, total, 0, loginVUs, Math.max(20, loginRate * 8)),
      refresh: arrival('refresh', 'refreshLoad', refreshRate, total, 0, refreshVUs, Math.max(40, refreshRate * 4)),
    };
  }
  if (profile === 'fault') {
    const total = baselineSeconds + stressSeconds + recoverySeconds;
    return {
      api: arrival('api', 'apiLoad', apiRate, total, 0, 200, 200),
      login: arrival('login', 'loginLoad', loginRate, total, 0, loginVUs, Math.max(20, loginRate * 8)),
      refresh: arrival('refresh', 'refreshLoad', refreshRate, total, 0, refreshVUs, Math.max(40, refreshRate * 4)),
    };
  }
  return {
    api: arrival('api', 'apiLoad', apiRate, duration + warmup, 0, 200, 200),
    login: arrival('login', 'loginLoad', loginRate, duration + warmup, 0, loginVUs, Math.max(20, loginRate * 8)),
    refresh: arrival('refresh', 'refreshLoad', refreshRate, duration + warmup, 0, refreshVUs, Math.max(40, refreshRate * 4)),
  };
}
export const options = {
  setupTimeout: '20m',
  scenarios: scenarios(),
  thresholds: {
    'authz_attempts{phase:measure}': ['count>0'],
    'authz_expected_allow{phase:measure}': ['count>0'],
    'authz_expected_deny{phase:measure}': ['count>0'],
    'authz_decision_correct{phase:measure}': ['rate>=0.999'],
    'authz_api_ms{phase:measure}': ['p(99)<300'],
    'authz_api_ms{phase:measure,expected:allow}': ['p(99)<300'],
    'authz_api_ms{phase:measure,expected:deny}': ['p(99)<300'],
    'authz_over_permit{phase:measure}': ['count==0'],
    'authz_unexpected{phase:measure}': ['rate<=0.001'],
    'oidc_attempts{flow:login,phase:measure}': ['count>0'],
    'oidc_attempts{flow:refresh,phase:measure}': ['count>0'],
    'oidc_completed{flow:login,phase:measure}': ['count>=0'],
    'oidc_completed{flow:refresh,phase:measure}': ['count>=0'],
    'oidc_success{flow:login,phase:measure}': ['rate>=0.99'],
    'oidc_success{flow:refresh,phase:measure}': ['rate>=0.99'],
    'oidc_latency_ms{flow:login,phase:measure}': ['p(99)<2000'],
    'oidc_latency_ms{flow:refresh,phase:measure}': ['p(99)<500'],
    dropped_iterations: ['count==0'],
  },
  systemTags: ['method', 'name', 'status', 'scenario', 'expected_response'],
  summaryTrendStats: ['avg', 'med', 'p(50)', 'p(95)', 'p(99)', 'max', 'count'],
};

function params(name) {
  return {redirects: 0, timeout: '10s', responseCallback: http.expectedStatuses(200, 302), tags: {name}};
}
function query(values) {
  return Object.entries(values).map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`).join('&');
}
function failure(kind) { throw Error(kind); }
function body(response) {
  if (response.status !== 200) failure(`http_${response.status || 0}`);
  try { return response.json(); } catch (_) { failure('invalid_json'); }
}
function decodeJWT(token) {
  try { return JSON.parse(encoding.b64decode(token.split('.')[1], 'rawurl', 's')); }
  catch (_) { failure('invalid_id_token'); }
}
function tokens(response, nonce) {
  const value = body(response);
  if (!value.access_token || !value.refresh_token || String(value.token_type).toLowerCase() !== 'bearer') failure('missing_tokens');
  if (nonce) {
    const claims = decodeJWT(value.id_token || '');
    const audienceOk = claims.aud === 'load-client' || (Array.isArray(claims.aud) && claims.aud.includes('load-client'));
    if (claims.iss !== issuer || !audienceOk || claims.nonce !== nonce || claims.exp * 1000 <= Date.now()) failure('id_claims');
  }
  return value;
}
function login(user) {
  http.cookieJar().clear(keycloakBase);
  const verifier = encoding.b64encode(crypto.randomBytes(32), 'rawurl');
  const challenge = encoding.b64encode(crypto.sha256(verifier, 'binary'), 'rawurl');
  const state = encoding.b64encode(crypto.randomBytes(16), 'rawurl');
  const nonce = encoding.b64encode(crypto.randomBytes(16), 'rawurl');
  const auth = http.get(`${endpoint}/auth?${query({
    client_id: 'load-client', response_type: 'code', scope: 'openid', redirect_uri: redirect,
    state, nonce, code_challenge: challenge, code_challenge_method: 'S256', prompt: 'login',
  })}`, params('authorize'));
  if (auth.status !== 200) failure('authorize_http');
  const action = auth.html().find('form#kc-form-login').attr('action');
  if (!action || !action.startsWith(`${keycloakBase}/realms/authz-lab/login-actions/`)) failure('login_form');
  const submitted = http.post(action, {
    username: `user-${String(user).padStart(5, '0')}`, password: __ENV.LAB_PASSWORD, credentialId: '',
  }, params('credentials'));
  if (submitted.status !== 302) failure('credentials_http');
  const location = submitted.headers.Location || '';
  if (!location.startsWith(`${redirect}?`)) failure('callback_origin');
  const fields = {};
  for (const part of location.slice(location.indexOf('?') + 1).split('&')) {
    const at = part.indexOf('=');
    if (at > 0) fields[decodeURIComponent(part.slice(0, at))] = decodeURIComponent(part.slice(at + 1));
  }
  if (fields.state !== state || !fields.code || fields.error) failure('callback_state');
  return tokens(http.post(`${endpoint}/token`, {
    grant_type: 'authorization_code', client_id: 'load-client', redirect_uri: redirect,
    code: fields.code, code_verifier: verifier,
  }, params('code_exchange')), nonce);
}
function refresh(token) {
  return tokens(http.post(`${endpoint}/token`, {
    grant_type: 'refresh_token', client_id: 'load-client', refresh_token: token,
  }, params('refresh')));
}
function directTokenRequest(index) {
  return {
    method: 'POST', url: `${endpoint}/token`,
    body: {grant_type: 'password', client_id: 'load-client', username: `user-${String(index).padStart(5, '0')}`, password: __ENV.LAB_PASSWORD},
    params: {headers: {'Content-Type': 'application/x-www-form-urlencoded'}, timeout: '10s', responseCallback: http.expectedStatuses(200), tags: {name: 'token_setup'}},
  };
}

export function setup() {
  if (!__ENV.LAB_PASSWORD) failure('missing_lab_password');
  const discovery = body(http.get(`${issuer}/.well-known/openid-configuration`, params('discovery')));
  if (discovery.issuer !== issuer || discovery.token_endpoint !== `${endpoint}/token`) failure('discovery');
  const apiTokens = new Array(1000);
  for (let start = 0; start < 1000; start += 25) {
    const responses = http.batch(Array.from({length: Math.min(25, 1000 - start)}, (_, offset) => directTokenRequest(start + offset)));
    for (let offset = 0; offset < responses.length; offset++) {
      if (responses[offset].status !== 200) failure('token_setup_failed');
      apiTokens[start + offset] = responses[offset].json('access_token');
      if (!apiTokens[start + offset]) failure('token_missing');
    }
  }
  const refreshTokens = [];
  for (let index = 0; index < sessionCount; index++) refreshTokens.push(login(index).refresh_token);
  return {apiTokens, refreshTokens};
}

function phase() {
  return profile !== 'standard' || Date.now() - exec.scenario.startTime >= warmup * 1000 ? 'measure' : 'warmup';
}
function period() {
  if (profile === 'timeline') return exec.scenario.name.replace('api_', '');
  return profile;
}
function timing(response, name) {
  const header = response.headers['Server-Timing'] || '';
  const match = new RegExp(`${name};dur=([0-9.]+)`).exec(header);
  return match ? Number(match[1]) : 0;
}
export function apiLoad(data) {
  const currentPhase = phase();
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
  const tags = {phase: currentPhase, expected, operation, mode, period: period()};
  authzAttempts.add(1, tags);
  if (expected === 'allow') expectedAllow.add(1, tags); else expectedDeny.add(1, tags);
  const requestParams = {
    headers: {Authorization: `Bearer ${data.apiTokens[user]}`, 'Content-Type': 'application/json'},
    timeout: '2s', responseCallback: http.expectedStatuses(200, 403),
    tags: {name: `album_${operation}`, expected, phase: currentPhase, mode, period: period()},
  };
  const response = operation === 'edit'
    ? http.put(`${apiBase}/albums/${album}?mode=${mode}`, JSON.stringify({name: 'Synthetic load update'}), requestParams)
    : http.get(`${apiBase}/albums/${album}?mode=${mode}`, requestParams);
  if (response.status > 0) authzCompleted.add(1, tags);
  const expectedStatus = expected === 'allow' ? 200 : 403;
  const isCorrect = response.status === expectedStatus;
  const over = expected === 'deny' && response.status === 200;
  const under = expected === 'allow' && response.status === 403;
  const isUnexpected = response.status === 0 || response.status >= 500 || (!isCorrect && !over && !under);
  decisionCorrect.add(isCorrect, tags);
  authzUnexpected.add(isUnexpected, tags);
  if (over) overPermit.add(1, tags);
  if (under) underPermit.add(1, tags);
  apiLatency.add(response.timings.duration, tags);
  jwtLatency.add(timing(response, 'jwt'), tags);
  dbLatency.add(timing(response, 'db'), tags);
  fgaLatency.add(timing(response, 'fga'), tags);
}

function measureOidc(flow, operation) {
  const tags = {flow, phase: phase(), period: profile};
  oidcAttempts.add(1, tags);
  const started = Date.now();
  let ok = false;
  try { operation(); ok = true; }
  catch (error) {
    const safeReason = /^(http_[0-9]{1,3}|invalid_json|missing_tokens|invalid_id_token|id_claims|authorize_http|login_form|credentials_http|callback_origin|callback_state|session_lost|session_assignment)$/.test(error.message)
      ? error.message : 'flow_failed';
    oidcErrors.add(1, {...tags, reason: safeReason});
  }
  oidcLatency.add(Date.now() - started, tags);
  oidcSuccess.add(ok, tags);
  if (ok) oidcCompleted.add(1, tags);
}
export function loginLoad() {
  measureOidc('login', () => login((sessionCount + exec.scenario.iterationInTest) % 1000));
}
let currentRefreshToken;
let refreshBroken = false;
export function refreshLoad(data) {
  measureOidc('refresh', () => {
    const sessionIndex = (exec.vu.idInTest - 1) % data.refreshTokens.length;
    if (refreshBroken && profile === 'fault') {
      // A client whose rotating token outcome is uncertain must establish a new
      // code-flow session; it must never replay the old refresh token.
      currentRefreshToken = login(sessionIndex).refresh_token;
      refreshBroken = false;
    }
    if (refreshBroken) failure('session_lost');
    if (!currentRefreshToken) currentRefreshToken = data.refreshTokens[sessionIndex];
    if (!currentRefreshToken) failure('session_assignment');
    try { currentRefreshToken = refresh(currentRefreshToken).refresh_token; }
    catch (error) { refreshBroken = true; throw error; }
  });
}

export function handleSummary(data) {
  const metrics = {};
  for (const [name, value] of Object.entries(data.metrics)) {
    if (name.startsWith('authz_') || name.startsWith('oidc_') || name === 'dropped_iterations') metrics[name] = value;
  }
  return {
    '/results/summary.json': JSON.stringify({state: data.state, metrics}, null, 2),
    stdout: JSON.stringify({finished: true, metric_count: Object.keys(metrics).length}) + '\n',
  };
}
