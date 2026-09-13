#!/usr/bin/env python3
"""Mac-mini-only authorization experiment runner.

Secrets, tokens, dumps, and raw logs stay under ignored paths. Commands emit only
fixed status text and aggregate results suitable for review.
"""

import argparse
import base64
import json
import os
import pathlib
import secrets
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".authz-runtime"
ENV_FILE = RUNTIME / ".env"
CERTS = RUNTIME / "certs"
IMPORT = RUNTIME / "import"
RAW = ROOT / "results" / "authz-raw"
COMPOSE = ["docker", "compose", "--env-file", str(ENV_FILE), "-f", str(ROOT / "authz" / "compose.yaml")]
CA = CERTS / "ca.crt"


def command(args, *, input_text=None, capture=False, check=True, timeout=None):
    return subprocess.run(
        args,
        cwd=ROOT,
        input=input_text,
        text=True,
        capture_output=capture,
        check=check,
        timeout=timeout,
    )


def env_values():
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def set_env(updates):
    values = env_values()
    values.update(updates)
    ENV_FILE.write_text("".join(f"{key}={values[key]}\n" for key in sorted(values)))
    ENV_FILE.chmod(0o600)


def user_subject(index):
    return f"10000000-0000-4000-8000-{index:012d}"


def realm(password):
    audience_mapper = {
        "name": "album-api-audience",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "consentRequired": False,
        "config": {
            "included.client.audience": "album-api",
            "id.token.claim": "false",
            "access.token.claim": "true",
            "introspection.token.claim": "true",
        },
    }
    users = []
    for i in range(1000):
        users.append(
            {
                "id": user_subject(i),
                "username": f"user-{i:05d}",
                "firstName": "Synthetic",
                "lastName": f"User {i:05d}",
                "email": f"user-{i:05d}@example.invalid",
                "enabled": True,
                "emailVerified": True,
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            }
        )
    common = {
        "publicClient": True,
        "directAccessGrantsEnabled": True,
        "standardFlowEnabled": True,
        "redirectUris": ["https://127.0.0.1:18446/callback"],
        "webOrigins": [],
    }
    return {
        "realm": "authz-lab",
        "enabled": True,
        "sslRequired": "external",
        "accessTokenLifespan": 7200,
        "ssoSessionIdleTimeout": 14400,
        "clients": [
            {**common, "clientId": "load-client", "protocolMappers": [audience_mapper]},
            {
                **common,
                "clientId": "short-client",
                "attributes": {"access.token.lifespan": "1"},
                "protocolMappers": [audience_mapper],
            },
            {**common, "clientId": "wrong-audience", "protocolMappers": []},
        ],
        "users": users,
    }


def create_cert(name, sans):
    key = CERTS / f"{name}.key"
    csr = CERTS / f"{name}.csr"
    crt = CERTS / f"{name}.crt"
    ext = CERTS / f"{name}.ext"
    ext.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n"
        "subjectAltName=" + ",".join(sans) + "\n"
        "extendedKeyUsage=serverAuth\n"
    )
    command(["openssl", "genrsa", "-out", str(key), "2048"], capture=True)
    command(["openssl", "req", "-new", "-key", str(key), "-out", str(csr), "-subj", f"/CN={name}"], capture=True)
    command(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(CA),
            "-CAkey",
            str(CERTS / "ca.key"),
            "-CAserial",
            str(CERTS / "ca.srl"),
            "-CAcreateserial",
            "-out",
            str(crt),
            "-days",
            "7",
            "-sha256",
            "-extfile",
            str(ext),
        ],
        capture=True,
    )
    key.chmod(0o600)


def generate_certs():
    CERTS.mkdir(parents=True, exist_ok=True)
    command(["openssl", "genrsa", "-out", str(CERTS / "ca.key"), "2048"], capture=True)
    command(
        [
            "openssl",
            "req",
            "-x509",
            "-new",
            "-nodes",
            "-key",
            str(CERTS / "ca.key"),
            "-sha256",
            "-days",
            "7",
            "-out",
            str(CA),
            "-subj",
            "/CN=OIDC Authz Lab Local CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-addext",
            "subjectKeyIdentifier=hash",
        ],
        capture=True,
    )
    (CERTS / "ca.key").chmod(0o600)
    common = ["DNS:localhost", "IP:127.0.0.1"]
    create_cert("keycloak", ["DNS:keycloak", *common])
    create_cert("api", ["DNS:api", *common])
    create_cert("openfga", ["DNS:openfga", *common])


