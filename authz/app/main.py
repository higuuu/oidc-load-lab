import json
import os
import threading
import time
import uuid
from dataclasses import dataclass

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from jwt.algorithms import RSAAlgorithm
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


DB = {
    "host": os.environ["APP_DB_HOST"],
    "dbname": os.environ["APP_DB_NAME"],
    "user": os.environ["APP_DB_USER"],
    "password": os.environ["APP_DB_PASSWORD"],
    "sslmode": "disable",
}
ISSUER = os.environ["OIDC_ISSUER"]
AUDIENCE = os.environ["OIDC_AUDIENCE"]
JWKS_URL = os.environ["OIDC_JWKS_URL"]
FGA_URL = os.environ["OPENFGA_URL"].rstrip("/")
FGA_STORE = os.environ["OPENFGA_STORE_ID"]
FGA_MODEL = os.environ["OPENFGA_MODEL_ID"]
FGA_KEY = os.environ["OPENFGA_PRESHARED_KEY"]
CA_FILE = os.environ["CA_FILE"]
OBSERVER_TOKEN = os.environ["OBSERVER_TOKEN"]

pool = ConnectionPool(kwargs=DB, min_size=2, max_size=20, timeout=2, open=False)


class JwksVerifier:
    def __init__(self):
        self.client = httpx.Client(
            verify=CA_FILE, timeout=1.5, limits=httpx.Limits(max_connections=4), trust_env=False
        )
        self.keys = {}
        self.fetched_at = 0.0
        self.last_unknown_refresh = 0.0
        self.lock = threading.Lock()
        self.refresh_count = 0

    def _refresh(self):
        response = self.client.get(JWKS_URL)
        response.raise_for_status()
        body = response.json()
        keys = {}
        for item in body.get("keys", []):
            if item.get("kid") and item.get("kty") == "RSA" and item.get("use") in (None, "sig"):
                keys[item["kid"]] = RSAAlgorithm.from_jwk(json.dumps(item))
        if not keys:
            raise ValueError("empty_jwks")
        self.keys = keys
        self.fetched_at = time.monotonic()
        self.refresh_count += 1

    def verify(self, token):
        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:
            raise ValueError("invalid_token") from exc
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("invalid_token")
        with self.lock:
            now = time.monotonic()
            if not self.keys or now - self.fetched_at > 300:
                self._refresh()
            key = self.keys.get(header["kid"])
            if key is None and now - self.last_unknown_refresh >= 5:
                self.last_unknown_refresh = now
                self._refresh()
                key = self.keys.get(header["kid"])
        if key is None:
            raise ValueError("invalid_token")
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise ValueError("invalid_token") from exc


verifier = JwksVerifier()
fga = httpx.Client(
    verify=CA_FILE,
    timeout=httpx.Timeout(0.4, connect=0.25),
    headers={"Authorization": f"Bearer {FGA_KEY}", "Content-Type": "application/json"},
    limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
    trust_env=False,
)


@dataclass
class Timings:
    jwt_ms: float = 0.0
    db_ms: float = 0.0
    fga_ms: float = 0.0

    def header(self):
        return f"jwt;dur={self.jwt_ms:.3f}, db;dur={self.db_ms:.3f}, fga;dur={self.fga_ms:.3f}"


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.values = {
            "requests": 0,
            "authenticated": 0,
            "allowed": 0,
            "denied": 0,
            "auth_failed": 0,
            "dependency_failed": 0,
        }

    def add(self, key):
        with self.lock:
            self.values[key] += 1

    def snapshot(self):
        with self.lock:
            return {**self.values, "uptime_s": round(time.time() - self.started, 3), "jwks_refreshes": verifier.refresh_count}


counters = Counters()
app = FastAPI(title="Synthetic album authorization lab", docs_url=None, redoc_url=None)


def reply(status, body, timings):
    return JSONResponse(status_code=status, content=body, headers={"Server-Timing": timings.header(), "Cache-Control": "no-store"})


