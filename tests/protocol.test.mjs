// Mock transport tests: verify flow mechanics, not Keycloak compatibility or performance.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import nodeCrypto from 'node:crypto';
import {configuration} from '../load/config.mjs';

function harness(badState = false) {
  const records = [], tokensSeen = [];
  let authQuery, serial = 0;
  const crypto = {
    randomBytes: n => nodeCrypto.randomBytes(n),
    sha256: s => nodeCrypto.createHash('sha256').update(s).digest(),
  };
  const encoding = {
    b64encode: b => Buffer.from(b).toString('base64url'),
    b64decode: s => Buffer.from(s, 'base64url').toString(),
  };
  class Metric { constructor(name) { this.name = name; } add(value, tags) { records.push({name: this.name, value, tags}); } }
  const makeTokens = () => ({status: 200, json: () => ({access_token: 'synthetic-access', refresh_token: `synthetic-refresh-${++serial}`, token_type: 'Bearer', id_token: ['header', Buffer.from(JSON.stringify({iss: 'http://keycloak:8080/realms/oidc-lab', aud: 'load-client', nonce: authQuery.get('nonce'), exp: Date.now() / 1000 + 300})).toString('base64url'), 'signature'].join('.')})});
  const http = {
    cookieJar: () => ({clear() {}}),
    get(url) {
      if (url.endsWith('openid-configuration')) return {status: 200, json: () => ({issuer: 'http://keycloak:8080/realms/oidc-lab', token_endpoint: 'http://keycloak:8080/realms/oidc-lab/protocol/openid-connect/token'})};
      authQuery = new URL(url).searchParams;
      assert.equal(authQuery.get('code_challenge_method'), 'S256');
      return {status: 200, html: () => ({find: () => ({attr: () => 'http://keycloak:8080/realms/oidc-lab/login-actions/authenticate'})})};
    },
    post(url, data) {
      if (url.includes('login-actions')) return {status: 302, headers: {Location: 'http://127.0.0.1:18081/callback?code=synthetic-code&state=' + (badState ? 'wrong' : authQuery.get('state'))}};
      if (data.grant_type === 'authorization_code') {
        assert.equal(encoding.b64encode(crypto.sha256(data.code_verifier)), authQuery.get('code_challenge'));
        return makeTokens();
      }
      assert.equal(data.grant_type, 'refresh_token');
      tokensSeen.push(data.refresh_token);
      return makeTokens();
    },
  };
  let source = fs.readFileSync(new URL('../load/oidc.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace(/export /g, '');
  source += '\nthis.api = {setup, smoke, refreshLoad, handleSummary};';
  const context = {http, crypto, encoding, exec: {vu: {idInTest: 1}, scenario: {startTime: 0}}, Counter: Metric, Rate: Metric, Trend: Metric, configuration, __ENV: {MODE: 'refresh', VUS: '2', LAB_PASSWORD: 'synthetic-test-only'}};
  vm.createContext(context);
  vm.runInContext(source, context);
  return {...context.api, records, tokensSeen};
}
test('Code + S256 PKCE then stateful refresh uses returned rotated token', () => {
  const h = harness();
  const data = h.setup();
  assert.equal(data.sessions.length, 4);
  h.refreshLoad(data);
  h.refreshLoad(data);
  assert.equal(h.tokensSeen[0], data.sessions[0]);
  assert.notEqual(h.tokensSeen[1], h.tokensSeen[0]);
  assert.equal(h.records.filter(r => r.name === 'lab_success').every(r => r.value), true);
});
test('state mismatch never exchanges authorization code or records success', () => {
  const h = harness(true);
  h.smoke();
  assert.equal(h.records.find(r => r.name === 'lab_success').value, false);
  assert.equal(h.tokensSeen.length, 0);
});