def init():
    if ENV_FILE.exists():
        print("Already initialized; existing ignored credentials preserved.")
        return
    CERTS.mkdir(parents=True)
    IMPORT.mkdir(parents=True)
    RAW.mkdir(parents=True)
    values = {
        "APP_DB_PASSWORD": secrets.token_hex(24),
        "AUTH_DB_PASSWORD": secrets.token_hex(24),
        "AUTHZ_DB_PASSWORD": secrets.token_hex(24),
        "KC_ADMIN_PASSWORD": secrets.token_hex(24),
        "KC_ADMIN_USER": "local-admin",
        "LAB_PASSWORD": secrets.token_urlsafe(24),
        "OBSERVER_TOKEN": secrets.token_hex(24),
        "OPENFGA_MODEL_ID": "not-initialized",
        "OPENFGA_PRESHARED_KEY": secrets.token_hex(32),
        "OPENFGA_STORE_ID": "not-initialized",
        "POSTGRES_PASSWORD": secrets.token_hex(24),
    }
    ENV_FILE.write_text("".join(f"{key}={values[key]}\n" for key in sorted(values)))
    ENV_FILE.chmod(0o600)
    generate_certs()
    (IMPORT / "realm.json").write_text(json.dumps(realm(values["LAB_PASSWORD"]), separators=(",", ":")))
    (IMPORT / "realm.json").chmod(0o600)
    print("Initialized ignored credentials, local CA, service certificates, and 1000 synthetic users.")


def ssl_context():
    return ssl.create_default_context(cafile=str(CA))


def request(url, *, method="GET", body=None, headers=None, timeout=5):
    data = None if body is None else json.dumps(body).encode()
    merged = {"Accept": "application/json"}
    if data is not None:
        merged["Content-Type"] = "application/json"
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=merged)
    try:
        with urllib.request.urlopen(req, context=ssl_context(), timeout=timeout) as response:
            payload = response.read()
            parsed = json.loads(payload) if payload else None
            return response.status, parsed, dict(response.headers)
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload) if payload else None
        except Exception:
            parsed = {"error": "non_json_error"}
        return exc.code, parsed, dict(exc.headers)


def wait_url(url, *, headers=None, statuses=(200,), seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            status, _, _ = request(url, headers=headers, timeout=2)
            if status in statuses:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"service_not_ready:{urllib.parse.urlsplit(url).port}")


def fga_headers():
    return {"Authorization": f"Bearer {env_values()['OPENFGA_PRESHARED_KEY']}"}


def create_store_model(label=None):
    status, store, _ = request(
        "https://127.0.0.1:18445/stores",
        method="POST",
        body={"name": label or f"authz-lab-{int(time.time())}"},
        headers=fga_headers(),
    )
    if status != 201:
        raise RuntimeError(f"create_store_http_{status}")
    store_id = store["id"]
    model = {
        "schema_version": "1.1",
        "type_definitions": [
            {"type": "user"},
            {
                "type": "album",
                "relations": {
                    "owner": {"this": {}},
                    "viewer": {"union": {"child": [{"this": {}}, {"computedUserset": {"relation": "owner"}}]}},
                    "can_view": {"computedUserset": {"relation": "viewer"}},
                    "can_edit": {"computedUserset": {"relation": "owner"}},
                },
                "metadata": {
                    "relations": {
                        "owner": {"directly_related_user_types": [{"type": "user"}]},
                        "viewer": {"directly_related_user_types": [{"type": "user"}]},
                        "can_view": {"directly_related_user_types": []},
                        "can_edit": {"directly_related_user_types": []},
                    }
                },
            },
        ],
    }
    status, created, _ = request(
        f"https://127.0.0.1:18445/stores/{store_id}/authorization-models",
        method="POST",
        body=model,
        headers=fga_headers(),
    )
    if status != 201:
        raise RuntimeError(f"create_model_http_{status}")
    set_env({"OPENFGA_STORE_ID": store_id, "OPENFGA_MODEL_ID": created["authorization_model_id"]})
    return store_id, created["authorization_model_id"]


def start(reset=False):
    if not ENV_FILE.exists():
        init()
    if reset:
        command(COMPOSE + ["down", "--volumes", "--remove-orphans"])
    command(COMPOSE + ["up", "-d", "--build", "db", "keycloak", "openfga"])
    wait_url("https://127.0.0.1:18443/realms/authz-lab/.well-known/openid-configuration", seconds=240)
    wait_url("https://127.0.0.1:18445/healthz", headers=fga_headers(), statuses=(200,), seconds=120)
    create_store_model("authz-lab-initial")
    command(COMPOSE + ["up", "-d", "--build", "--force-recreate", "api", "worker"])
    wait_url("https://127.0.0.1:18444/healthz", seconds=180)
    print("Authorization stack ready with verified HTTPS endpoints.")


def psql(sql, *, database="app", capture=False):
    args = COMPOSE + [
        "exec", "-T", "db", "psql", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", database
    ]
    return command(args, input_text=sql, capture=capture)


def fga_write(store_id, model_id, keys):
    for start_at in range(0, len(keys), 100):
        status, _, _ = request(
            f"https://127.0.0.1:18445/stores/{store_id}/write",
            method="POST",
            body={"authorization_model_id": model_id, "writes": {"tuple_keys": keys[start_at : start_at + 100]}},
            headers=fga_headers(),
            timeout=15,
        )
        if status != 200:
            raise RuntimeError(f"fga_seed_http_{status}")


