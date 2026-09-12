import http from 'k6/http';
import crypto from 'k6/crypto';
import encoding from 'k6/encoding';
import exec from 'k6/execution';
import {Counter, Rate, Trend} from 'k6/metrics';
import {configuration} from './config.mjs';

const cfg = configuration(__ENV);
const base = 'http://keycloak:8080';
const issuer = `${base}/realms/oidc-lab`;
const endpoint = `${issuer}/protocol/openid-connect`;
const redirect = 'http://127.0.0.1:18081/callback';
const attempts = new Counter('lab_attempts');
const completed = new Counter('lab_completed');
const errors = new Counter('lab_errors');
const success = new Rate('lab_success');
const latency = new Trend('lab_latency_ms', true);
const successLatency = new Trend('lab_success_latency_ms', true);
const thresholds = {dropped_iterations: ['count==0']};
for (const flow of cfg.mode === 'smoke' ? ['smoke'] : ['login', 'refresh'].filter(f => f === 'login' ? cfg.hasLogin : cfg.hasRefresh)) {
  thresholds[`lab_success{flow:${flow},phase:measure}`] = ['rate>=0.99'];
  if (flow !== 'smoke') thresholds[`lab_latency_ms{flow:${flow},phase:measure}`] = [`p(99)<${flow === 'login' ? 2000 : 500}`];
}
export const options = {
  scenarios: cfg.scenarios, thresholds, setupTimeout: '20m',
  // URL, error text and group tags can contain authorization codes or session IDs.
  systemTags: ['method', 'name', 'status', 'scenario', 'expected_response'],
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};
function params(name) { return {redirects: 0, timeout: '10s', tags: {name}}; }
function query(values) { return Object.entries(values).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&'); }
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
  const t = body(response);
  if (!t.access_token || !t.refresh_token || String(t.token_type).toLowerCase() !== 'bearer') failure('missing_tokens');
  if (nonce) {
    const claims = decodeJWT(t.id_token || '');
    if (claims.iss !== issuer || claims.aud !== 'load-client' || claims.nonce !== nonce || claims.exp * 1000 <= Date.now()) failure('id_claims');
  }
  return t;
}
function login(user) {
  http.cookieJar().clear(base);
  const verifier = encoding.b64encode(crypto.randomBytes(32), 'rawurl');
  const challenge = encoding.b64encode(crypto.sha256(verifier, 'binary'), 'rawurl');
  const state = crypto.randomBytes(16);
  const stateText = encoding.b64encode(state, 'rawurl');
  const nonce = encoding.b64encode(crypto.randomBytes(16), 'rawurl');
  const auth = http.get(`${endpoint}/auth?${query({client_id: 'load-client', response_type: 'code', scope: 'openid', redirect_uri: redirect, state: stateText, nonce, code_challenge: challenge, code_challenge_method: 'S256', prompt: 'login'})}`, params('authorize'));
  if (auth.status !== 200) failure('authorize_http');
  const action = auth.html().find('form#kc-form-login').attr('action');
  if (!action || !action.startsWith(`${base}/realms/oidc-lab/login-actions/`)) failure('login_form');
  const submitted = http.post(action, {username: `user-${String(user).padStart(5, '0')}`, password: __ENV.LAB_PASSWORD, credentialId: ''}, params('credentials'));
  if (submitted.status !== 302) failure('credentials_http');
  const location = submitted.headers.Location || '';
  if (!location.startsWith(`${redirect}?`)) failure('callback_origin');
  const fields = {};
  for (const part of location.slice(location.indexOf('?') + 1).split('&')) {
    const at = part.indexOf('=');
    fields[decodeURIComponent(part.slice(0, at))] = decodeURIComponent(part.slice(at + 1));
  }
  if (fields.state !== stateText || !fields.code || fields.error) failure('callback_state');
  return tokens(http.post(`${endpoint}/token`, {grant_type: 'authorization_code', client_id: 'load-client', redirect_uri: redirect, code: fields.code, code_verifier: verifier}, params('code_exchange')), nonce);
}
function refresh(token) {
  return tokens(http.post(`${endpoint}/token`, {grant_type: 'refresh_token', client_id: 'load-client', refresh_token: token}, params('refresh')));
}
export function setup() {
  if (!__ENV.LAB_PASSWORD) failure('missing_lab_password');
  const discovery = body(http.get(`${issuer}/.well-known/openid-configuration`, params('discovery')));
  if (discovery.issuer !== issuer || discovery.token_endpoint !== `${endpoint}/token`) failure('discovery');
  const sessions = [];
  // Seed identical session counts even for login-only runs, so mixed comparisons have equal initial state.
  for (let i = 0; i < cfg.sessions; i++) sessions.push(login(i).refresh_token);
  return {sessions}; // In-memory only. Never exported by handleSummary.
}
let rt;
let broken = false;
function measure(flow, operation) {
  const phase = cfg.mode === 'smoke' || Date.now() - exec.scenario.startTime >= cfg.warmup * 1000 ? 'measure' : 'warmup';
  const tags = {flow, phase};
  attempts.add(1, tags);
  const begin = Date.now();
  let ok = false;
  try { operation(); ok = true; }
  catch (e) {
    // Fixed classification only: never emit exception messages, responses or credentials.
    const reason = /^(http_[0-9]{1,3}|invalid_json|missing_tokens|invalid_id_token|id_claims|authorize_http|login_form|credentials_http|callback_origin|callback_state|session_lost|session_assignment)$/.test(e.message) ? e.message : 'flow_failed';
    errors.add(1, {...tags, reason});
  }
  const elapsed = Date.now() - begin;
  latency.add(elapsed, tags);
  success.add(ok, tags);
  if (ok) { completed.add(1, tags); successLatency.add(elapsed, tags); }
}
export function loginLoad() {
  measure('login', () => login(cfg.sessions + exec.scenario.iterationInTest % (cfg.users - cfg.sessions)));
}
export function refreshLoad(data) {
  measure('refresh', () => {
    if (broken) failure('session_lost');
    if (!rt) rt = data.sessions[exec.vu.idInTest - 1];
    if (!rt) failure('session_assignment');
    try { rt = refresh(rt).refresh_token; }
    catch (e) { broken = true; throw e; } // Never hide failure by re-login or reuse an uncertain rotated token.
  });
}
export function smoke() { measure('smoke', () => { const t = login(0); refresh(t.refresh_token); }); }
export function handleSummary(data) {
  const metrics = {};
  for (const [name, value] of Object.entries(data.metrics)) {
    if (name.startsWith('lab_') || ['dropped_iterations', 'http_reqs', 'http_req_failed', 'iteration_duration'].includes(name)) metrics[name] = {type: value.type, values: value.values, thresholds: value.thresholds};
  }
  return {'/results/summary.json': JSON.stringify({config: cfg, metrics}, null, 2), stdout: 'Local run finished. Aggregate summary saved; inspect thresholds and dropped iterations.\n'};
}
