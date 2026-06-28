import functools
import hashlib
import logging
import sqlite3
import threading
from datetime import timezone
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path

from src.scripts.utils import _parse_keyword_matches

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id_hash TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    sender TEXT,
    subject TEXT,
    date TEXT,
    date_parsed TEXT,
    body TEXT,
    status TEXT DEFAULT 'fetched',
    category TEXT,
    keyword_matches TEXT,
    thread_id TEXT,
    in_reply_to TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_status ON emails(status);
CREATE INDEX IF NOT EXISTS idx_category ON emails(category);
CREATE INDEX IF NOT EXISTS idx_date ON emails(date_parsed);
CREATE INDEX IF NOT EXISTS idx_thread ON emails(thread_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


_LIST_COLUMNS = (
    "message_id_hash, message_id, sender, subject, date, date_parsed, "
    "status, category, keyword_matches, thread_id, in_reply_to"
)


@functools.lru_cache(maxsize=4096)
def _hash_message_id(message_id: str) -> str:
    return hashlib.sha256(message_id.encode()).hexdigest()[:16]


def _parse_date_iso(date_str: str) -> str | None:
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


_local = threading.local()
_conn_lock = threading.Lock()
_all_conns: set[sqlite3.Connection] = set()


def close_all_connections() -> None:
    with _conn_lock:
        conns = list(_all_conns)
        _all_conns.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception:
            pass
    for key in list(vars(_local)):
        if isinstance(getattr(_local, key, None), sqlite3.Connection):
            setattr(_local, key, None)


def _get_conn(db_path: str) -> sqlite3.Connection:
    key = f"_conn_{db_path}"
    conn = getattr(_local, key, None)
    if conn is None:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA mmap_size=268435456")
        conn.execute("PRAGMA busy_timeout=5000")
        with _conn_lock:
            _all_conns.add(conn)
        setattr(_local, key, conn)
    return conn


@contextmanager
def _connect(db_path: str):
    conn = _get_conn(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            with _conn_lock:
                _all_conns.discard(conn)
            key = f"_conn_{db_path}"
            try:
                conn.close()
            except Exception:
                pass
            setattr(_local, key, None)
        raise


def init_db(db_path: str) -> None:
    Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


_BATCH_CHUNK = 500


def _batch_existing_hashes(conn, hashes: list[str]) -> set[str]:
    existing = set()
    for i in range(0, len(hashes), _BATCH_CHUNK):
        chunk = hashes[i : i + _BATCH_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT message_id_hash FROM emails WHERE message_id_hash IN ({placeholders})",
            chunk,
        ).fetchall()
        existing.update(row["message_id_hash"] for row in rows)
    return existing


def delete_email(message_id: str, db_path: str) -> bool:
    message_id_hash = _hash_message_id(message_id)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM emails WHERE message_id_hash = ?",
            (message_id_hash,),
        )
    return cursor.rowcount > 0


def clear_emails(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM emails")


def read_emails(db_path: str) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM emails ORDER BY date_parsed DESC").fetchall()
    return [_row_to_dict(row) for row in rows]


def save_headers_batch(emails: list[dict], db_path: str) -> int:
    prepared = []
    for email_data in emails:
        message_id = email_data.get("message_id", "")
        if not message_id:
            continue
        message_id_hash = _hash_message_id(message_id)
        date_parsed = _parse_date_iso(email_data.get("date", ""))
        prepared.append(
            (
                message_id_hash,
                message_id,
                email_data.get("from", ""),
                email_data.get("subject", ""),
                email_data.get("date", ""),
                date_parsed,
                email_data.get("thread_id"),
                email_data.get("in_reply_to"),
            )
        )

    if not prepared:
        return 0

    with _connect(db_path) as conn:
        all_hashes = [p[0] for p in prepared]
        existing_hashes = _batch_existing_hashes(conn, all_hashes)

        insert_rows = [p for p in prepared if p[0] not in existing_hashes]

        if insert_rows:
            conn.executemany(
                """INSERT OR IGNORE INTO emails
                   (message_id_hash, message_id, sender, subject, date, date_parsed, body, status, category, keyword_matches, thread_id, in_reply_to)
                   VALUES (?, ?, ?, ?, ?, ?, '', 'headers_only', NULL, NULL, ?, ?)""",
                insert_rows,
            )

    return len(insert_rows)


def update_bodies_batch(updates: list[tuple[str, str]], db_path: str) -> int:
    if not updates:
        return 0
    rows = [
        (body, "fetched_no_body" if not (body or "").strip() else "fetched", _hash_message_id(mid))
        for mid, body in updates
    ]
    with _connect(db_path) as conn:
        cursor = conn.executemany(
            "UPDATE emails SET body = ?, status = ? WHERE message_id_hash = ?",
            rows,
        )
    return cursor.rowcount


def get_headers_only_message_ids(db_path: str) -> list[str]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT message_id FROM emails WHERE status = 'headers_only'").fetchall()
    return [row["message_id"] for row in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "message_id": row["message_id"],
        "from": row["sender"],
        "subject": row["subject"],
        "date": row["date"],
        "body": row["body"] or "",
        "status": row["status"],
        "keyword_matches": _parse_keyword_matches(row["keyword_matches"]),
        "thread_id": row["thread_id"],
        "in_reply_to": row["in_reply_to"],
    }