def seed(shares):
    if shares not in (5, 50):
        raise ValueError("shares must be 5 or 50")
    store_id, model_id = create_store_model(f"authz-lab-shares-{shares}-{int(time.time())}")
    # Recreate API and worker so both processes use the fixed new store/model IDs.
    command(COMPOSE + ["up", "-d", "--build", "--force-recreate", "api", "worker"])
    wait_url("https://127.0.0.1:18444/healthz", seconds=180)
    sql = f"""
TRUNCATE authz_event, album_share, album RESTART IDENTITY CASCADE;
INSERT INTO album(id,owner_subject,name,version,changing)
SELECT i+1, '10000000-0000-4000-8000-' || lpad((i % 1000)::text,12,'0'), 'Synthetic album ' || (i+1), 0, false
FROM generate_series(0,999) AS g(i);
INSERT INTO album_share(album_id,user_subject,permission,version)
SELECT a, '10000000-0000-4000-8000-' || lpad(((a-1+s) % 1000)::text,12,'0'), 'viewer', 0
FROM generate_series(1,1000) a CROSS JOIN generate_series(1,{shares}) s;
"""
    psql(sql)
    owners = [
        {"user": f"user:{user_subject(i)}", "relation": "owner", "object": f"album:{i + 1}"}
        for i in range(1000)
    ]
    viewers = []
    for album in range(1, 1001):
        for offset in range(1, shares + 1):
            viewers.append(
                {
                    "user": f"user:{user_subject((album - 1 + offset) % 1000)}",
                    "relation": "viewer",
                    "object": f"album:{album}",
                }
            )
    fga_write(store_id, model_id, owners)
    fga_write(store_id, model_id, viewers)
    print(f"Seeded 1000 albums with {shares} viewers each in app DB and OpenFGA.")


def token(user_index, client_id="load-client"):
    values = env_values()
    form = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": client_id,
            "username": f"user-{user_index:05d}",
            "password": values["LAB_PASSWORD"],
        }
    ).encode()
    req = urllib.request.Request(
        "https://127.0.0.1:18443/realms/authz-lab/protocol/openid-connect/token",
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, context=ssl_context(), timeout=10) as response:
            return json.loads(response.read())["access_token"]
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read())
            code = error_body.get("error", "unknown")
            description = error_body.get("error_description", "")
            if "not fully set up" in description:
                category = "account_not_ready"
            elif "Invalid user credentials" in description:
                category = "invalid_credentials"
            elif "Client not allowed" in description:
                category = "client_not_allowed"
            else:
                category = "other"
        except Exception:
            code = "unknown"
            category = "unknown"
        raise RuntimeError(f"token_http_{exc.code}_{code}_{category}") from None


def form_request(url, values, *, headers=None):
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(values).encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, context=ssl_context(), timeout=10) as response:
        return response.status, json.loads(response.read())


def wrong_issuer_token():
    values = env_values()
    _, admin = form_request(
        "https://127.0.0.1:18443/realms/master/protocol/openid-connect/token",
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": values["KC_ADMIN_USER"],
            "password": values["KC_ADMIN_PASSWORD"],
        },
    )
    admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
    realm_url = "https://127.0.0.1:18443/admin/realms/authz-lab"
    status, representation, _ = request(realm_url, headers=admin_headers)
    if status != 200:
        raise RuntimeError(f"admin_get_realm_http_{status}")
    original_attributes = dict(representation.get("attributes") or {})
    changed = dict(representation)
    changed["attributes"] = {**original_attributes, "frontendUrl": "https://wrong-issuer.invalid"}
    status, _, _ = request(realm_url, method="PUT", body=changed, headers=admin_headers)
    if status != 204:
        raise RuntimeError(f"admin_set_frontend_http_{status}")
    try:
        issued = token(0)
        payload = issued.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if claims.get("iss") == "https://keycloak:8443/realms/authz-lab":
            raise RuntimeError("wrong_issuer_fixture_unchanged")
        return issued
    finally:
        restored = dict(representation)
        restored["attributes"] = original_attributes
        status, _, _ = request(realm_url, method="PUT", body=restored, headers=admin_headers)
        if status != 204:
            raise RuntimeError(f"admin_restore_frontend_http_{status}")


def api(path, access_token=None, *, method="GET", body=None, mode=None, idem=None):
    query = "" if mode is None else "?" + urllib.parse.urlencode({"mode": mode})
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if idem:
        headers["Idempotency-Key"] = idem
    return request(f"https://127.0.0.1:18444{path}{query}", method=method, body=body, headers=headers)


def safe_body(body):
    return isinstance(body, dict) and not any(key in body for key in ("name", "id", "token", "access_token"))


def record_case(cases, mode, name, status, expected_status, body, *, expects_data=False):
    if expected_status == 200:
        data_ok = isinstance(body, dict) and ("id" in body) and ("name" in body if expects_data else True)
    else:
        data_ok = safe_body(body)
    passed = status == expected_status and data_ok
    cases.append(
        {
            "mode": mode,
            "case": name,
            "expected_status": expected_status,
            "actual_status": status,
            "protected_data_expected": expects_data,
            "protected_data_shape_ok": data_ok,
            "pass": passed,
        }
    )


