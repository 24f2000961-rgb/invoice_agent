import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from . import config

_lock = threading.RLock()
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _init_schema(_conn)
    return _conn


def _init_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            principal TEXT NOT NULL,
            context_id TEXT NOT NULL,
            batch_id TEXT,
            state TEXT NOT NULL,
            artifacts TEXT NOT NULL,
            history TEXT NOT NULL,
            proposals TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS idempotency (
            principal TEXT NOT NULL,
            message_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            task_id TEXT NOT NULL,
            response_json TEXT NOT NULL,
            PRIMARY KEY (principal, message_id)
        );

        CREATE TABLE IF NOT EXISTS package_cache (
            content_hash TEXT PRIMARY KEY,
            action_id TEXT NOT NULL,
            decision_json TEXT NOT NULL
        );
        """
    )
    conn.commit()


@contextmanager
def txn():
    """Coarse global lock + sqlite transaction. Good enough for a single-process
    deployment and sufficient to make the cancel-vs-result race and concurrent
    idempotent replays deterministic."""
    with _lock:
        conn = _get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# ---------- idempotency ----------

def get_idempotency(conn, principal: str, message_id: str):
    row = conn.execute(
        "SELECT * FROM idempotency WHERE principal=? AND message_id=?",
        (principal, message_id),
    ).fetchone()
    return dict(row) if row else None


def put_idempotency(conn, principal: str, message_id: str, content_hash: str, task_id: str, response_obj: dict):
    conn.execute(
        "INSERT INTO idempotency (principal, message_id, content_hash, task_id, response_json) "
        "VALUES (?,?,?,?,?)",
        (principal, message_id, content_hash, task_id, json.dumps(response_obj)),
    )


# ---------- package decision cache (canonical content -> decision) ----------

def get_cached_decision(conn, content_hash: str):
    row = conn.execute(
        "SELECT action_id, decision_json FROM package_cache WHERE content_hash=?",
        (content_hash,),
    ).fetchone()
    if not row:
        return None
    return row["action_id"], json.loads(row["decision_json"])


def put_cached_decision(conn, content_hash: str, action_id: str, decision: dict):
    conn.execute(
        "INSERT OR IGNORE INTO package_cache (content_hash, action_id, decision_json) VALUES (?,?,?)",
        (content_hash, action_id, json.dumps(decision)),
    )


# ---------- tasks ----------

def create_task(conn, task_id, principal, context_id, batch_id, state, artifacts, history, proposals):
    now = time.time()
    conn.execute(
        "INSERT INTO tasks (id, principal, context_id, batch_id, state, artifacts, history, proposals, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            task_id, principal, context_id, batch_id, state,
            json.dumps(artifacts), json.dumps(history), json.dumps(proposals),
            now, now,
        ),
    )


def get_task_row(conn, task_id):
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(conn, principal):
    rows = conn.execute(
        "SELECT * FROM tasks WHERE principal=? ORDER BY created_at ASC", (principal,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_task(conn, task_id, *, state=None, artifacts=None, history=None):
    fields, values = [], []
    if state is not None:
        fields.append("state=?")
        values.append(state)
    if artifacts is not None:
        fields.append("artifacts=?")
        values.append(json.dumps(artifacts))
    if history is not None:
        fields.append("history=?")
        values.append(json.dumps(history))
    fields.append("updated_at=?")
    values.append(time.time())
    values.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)


TERMINAL_STATES = {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"}
