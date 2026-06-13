import functools
import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
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
_CONNECTION_MAX_AGE = 300


def _get_conn(db_path: str) -> sqlite3.Connection:
    key = f"_conn_{db_path}"
    ts_key = f"_conn_ts_{db_path}"
    conn = getattr(_local, key, None)
    now = time.monotonic()
    if conn is not None:
        age = now - getattr(_local, ts_key, 0)
        if age > _CONNECTION_MAX_AGE:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
    if conn is None:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA mmap_size=268435456")
        conn.execute("PRAGMA busy_timeout=5000")
        setattr(_local, key, conn)
        setattr(_local, ts_key, now)
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
            key = f"_conn_{db_path}"
            try:
                conn.close()
            except Exception:
                pass
            setattr(_local, key, None)
        raise


def _migrate_thread_id(db_path: str) -> None:
    with _connect(db_path) as conn:
        try:
            conn.execute("ALTER TABLE emails ADD COLUMN thread_id TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE emails ADD COLUMN in_reply_to TEXT")
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_thread ON emails(thread_id)")
        except Exception:
            pass


def init_db(db_path: str) -> None:
    Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
    _migrate_thread_id(db_path)


def save_email(email_data: dict, db_path: str) -> bool:
    message_id = email_data.get("message_id", "")
    if not message_id:
        return False
    message_id_hash = _hash_message_id(message_id)
    date_parsed = _parse_date_iso(email_data.get("date", ""))
    keyword_matches = email_data.get("keyword_matches")
    keyword_json = json.dumps(keyword_matches, ensure_ascii=False) if keyword_matches else None
    category = email_data.get("_category")
    thread_id = email_data.get("thread_id")
    in_reply_to = email_data.get("in_reply_to")

    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT message_id_hash FROM emails WHERE message_id_hash = ?",
            (message_id_hash,),
        ).fetchone()
        if existing:
            updates = []
            params = []
            if keyword_json is not None:
                updates.append("keyword_matches = ?")
                params.append(keyword_json)
            if category is not None:
                updates.append("category = ?")
                params.append(category)
            if updates:
                params.append(message_id_hash)
                conn.execute(f"UPDATE emails SET {', '.join(updates)} WHERE message_id_hash = ?", params)
            return False

        conn.execute(
            """INSERT OR IGNORE INTO emails
               (message_id_hash, message_id, sender, subject, date, date_parsed, body, status, category, keyword_matches, thread_id, in_reply_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'fetched', ?, ?, ?, ?)""",
            (
                message_id_hash,
                message_id,
                email_data.get("from", ""),
                email_data.get("subject", ""),
                email_data.get("date", ""),
                date_parsed,
                email_data.get("body", ""),
                category,
                keyword_json,
                thread_id,
                in_reply_to,
            ),
        )
    return True


_BATCH_CHUNK = 500


def _batch_existing_hashes(conn, hashes: list[str]) -> set[str]:
    existing = set()
    for i in range(0, len(hashes), _BATCH_CHUNK):
        chunk = hashes[i:i + _BATCH_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT message_id_hash FROM emails WHERE message_id_hash IN ({placeholders})",
            chunk,
        ).fetchall()
        existing.update(row["message_id_hash"] for row in rows)
    return existing