def e1():
    seed(5)
    alice, bob, carol = token(0), token(1), token(100)
    wrong_aud = token(0, "wrong-audience")
    short = token(0, "short-client")
    wrong_issuer = wrong_issuer_token()
    time.sleep(2)
    parts = alice.split(".")
    tampered_signature = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    tampered = ".".join([parts[0], parts[1], tampered_signature])
    cases = []
    for mode in ("direct", "fga"):
        for name, user_token, path, method, body, expected, data in [
            ("alice_view", alice, "/albums/1", "GET", None, 200, True),
            ("alice_edit", alice, "/albums/1", "PUT", {"name": "E1 edited", "user_id": 999}, 200, False),
            ("bob_shared_view", bob, "/albums/1", "GET", None, 200, True),
            ("bob_edit_denied", bob, "/albums/1", "PUT", {"name": "forbidden"}, 403, False),
            ("bob_share_change_denied", bob, "/albums/1/shares", "POST", {"user_id": 100}, 403, False),
            ("carol_view_denied", carol, "/albums/1", "GET", None, 403, False),
            ("body_identity_ignored", alice, "/albums/2", "PUT", {"name": "tamper", "user_id": 1}, 403, False),
            ("nonexistent_no_leak", alice, "/albums/999999", "GET", None, 403, False),
            ("malformed_no_leak", alice, "/albums/not-a-number", "GET", None, 400, False),
            ("unauthenticated", None, "/albums/1", "GET", None, 401, False),
            ("tampered_token", tampered, "/albums/1", "GET", None, 401, False),
            ("expired_token", short, "/albums/1", "GET", None, 401, False),
            ("wrong_audience", wrong_aud, "/albums/1", "GET", None, 401, False),
            ("wrong_issuer", wrong_issuer, "/albums/1", "GET", None, 401, False),
        ]:
            status, response_body, _ = api(path, user_token, method=method, body=body, mode=mode)
            record_case(cases, mode, name, status, expected, response_body, expects_data=data)
    # A stopped dependency must not become an allow decision in mode B.
    command(COMPOSE + ["stop", "openfga"])
    status, response_body, _ = api("/albums/1", alice, mode="fga")
    record_case(cases, "fga", "openfga_stopped_fail_closed", status, 503, response_body)
    command(COMPOSE + ["start", "openfga"])
    wait_url("https://127.0.0.1:18445/healthz", headers=fga_headers(), seconds=120)
    output = {
        "experiment": "E1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cases": cases,
        "passed": all(case["pass"] for case in cases),
        "case_count": len(cases),
        "over_permit_count": sum(1 for case in cases if case["expected_status"] != 200 and case["actual_status"] == 200),
        "limitations": [],
    }
    folder = RAW / (time.strftime("%Y%m%dT%H%M%S") + "-e1")
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(json.dumps(output, indent=2))
    print(json.dumps({"run": folder.name, "passed": output["passed"], "cases": len(cases), "over_permit": output["over_permit_count"]}))
    return output["passed"]


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def wait_operation(access_token, operation_id, timeout_s=15):
    started = time.perf_counter()
    last = None
    while time.perf_counter() - started < timeout_s:
        status, body, _ = api(f"/operations/{operation_id}", access_token)
        if status == 200:
            last = body
            if body.get("status") in ("applied", "superseded"):
                return body, (time.perf_counter() - started) * 1000
        time.sleep(0.02)
    raise RuntimeError(f"operation_timeout_{last.get('status') if isinstance(last, dict) else 'unknown'}")


def share_change(owner_token, mode, operation, idem=None):
    method = "POST" if operation == "add" else "DELETE"
    return api(
        "/albums/1/shares",
        owner_token,
        method=method,
        body={"user_id": 1},
        mode=mode,
        idem=idem or str(uuid.uuid4()),
    )


def fga_viewer_allowed():
    values = env_values()
    status, body, _ = request(
        f"https://127.0.0.1:18445/stores/{values['OPENFGA_STORE_ID']}/check",
        method="POST",
        body={
            "authorization_model_id": values["OPENFGA_MODEL_ID"],
            "tuple_key": {"user": f"user:{user_subject(1)}", "relation": "viewer", "object": "album:1"},
            "consistency": "HIGHER_CONSISTENCY",
        },
        headers=fga_headers(),
    )
    if status != 200:
        raise RuntimeError(f"fga_check_http_{status}")
    return bool(body.get("allowed"))


def ensure_share(owner_token, mode, desired):
    operation = "add" if desired else "delete"
    status, body, _ = share_change(owner_token, mode, operation)
    if status == 200 and body.get("status") == "already_applied":
        return 0.0
    if status != 202:
        raise RuntimeError(f"ensure_share_http_{status}")
    result, elapsed = wait_operation(owner_token, body["operation_id"])
    if result["status"] != "applied":
        raise RuntimeError("ensure_share_not_applied")
    return elapsed


