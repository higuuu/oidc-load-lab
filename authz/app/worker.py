import os
import time

import httpx
from psycopg_pool import ConnectionPool


pool = ConnectionPool(
    kwargs={
        "host": os.environ["APP_DB_HOST"],
        "dbname": os.environ["APP_DB_NAME"],
        "user": os.environ["APP_DB_USER"],
        "password": os.environ["APP_DB_PASSWORD"],
        "sslmode": "disable",
    },
    min_size=1,
    max_size=3,
    timeout=2,
)
base = os.environ["OPENFGA_URL"].rstrip("/")
store = os.environ["OPENFGA_STORE_ID"]
model = os.environ["OPENFGA_MODEL_ID"]
client = httpx.Client(
    verify=os.environ["CA_FILE"],
    timeout=httpx.Timeout(1, connect=0.4),
    headers={"Authorization": f"Bearer {os.environ['OPENFGA_PRESHARED_KEY']}", "Content-Type": "application/json"},
    limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    trust_env=False,
)


def check(user, album):
    response = client.post(
        f"{base}/stores/{store}/check",
        json={
            "authorization_model_id": model,
            "tuple_key": {"user": f"user:{user}", "relation": "viewer", "object": f"album:{album}"},
            "consistency": "HIGHER_CONSISTENCY",
        },
    )
    response.raise_for_status()
    return bool(response.json().get("allowed"))


def process_one():
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT e.id,e.album_id,e.user_subject,e.operation,e.version,a.version
               FROM authz_event e JOIN album a ON a.id=e.album_id
               WHERE e.status='pending' ORDER BY e.created_at,e.id LIMIT 1"""
        ).fetchone()
    if not row:
        return False
    event_id, album_id, user, operation, event_version, album_version = row
    if event_version != album_version:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE authz_event SET status='superseded',applied_at=now(),last_error_code='stale_version' WHERE id=%s AND status='pending'",
                (event_id,),
            )
        return True
    desired = operation == "add"
    try:
        current = check(user, album_id)
        if current != desired:
            key = {"user": f"user:{user}", "relation": "viewer", "object": f"album:{album_id}"}
            field = "writes" if desired else "deletes"
            response = client.post(
                f"{base}/stores/{store}/write",
                json={"authorization_model_id": model, field: {"tuple_keys": [key]}},
            )
            response.raise_for_status()
        if check(user, album_id) != desired:
            raise RuntimeError("confirmation_mismatch")
        with pool.connection() as conn:
            with conn.transaction():
                changed = conn.execute(
                    """UPDATE authz_event SET status='applied',attempts=attempts+1,last_error_code=NULL,applied_at=now()
                       WHERE id=%s AND status='pending' RETURNING album_id,version""",
                    (event_id,),
                ).fetchone()
                if changed:
                    conn.execute(
                        "UPDATE album SET changing=false WHERE id=%s AND version=%s",
                        (changed[0], changed[1]),
                    )
    except Exception:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE authz_event SET attempts=attempts+1,last_error_code='dependency_error' WHERE id=%s AND status='pending'",
                (event_id,),
            )
        time.sleep(0.5)
    return True


try:
    while True:
        if not process_one():
            time.sleep(0.1)
except KeyboardInterrupt:
    pass
finally:
    client.close()
    pool.close()