def save_emails_batch(emails: list[dict], db_path: str) -> int:
    prepared = []
    for email_data in emails:
        message_id = email_data.get("message_id", "")
        if not message_id:
            continue
        message_id_hash = _hash_message_id(message_id)
        date_parsed = _parse_date_iso(email_data.get("date", ""))
        keyword_matches = email_data.get("keyword_matches")
        keyword_json = json.dumps(keyword_matches, ensure_ascii=False) if keyword_matches else None
        prepared.append({
            "hash": message_id_hash,
            "message_id": message_id,
            "from": email_data.get("from", ""),
            "subject": email_data.get("subject", ""),
            "date": email_data.get("date", ""),
            "date_parsed": date_parsed,
            "body": email_data.get("body", ""),
            "keyword_json": keyword_json,
            "category": email_data.get("_category"),
            "thread_id": email_data.get("thread_id"),
            "in_reply_to": email_data.get("in_reply_to"),
        })

    if not prepared:
        return 0

    has_updates = any(p["keyword_json"] is not None or p["category"] is not None for p in prepared)

    with _connect(db_path) as conn:
        if has_updates:
            all_hashes = [p["hash"] for p in prepared]
            existing_hashes = _batch_existing_hashes(conn, all_hashes)
        else:
            existing_hashes = set()

        insert_rows = []
        update_rows_kw = []
        update_rows_cat = []
        update_rows_both = []

        for p in prepared:
            h = p["hash"]
            if h in existing_hashes:
                has_kw = p["keyword_json"] is not None
                has_cat = p["category"] is not None
                if has_kw and has_cat:
                    update_rows_both.append((p["keyword_json"], p["category"], h))
                elif has_kw:
                    update_rows_kw.append((p["keyword_json"], h))
                elif has_cat:
                    update_rows_cat.append((p["category"], h))
            else:
                insert_rows.append((
                    h, p["message_id"], p["from"], p["subject"], p["date"],
                    p["date_parsed"], p["body"], p["category"], p["keyword_json"],
                    p["thread_id"], p["in_reply_to"],
                ))

        if insert_rows:
            conn.executemany(
                """INSERT OR IGNORE INTO emails
                   (message_id_hash, message_id, sender, subject, date, date_parsed, body, status, category, keyword_matches, thread_id, in_reply_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'fetched', ?, ?, ?, ?)""",
                insert_rows,
            )
        if update_rows_both:
            conn.executemany(
                "UPDATE emails SET keyword_matches = ?, category = ? WHERE message_id_hash = ?",
                update_rows_both,
            )
        if update_rows_kw:
            conn.executemany(
                "UPDATE emails SET keyword_matches = ? WHERE message_id_hash = ?",
                update_rows_kw,
            )
        if update_rows_cat:
            conn.executemany(
                "UPDATE emails SET category = ? WHERE message_id_hash = ?",
                update_rows_cat,
            )

    return len(insert_rows)


def delete_email(message_id: str, db_path: str) -> bool:
    message_id_hash = _hash_message_id(message_id)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM emails WHERE message_id_hash = ?",
            (message_id_hash,),
        )
    return cursor.rowcount > 0


def read_emails(db_path: str, max_emails: int | None = None, since_date: str | None = None, include_body: bool = True) -> list[dict]:
    cols = "*" if include_body else _LIST_COLUMNS
    with _connect(db_path) as conn:
        query = f"SELECT {cols} FROM emails"
        params: list = []
        conditions = []

        if since_date:
            try:
                since_dt = datetime.strptime(since_date, "%d-%b-%Y").replace(tzinfo=timezone.utc)
                since_iso = since_dt.isoformat()
                conditions.append("date_parsed >= ?")
                params.append(since_iso)
            except ValueError:
                pass

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY date_parsed DESC"
        if max_emails is not None:
            query += " LIMIT ?"
            params.append(max_emails)

        rows = conn.execute(query, params).fetchall()

    results = [_row_to_dict(row, include_body=include_body) for row in rows]
    return results


def save_headers_batch(emails: list[dict], db_path: str) -> int:
    prepared = []
    for email_data in emails:
        message_id = email_data.get("message_id", "")
        if not message_id:
            continue
        message_id_hash = _hash_message_id(message_id)
        date_parsed = _parse_date_iso(email_data.get("date", ""))
        prepared.append((
            message_id_hash,
            message_id,
            email_data.get("from", ""),
            email_data.get("subject", ""),
            email_data.get("date", ""),
            date_parsed,
            email_data.get("thread_id"),
            email_data.get("in_reply_to"),
        ))

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
    rows = [(body, _hash_message_id(mid)) for mid, body in updates]
    with _connect(db_path) as conn:
        cursor = conn.executemany(
            "UPDATE emails SET body = ?, status = 'fetched' "
            "WHERE message_id_hash = ?",
            rows,
        )
    return cursor.rowcount


def get_headers_only_message_ids(db_path: str) -> list[str]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT message_id FROM emails WHERE status = 'headers_only'"
        ).fetchall()
    return [row["message_id"] for row in rows]


def update_email_body(message_id: str, body: str, db_path: str) -> bool:
    message_id_hash = _hash_message_id(message_id)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """UPDATE emails SET body = ?, status = 'fetched'
               WHERE message_id_hash = ?""",
            (body, message_id_hash),
        )
    return cursor.rowcount > 0


def _row_to_dict(row: sqlite3.Row, include_body: bool = True) -> dict:
    d = {
        "message_id": row["message_id"],
        "from": row["sender"],
        "subject": row["subject"],
        "date": row["date"],
        "body": (row["body"] or "") if include_body else "",
        "status": row["status"],
    }
    d["keyword_matches"] = _parse_keyword_matches(row["keyword_matches"])
    d["thread_id"] = row["thread_id"]
    d["in_reply_to"] = row["in_reply_to"]
    return d