def e2_mode(mode, cycles=100):
    seed(5)
    alice, bob = token(0), token(1)
    ensure_share(alice, mode, True)
    removals = []
    additions = []
    erroneous_allow_after_remove = 0
    erroneous_deny_after_add = 0
    pending_availability_denials = 0
    pending_allows = 0
    status_errors = 0
    for _ in range(cycles):
        began = time.perf_counter()
        status, body, _ = share_change(alice, mode, "delete")
        if status != 202:
            status_errors += 1
            continue
        immediate, immediate_body, _ = api("/albums/1", bob, mode=mode)
        if immediate == 503:
            pending_availability_denials += 1
        elif immediate == 200:
            pending_allows += 1
        result, _ = wait_operation(alice, body["operation_id"])
        removals.append((time.perf_counter() - began) * 1000)
        if result["status"] != "applied":
            status_errors += 1
        for _ in range(3):
            observed, _, _ = api("/albums/1", bob, mode=mode)
            if observed == 200:
                erroneous_allow_after_remove += 1
            elif observed != 403:
                status_errors += 1
        began = time.perf_counter()
        status, body, _ = share_change(alice, mode, "add")
        if status != 202:
            status_errors += 1
            continue
        immediate, _, _ = api("/albums/1", bob, mode=mode)
        if immediate == 503:
            pending_availability_denials += 1
        elif immediate == 200:
            pending_allows += 1
        result, _ = wait_operation(alice, body["operation_id"])
        additions.append((time.perf_counter() - began) * 1000)
        if result["status"] != "applied":
            status_errors += 1
        for _ in range(3):
            observed, _, _ = api("/albums/1", bob, mode=mode)
            if observed != 200:
                erroneous_deny_after_add += 1

    # Duplicate idempotency key: both responses must point to one operation.
    duplicate_key = str(uuid.uuid4())
    command(COMPOSE + ["stop", "worker"])
    first_status, first_body, _ = share_change(alice, mode, "delete", duplicate_key)
    second_status, second_body, _ = share_change(alice, mode, "delete", duplicate_key)
    duplicate_ok = (
        first_status == 202
        and second_status == 202
        and first_body.get("operation_id") == second_body.get("operation_id")
        and first_body.get("version") == second_body.get("version")
    )
    command(COMPOSE + ["start", "worker"])
    wait_operation(alice, first_body["operation_id"])

    # An old event must be superseded and must not resurrect a removed tuple.
    version_result = psql("SELECT version FROM album WHERE id=1;", capture=True)
    current_version = int(version_result.stdout.strip().splitlines()[-1])
    stale_id, stale_key = uuid.uuid4(), uuid.uuid4()
    psql(
        "INSERT INTO authz_event(id,idempotency_key,album_id,user_subject,relation,operation,version,status) "
        f"VALUES('{stale_id}','{stale_key}',1,'{user_subject(1)}','viewer','add',{max(0, current_version - 1)},'pending');"
    )
    stale_result, _ = wait_operation(alice, str(stale_id))
    stale_ok = stale_result.get("status") == "superseded" and not fga_viewer_allowed()

    # Explicit add -> remove -> add ordering and final-state agreement.
    chain = []
    for operation in ("add", "delete", "add"):
        status, body, _ = share_change(alice, mode, operation)
        if status != 202:
            chain.append(False)
            continue
        applied, _ = wait_operation(alice, body["operation_id"])
        chain.append(applied.get("status") == "applied")

    state = psql(
        f"SELECT (SELECT count(*) FROM album_share WHERE album_id=1 AND user_subject='{user_subject(1)}'),"
        "version,changing,(SELECT count(*) FROM authz_event WHERE status='pending') FROM album WHERE id=1;",
        capture=True,
    ).stdout.strip().splitlines()[-1].split("|")
    final_app_shared = int(state[0]) == 1
    final_changing = state[2] == "f"
    final_pending = int(state[3])
    final_fga_shared = fga_viewer_allowed()
    output = {
        "mode": mode,
        "cycles": cycles,
        "share_changes_completed": len(removals) + len(additions),
        "removal_completion_ms": {
            "p50": percentile(removals, 0.5),
            "p95": percentile(removals, 0.95),
            "p99": percentile(removals, 0.99),
            "max": max(removals) if removals else None,
        },
        "addition_completion_ms": {
            "p50": percentile(additions, 0.5),
            "p95": percentile(additions, 0.95),
            "p99": percentile(additions, 0.99),
            "max": max(additions) if additions else None,
        },
        "erroneous_allow_after_removal_completion": erroneous_allow_after_remove,
        "erroneous_deny_after_add_completion": erroneous_deny_after_add,
        "pending_availability_denials": pending_availability_denials,
        "pending_allows_observed": pending_allows,
        "status_errors": status_errors,
        "duplicate_idempotency_pass": duplicate_ok,
        "stale_event_superseded_pass": stale_ok,
        "ordered_add_remove_add_pass": all(chain),
        "final_app_shared": final_app_shared,
        "final_fga_shared": final_fga_shared,
        "final_album_changing": not final_changing,
        "final_pending_events": final_pending,
    }
    output["passed"] = (
        len(removals) == cycles
        and len(additions) == cycles
        and erroneous_allow_after_remove == 0
        and erroneous_deny_after_add == 0
        and status_errors == 0
        and duplicate_ok
        and stale_ok
        and all(chain)
        and final_app_shared
        and final_fga_shared
        and final_changing
        and final_pending == 0
    )
    return output