def authenticate(request, timings):
    start = time.perf_counter()
    try:
        value = request.headers.get("authorization", "")
        if not value.startswith("Bearer ") or len(value) <= 7:
            raise ValueError("missing_token")
        claims = verifier.verify(value[7:])
        counters.add("authenticated")
        return claims["sub"]
    except Exception:
        counters.add("auth_failed")
        return None
    finally:
        timings.jwt_ms += (time.perf_counter() - start) * 1000


def parse_album_id(raw):
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if 1 <= value <= 1_000_000_000 else None


def get_album(album_id, timings, lock=False):
    start = time.perf_counter()
    with pool.connection() as conn:
        suffix = " FOR UPDATE" if lock else ""
        row = conn.execute(
            "SELECT id, owner_subject, name, version, changing FROM album WHERE id = %s" + suffix,
            (album_id,),
        ).fetchone()
    timings.db_ms += (time.perf_counter() - start) * 1000
    return row


def direct_allowed(subject, album_id, action, timings):
    start = time.perf_counter()
    with pool.connection() as conn:
        if action in ("edit", "share"):
            row = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM album WHERE id=%s AND owner_subject=%s)",
                (album_id, subject),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT EXISTS(
                       SELECT 1 FROM album a WHERE a.id=%s AND
                       (a.owner_subject=%s OR EXISTS(
                         SELECT 1 FROM album_share s WHERE s.album_id=a.id AND s.user_subject=%s AND s.permission='viewer'
                       )))""",
                (album_id, subject, subject),
            ).fetchone()
    timings.db_ms += (time.perf_counter() - start) * 1000
    return bool(row[0])


def fga_allowed(subject, album_id, action, timings):
    relation = "can_view" if action == "view" else "can_edit"
    start = time.perf_counter()
    try:
        response = fga.post(
            f"{FGA_URL}/stores/{FGA_STORE}/check",
            json={
                "authorization_model_id": FGA_MODEL,
                "tuple_key": {"user": f"user:{subject}", "relation": relation, "object": f"album:{album_id}"},
                "consistency": "HIGHER_CONSISTENCY",
            },
        )
        response.raise_for_status()
        return bool(response.json().get("allowed"))
    except Exception as exc:
        raise RuntimeError("authorization_dependency") from exc
    finally:
        timings.fga_ms += (time.perf_counter() - start) * 1000


def authorize(subject, album, action, mode, timings):
    if album is None:
        return False, 403, "forbidden"
    if album[4]:
        return False, 503, "authorization_pending"
    if mode == "direct":
        allowed = direct_allowed(subject, album[0], action, timings)
    elif mode == "fga":
        try:
            allowed = fga_allowed(subject, album[0], action, timings)
        except RuntimeError:
            counters.add("dependency_failed")
            return False, 503, "authorization_unavailable"
    else:
        return False, 400, "invalid_mode"
    return allowed, 200 if allowed else 403, "ok" if allowed else "forbidden"


@app.on_event("startup")
def startup():
    pool.open(wait=True)
    with pool.connection() as conn:
        conn.execute("""
          CREATE TABLE IF NOT EXISTS album (
            id bigint PRIMARY KEY,
            owner_subject text NOT NULL,
            name text NOT NULL,
            version bigint NOT NULL DEFAULT 0,
            changing boolean NOT NULL DEFAULT false
          );
          CREATE TABLE IF NOT EXISTS album_share (
            album_id bigint NOT NULL REFERENCES album(id) ON DELETE CASCADE,
            user_subject text NOT NULL,
            permission text NOT NULL CHECK (permission = 'viewer'),
            version bigint NOT NULL,
            PRIMARY KEY(album_id, user_subject, permission)
          );
          CREATE TABLE IF NOT EXISTS authz_event (
            id uuid PRIMARY KEY,
            idempotency_key text UNIQUE NOT NULL,
            album_id bigint NOT NULL REFERENCES album(id) ON DELETE CASCADE,
            user_subject text NOT NULL,
            relation text NOT NULL CHECK (relation = 'viewer'),
            operation text NOT NULL CHECK (operation IN ('add', 'delete')),
            version bigint NOT NULL,
            status text NOT NULL CHECK (status IN ('pending', 'applied', 'superseded')),
            attempts integer NOT NULL DEFAULT 0,
            last_error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            applied_at timestamptz
          );
          CREATE INDEX IF NOT EXISTS authz_event_pending_idx ON authz_event(status, created_at);
        """)


@app.on_event("shutdown")
def shutdown():
    pool.close()
    fga.close()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/internal/status")
def internal_status(request: Request):
    if request.headers.get("x-observer-token") != OBSERVER_TOKEN:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    with pool.connection() as conn:
        pending = conn.execute("SELECT count(*) FROM authz_event WHERE status='pending'").fetchone()[0]
        changing = conn.execute("SELECT count(*) FROM album WHERE changing").fetchone()[0]
    return {"metrics": counters.snapshot(), "outbox_pending": pending, "albums_changing": changing}


@app.get("/albums/{album_id}")
def view_album(album_id: str, request: Request, mode: str = "direct"):
    counters.add("requests")
    timings = Timings()
    subject = authenticate(request, timings)
    if subject is None:
        return reply(401, {"error": "unauthorized"}, timings)
    parsed = parse_album_id(album_id)
    if parsed is None:
        return reply(400, {"error": "invalid_request"}, timings)
    album = get_album(parsed, timings)
    allowed, status, code = authorize(subject, album, "view", mode, timings)
    if not allowed:
        counters.add("denied")
        return reply(status, {"error": code}, timings)
    counters.add("allowed")
    return reply(200, {"id": album[0], "name": album[2]}, timings)


@app.put("/albums/{album_id}")
async def edit_album(album_id: str, request: Request, mode: str = "direct"):
    counters.add("requests")
    timings = Timings()
    subject = authenticate(request, timings)
    if subject is None:
        return reply(401, {"error": "unauthorized"}, timings)
    parsed = parse_album_id(album_id)
    if parsed is None:
        return reply(400, {"error": "invalid_request"}, timings)
    try:
        body = await request.json()
        name = body.get("name")
        if not isinstance(name, str) or not (1 <= len(name) <= 80):
            raise ValueError()
    except Exception:
        return reply(400, {"error": "invalid_request"}, timings)
    album = get_album(parsed, timings)
    allowed, status, code = authorize(subject, album, "edit", mode, timings)
    if not allowed:
        counters.add("denied")
        return reply(status, {"error": code}, timings)
    start = time.perf_counter()
    with pool.connection() as conn:
        conn.execute("UPDATE album SET name=%s WHERE id=%s", (name, parsed))
    timings.db_ms += (time.perf_counter() - start) * 1000
    counters.add("allowed")
    return reply(200, {"id": parsed, "updated": True}, timings)


async def change_share(album_id, request, mode, operation):
    counters.add("requests")
    timings = Timings()
    subject = authenticate(request, timings)
    if subject is None:
        return reply(401, {"error": "unauthorized"}, timings)
    parsed = parse_album_id(album_id)
    if parsed is None:
        return reply(400, {"error": "invalid_request"}, timings)
    try:
        body = await request.json()
        target_id = int(body["user_id"])
        if target_id < 0 or target_id >= 1000:
            raise ValueError()
        target = f"10000000-0000-4000-8000-{target_id:012d}"
    except Exception:
        return reply(400, {"error": "invalid_request"}, timings)
    idem = request.headers.get("idempotency-key", "")
    try:
        uuid.UUID(idem)
    except Exception:
        return reply(400, {"error": "invalid_idempotency_key"}, timings)
    # Retry lookup precedes the pending-state gate. It is returned only to the
    # current album owner, so an idempotency key cannot be used as an oracle.
    start = time.perf_counter()
    with pool.connection() as conn:
        existing = conn.execute(
            """SELECT e.id,e.status,e.version,a.owner_subject
               FROM authz_event e JOIN album a ON a.id=e.album_id
               WHERE e.idempotency_key=%s AND e.album_id=%s""",
            (idem, parsed),
        ).fetchone()
    timings.db_ms += (time.perf_counter() - start) * 1000
    if existing and existing[3] == subject:
        return reply(202, {"operation_id": str(existing[0]), "status": existing[1], "version": existing[2]}, timings)
    album = get_album(parsed, timings)
    allowed, status, code = authorize(subject, album, "share", mode, timings)
    if not allowed:
        counters.add("denied")
        return reply(status, {"error": code}, timings)
    start = time.perf_counter()
    with pool.connection() as conn:
        with conn.transaction():
            existing = conn.execute(
                "SELECT id, status, version FROM authz_event WHERE idempotency_key=%s", (idem,)
            ).fetchone()
            if existing:
                timings.db_ms += (time.perf_counter() - start) * 1000
                return reply(202, {"operation_id": str(existing[0]), "status": existing[1], "version": existing[2]}, timings)
            locked = conn.execute("SELECT version, changing FROM album WHERE id=%s FOR UPDATE", (parsed,)).fetchone()
            if not locked or locked[1]:
                timings.db_ms += (time.perf_counter() - start) * 1000
                return reply(503, {"error": "authorization_pending"}, timings)
            present = conn.execute(
                "SELECT 1 FROM album_share WHERE album_id=%s AND user_subject=%s AND permission='viewer'",
                (parsed, target),
            ).fetchone() is not None
            desired = operation == "add"
            if present == desired:
                timings.db_ms += (time.perf_counter() - start) * 1000
                return reply(200, {"status": "already_applied", "version": locked[0]}, timings)
            version = locked[0] + 1
            if desired:
                conn.execute(
                    "INSERT INTO album_share(album_id,user_subject,permission,version) VALUES(%s,%s,'viewer',%s)",
                    (parsed, target, version),
                )
            else:
                conn.execute(
                    "DELETE FROM album_share WHERE album_id=%s AND user_subject=%s AND permission='viewer'",
                    (parsed, target),
                )
            event_id = uuid.uuid4()
            conn.execute("UPDATE album SET version=%s, changing=true WHERE id=%s", (version, parsed))
            conn.execute(
                """INSERT INTO authz_event(id,idempotency_key,album_id,user_subject,relation,operation,version,status)
                   VALUES(%s,%s,%s,%s,'viewer',%s,%s,'pending')""",
                (event_id, idem, parsed, target, operation, version),
            )
    timings.db_ms += (time.perf_counter() - start) * 1000
    counters.add("allowed")
    return reply(202, {"operation_id": str(event_id), "status": "pending", "version": version}, timings)


@app.post("/albums/{album_id}/shares")
async def add_share(album_id: str, request: Request, mode: str = "direct"):
    return await change_share(album_id, request, mode, "add")


@app.delete("/albums/{album_id}/shares")
async def remove_share(album_id: str, request: Request, mode: str = "direct"):
    return await change_share(album_id, request, mode, "delete")


@app.get("/operations/{operation_id}")
def operation_status(operation_id: str, request: Request):
    counters.add("requests")
    timings = Timings()
    subject = authenticate(request, timings)
    if subject is None:
        return reply(401, {"error": "unauthorized"}, timings)
    try:
        event_id = uuid.UUID(operation_id)
    except Exception:
        return reply(400, {"error": "invalid_request"}, timings)
    start = time.perf_counter()
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT e.status,e.version,e.attempts,e.last_error_code,a.owner_subject
               FROM authz_event e JOIN album a ON a.id=e.album_id WHERE e.id=%s""",
            (event_id,),
        ).fetchone()
    timings.db_ms += (time.perf_counter() - start) * 1000
    if not row or row[4] != subject:
        return reply(403, {"error": "forbidden"}, timings)
    return reply(200, {"status": row[0], "version": row[1], "attempts": row[2], "last_error_code": row[3]}, timings)