def e2():
    modes = [e2_mode("direct"), e2_mode("fga")]
    # Required 30-second stopped-worker reconciliation in mode B.
    seed(5)
    alice, bob = token(0), token(1)
    command(COMPOSE + ["stop", "worker"])
    fault_started = time.perf_counter()
    status, body, _ = share_change(alice, "fga", "delete")
    pending_denials = 0
    unexpected = 0
    while time.perf_counter() - fault_started < 30:
        observed, _, _ = api("/albums/1", bob, mode="fga")
        if observed == 503:
            pending_denials += 1
        else:
            unexpected += 1
        time.sleep(1)
    command(COMPOSE + ["start", "worker"])
    applied, recovery_ms = wait_operation(alice, body["operation_id"], timeout_s=30)
    after_status, _, _ = api("/albums/1", bob, mode="fga")
    worker_fault = {
        "stop_duration_s": 30,
        "request_status": status,
        "pending_availability_denials": pending_denials,
        "unexpected_pending_results": unexpected,
        "operation_status_after_resume": applied.get("status"),
        "resume_to_completion_ms": recovery_ms,
        "new_request_after_completion_status": after_status,
    }
    worker_fault["passed"] = (
        status == 202
        and unexpected == 0
        and pending_denials > 0
        and applied.get("status") == "applied"
        and after_status == 403
    )
    output = {
        "experiment": "E2",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "modes": modes,
        "worker_stop_reconciliation": worker_fault,
        "passed": all(item["passed"] for item in modes) and worker_fault["passed"],
        "over_permit_count": sum(item["erroneous_allow_after_removal_completion"] for item in modes),
    }
    folder = RAW / (time.strftime("%Y%m%dT%H%M%S") + "-e2")
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(json.dumps(output, indent=2))
    print(json.dumps({"run": folder.name, "passed": output["passed"], "over_permit": output["over_permit_count"]}))
    return output["passed"]


def parse_size(value):
    number = value.strip()
    units = {"B": 1, "kB": 1000, "KB": 1000, "KiB": 1024, "MB": 1000**2, "MiB": 1024**2, "GB": 1000**3, "GiB": 1024**3}
    for unit in sorted(units, key=len, reverse=True):
        if number.endswith(unit):
            return float(number[: -len(unit)].strip()) * units[unit]
    return float(number)


def observe(stop_event, folder, started_monotonic, warmup):
    values = env_values()
    names = [
        "oidc-authz-lab-db-1",
        "oidc-authz-lab-keycloak-1",
        "oidc-authz-lab-openfga-1",
        "oidc-authz-lab-api-1",
        "oidc-authz-lab-worker-1",
    ]
    with (folder / "observer.jsonl").open("w") as stream:
        while not stop_event.is_set():
            elapsed = time.monotonic() - started_monotonic
            record = {
                "elapsed_s": elapsed,
                "phase": "measure" if elapsed >= warmup else "warmup",
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": {},
                "observation_error": False,
            }
            try:
                stats = command(
                    ["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}", *names],
                    capture=True,
                    timeout=8,
                )
                for line in stats.stdout.splitlines():
                    name, cpu, memory = line.split("|", 2)
                    used = memory.split("/", 1)[0].strip()
                    record["containers"][name.removeprefix("oidc-authz-lab-").removesuffix("-1")] = {
                        "cpu_pct": float(cpu.rstrip("%")),
                        "memory_bytes": parse_size(used),
                    }
                db = psql(
                    "SELECT datname,count(*),count(*) FILTER (WHERE state='active') FROM pg_stat_activity "
                    "WHERE datname IN ('auth','app','authz') GROUP BY datname ORDER BY datname;",
                    database="postgres",
                    capture=True,
                )
                record["database_connections"] = {
                    line.split("|")[0]: {"total": int(line.split("|")[1]), "active": int(line.split("|")[2])}
                    for line in db.stdout.splitlines()
                    if line.count("|") == 2
                }
                status, body, _ = request(
                    "https://127.0.0.1:18444/internal/status",
                    headers={"X-Observer-Token": values["OBSERVER_TOKEN"]},
                    timeout=2,
                )
                if status == 200:
                    record["application"] = body
                else:
                    record["observation_error"] = True
            except Exception:
                record["observation_error"] = True
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            stop_event.wait(5)


def metric_values(summary, name):
    return (summary.get("metrics", {}).get(name) or {}).get("values", {})


def aggregate_load(folder, manifest, resources):
    summary = json.loads((folder / "summary.json").read_text())
    attempts = metric_values(summary, "authz_attempts{phase:measure}")
    completed = metric_values(summary, "authz_completed{phase:measure}")
    allow = metric_values(summary, "authz_expected_allow{phase:measure}")
    deny = metric_values(summary, "authz_expected_deny{phase:measure}")
    correct = metric_values(summary, "authz_decision_correct{phase:measure}")
    unexpected = metric_values(summary, "authz_unexpected{phase:measure}")
    over = metric_values(summary, "authz_over_permit{phase:measure}")
    under = metric_values(summary, "authz_under_permit{phase:measure}")
    dropped = metric_values(summary, "dropped_iterations")

    def trend(name):
        values = metric_values(summary, name)
        return {key: values.get(key) for key in ("med", "p(50)", "p(95)", "p(99)", "max", "avg", "count")}

    result = {
        "run_id": folder.name,
        "experiment": manifest["experiment"],
        "mode": manifest["mode"],
        "rate_rps": manifest["rate_rps"],
        "shares_per_album": manifest["shares_per_album"],
        "planned": manifest["rate_rps"] * manifest["duration_s"],
        "started": int(attempts.get("count", 0)),
        "sent": int(attempts.get("count", 0)),
        "completed": int(completed.get("count", 0)),
        "expected_allow": int(allow.get("count", 0)),
        "expected_deny": int(deny.get("count", 0)),
        "correct_decisions": int(correct.get("passes", 0)),
        "decision_accuracy": correct.get("rate"),
        "over_permit": int(over.get("count", 0)),
        "under_permit": int(under.get("count", 0)),
        "unexpected_or_timeout": int(unexpected.get("passes", 0)),
        "unexpected_ratio": unexpected.get("rate"),
        "dropped_iterations_all_phases": int(dropped.get("count", 0)),
        "latency_ms": {
            "whole": trend("authz_api_ms{phase:measure}"),
            "expected_allow": trend("authz_api_ms{phase:measure,expected:allow}"),
            "expected_deny": trend("authz_api_ms{phase:measure,expected:deny}"),
            "jwt": trend("authz_jwt_ms{phase:measure}"),
            "db": trend("authz_db_ms{phase:measure}"),
            "openfga": trend("authz_fga_ms{phase:measure}"),
        },
        "resources": resources,
        "k6_thresholds_passed": summary.get("state", {}).get("isStdOutTTY") is not None,
        "exit_code": manifest["exit_code"],
    }
    p99 = result["latency_ms"]["whole"].get("p(99)")
    result["passed"] = (
        result["exit_code"] == 0
        and result["decision_accuracy"] is not None
        and result["decision_accuracy"] >= 0.999
        and result["over_permit"] == 0
        and p99 is not None
        and p99 < 300
        and result["unexpected_ratio"] is not None
        and result["unexpected_ratio"] <= 0.001
        and result["dropped_iterations_all_phases"] == 0
    )
    return result


def resource_summary(folder, warmup, rate_rps):
    rows = [json.loads(line) for line in (folder / "observer.jsonl").read_text().splitlines() if line]
    request_counts = [
        row.get("application", {}).get("metrics", {}).get("requests")
        for row in rows
        if row.get("application", {}).get("metrics", {}).get("requests") is not None
    ]
    baseline_requests = min(request_counts) if request_counts else 0
    warmup_requests = warmup * rate_rps
    measured = [
        row
        for row in rows
        if row.get("application", {}).get("metrics", {}).get("requests", baseline_requests) - baseline_requests >= warmup_requests
    ]
    containers = {}
    for service in ("db", "keycloak", "openfga", "api", "worker"):
        cpu = [row["containers"][service]["cpu_pct"] for row in measured if service in row.get("containers", {})]
        memory = [row["containers"][service]["memory_bytes"] for row in measured if service in row.get("containers", {})]
        containers[service] = {
            "cpu_pct_median": statistics.median(cpu) if cpu else None,
            "cpu_pct_peak": max(cpu) if cpu else None,
            "memory_bytes_median": statistics.median(memory) if memory else None,
            "memory_bytes_peak": max(memory) if memory else None,
        }
    connections = {}
    for database in ("auth", "app", "authz"):
        total = [row.get("database_connections", {}).get(database, {}).get("total") for row in measured]
        active = [row.get("database_connections", {}).get(database, {}).get("active") for row in measured]
        total = [value for value in total if value is not None]
        active = [value for value in active if value is not None]
        connections[database] = {"total_peak": max(total) if total else None, "active_peak": max(active) if active else None}
    pending = [row.get("application", {}).get("outbox_pending") for row in measured]
    pending = [value for value in pending if value is not None]
    return {
        "sample_count_all": len(rows),
        "sample_count_measure": len(measured),
        "measurement_boundary": "api_request_counter_after_warmup",
        "baseline_requests": baseline_requests,
        "warmup_requests": warmup_requests,
        "observation_errors_all": sum(bool(row.get("observation_error")) for row in rows),
        "observation_errors_measure": sum(bool(row.get("observation_error")) for row in measured),
        "containers": containers,
        "database_connections": connections,
        "outbox_pending_peak": max(pending) if pending else None,
    }


def image_manifest():
    images = ["postgres:17.6", "openfga/openfga:v1.18.1", "grafana/k6:1.8.1", "oidc-load-lab-keycloak:26.7.3", "oidc-authz-lab-api:1"]
    output = {}
    for name in images:
        inspected = command(
            ["docker", "image", "inspect", "--format", "{{.Id}}|{{.Architecture}}|{{join .RepoDigests \",\"}}", name],
            capture=True,
            check=False,
        )
        parts = inspected.stdout.strip().split("|", 2)
        output[name] = {"image_id": parts[0], "architecture": parts[1], "repo_digests": parts[2].split(",") if len(parts) > 2 and parts[2] else []} if len(parts) >= 2 else {"error": "inspect_failed"}
    return output


def run_load(name, mode, rate_rps, shares, duration_s=180, warmup_s=60, vus=200, experiment="E3"):
    folder = RAW / (time.strftime("%Y%m%dT%H%M%S") + f"-{name}-{mode}-{rate_rps}rps-s{shares}")
    folder.mkdir(parents=True)
    head = command(["git", "rev-parse", "HEAD"], capture=True).stdout.strip()
    dirty = bool(command(["git", "status", "--porcelain", "--untracked-files=no"], capture=True).stdout.strip())
    manifest = {
        "run_id": folder.name,
        "experiment": experiment,
        "mode": mode,
        "rate_rps": rate_rps,
        "shares_per_album": shares,
        "duration_s": duration_s,
        "warmup_s": warmup_s,
        "vus": vus,
        "users": 1000,
        "albums": 1000,
        "allowed_mix_pct": 80,
        "denied_mix_pct": 20,
        "target_seed": "deterministic-iteration-v1",
        "git_head": head,
        "git_dirty": dirty,
        "docker_vm": json.loads(command(["docker", "info", "--format", "{\"architecture\":\"{{.Architecture}}\",\"cpus\":{{.NCPU}},\"memory_bytes\":{{.MemTotal}}}"], capture=True).stdout),
        "images": image_manifest(),
        "resource_limits": {"keycloak": "1.5CPU/3GiB", "postgres": "1CPU/2GiB", "api": "0.4CPU/768MiB", "worker": "0.1CPU/256MiB", "openfga": "0.5CPU/1GiB", "k6": "0.5CPU/1GiB"},
        "tls": {"client_to_keycloak": "verified_local_ca", "client_to_api": "verified_local_ca", "api_worker_to_openfga": "verified_local_ca", "database": "disabled_inside_dedicated_network"},
        "openfga_check_consistency": "HIGHER_CONSISTENCY",
        "openfga_check_cache": "disabled",
        "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "measurement_status": "started",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    stop_event = threading.Event()
    observer = threading.Thread(target=observe, args=(stop_event, folder, time.monotonic(), warmup_s), daemon=True)
    observer.start()
    run_command = COMPOSE + [
        "run", "--rm", "--no-deps", "--user", "0:0", "-v", f"{folder}:/results",
        "-e", f"MODE={mode}", "-e", f"RATE={rate_rps}", "-e", f"SHARES={shares}",
        "-e", f"DURATION={duration_s}", "-e", f"WARMUP={warmup_s}", "-e", f"VUS={vus}",
        "k6", "run", "/scripts/authz.js",
    ]
    with (folder / "console.log").open("w") as console:
        process = subprocess.run(run_command, cwd=ROOT, stdout=console, stderr=subprocess.STDOUT)
    stop_event.set()
    observer.join(timeout=15)
    manifest["exit_code"] = process.returncode
    manifest["end_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["measurement_status"] = "completed" if (folder / "summary.json").exists() else "failed_without_summary"
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if not (folder / "summary.json").exists():
        result = {"run_id": folder.name, "passed": False, "exit_code": process.returncode, "error": "missing_summary"}
    else:
        resources = resource_summary(folder, warmup_s, rate_rps)
        result = aggregate_load(folder, manifest, resources)
    (folder / "aggregate.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"run": folder.name, "passed": result.get("passed"), "exit_code": process.returncode, "p99_ms": result.get("latency_ms", {}).get("whole", {}).get("p(99)"), "accuracy": result.get("decision_accuracy"), "over_permit": result.get("over_permit"), "dropped": result.get("dropped_iterations_all_phases")}))
    return result


def check():
    command([sys.executable, "-m", "py_compile", "authz/app/main.py", "authz/app/worker.py", "scripts/authz_lab.py"])
    command(COMPOSE + ["config", "--quiet"])
    command([sys.executable, "scripts/public_bundle.py", "--check"])
    print("Static checks passed; no runtime experiment result inferred.")


def stop(volumes=False):
    args = COMPOSE + ["down", "--remove-orphans"]
    if volumes:
        args.append("--volumes")
    command(args)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    start_parser = commands.add_parser("start")
    start_parser.add_argument("--reset", action="store_true")
    seed_parser = commands.add_parser("seed")
    seed_parser.add_argument("--shares", type=int, required=True, choices=[5, 50])
    commands.add_parser("e1")
    commands.add_parser("e2")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--mode", required=True, choices=["direct", "fga"])
    run_parser.add_argument("--rate", required=True, type=int)
    run_parser.add_argument("--shares", required=True, type=int, choices=[5, 50])
    run_parser.add_argument("--duration", type=int, default=180)
    run_parser.add_argument("--warmup", type=int, default=60)
    run_parser.add_argument("--vus", type=int, default=200)
    run_parser.add_argument("--experiment", default="E3")
    commands.add_parser("check")
    stop_parser = commands.add_parser("stop")
    stop_parser.add_argument("--volumes", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        init()
    elif args.command == "start":
        start(args.reset)
    elif args.command == "seed":
        seed(args.shares)
    elif args.command == "e1":
        raise SystemExit(0 if e1() else 1)
    elif args.command == "e2":
        raise SystemExit(0 if e2() else 1)
    elif args.command == "run":
        run_load(args.name, args.mode, args.rate, args.shares, args.duration, args.warmup, args.vus, args.experiment)
    elif args.command == "check":
        check()
    elif args.command == "stop":
        stop(args.volumes)


if __name__ == "__main__":
    main()
