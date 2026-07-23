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

STATUS_HEADERS_ONLY = "headers_only"
STATUS_FETCHED = "fetched"
STATUS_CHECKED = "checked"
STATUS_FETCHED_NO_BODY = "fetched_no_body"
HEADER_FILTER_STATUSES = (STATUS_HEADERS_ONLY, STATUS_FETCHED_NO_BODY)

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
    is_read INTEGER DEFAULT 0,
    is_starred INTEGER DEFAULT 0,
    is_sent INTEGER DEFAULT 0,
    gm_thrid TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_status ON emails(status);
CREATE INDEX IF NOT EXISTS idx_category ON emails(category);
CREATE INDEX IF NOT EXISTS idx_date ON emails(date_parsed);
CREATE INDEX IF NOT EXISTS idx_thread ON emails(thread_id);
CREATE INDEX IF NOT EXISTS idx_in_reply_to ON emails(in_reply_to);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


_LIST_COLUMNS = (
    "message_id_hash, message_id, sender, subject, date, date_parsed, "
    "status, category, keyword_matches, thread_id, in_reply_to, is_read, is_starred, is_sent"
)


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


_SCHEMA_VERSION = 2


def _migrate(db_path: str) -> None:
    with _connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= _SCHEMA_VERSION:
            return

        required = {
            "is_read": "INTEGER DEFAULT 0",
            "is_starred": "INTEGER DEFAULT 0",
            "is_sent": "INTEGER DEFAULT 0",
            "gm_thrid": "TEXT",
        }
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(emails)").fetchall()}
        for column, decl in required.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE emails ADD COLUMN {column} {decl}")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_in_reply_to ON emails(in_reply_to)")
        conn.execute("UPDATE emails SET thread_id = message_id_hash WHERE thread_id IS NULL")
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def init_db(db_path: str) -> None:
    Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
    _migrate(db_path)


_BATCH_CHUNK = 500


def _run_in_batches(conn, sql_template: str, hashes: list[str], leading=()):
    for i in range(0, len(hashes), _BATCH_CHUNK):
        chunk = hashes[i : i + _BATCH_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        yield conn.execute(sql_template.format(placeholders=placeholders), [*leading, *chunk])


def _batch_existing_hashes(conn, hashes: list[str]) -> set[str]:
    existing = set()
    for cursor in _run_in_batches(
        conn,
        "SELECT message_id_hash FROM emails WHERE message_id_hash IN ({placeholders})",
        hashes,
    ):
        existing.update(row["message_id_hash"] for row in cursor.fetchall())
    return existing


def delete_email(message_id: str, db_path: str) -> bool:
    message_id_hash = _hash_message_id(message_id)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM emails WHERE message_id_hash = ?",
            (message_id_hash,),
        )
    return cursor.rowcount > 0


def delete_emails_by_hashes(db_path: str, message_id_hashes: list[str]) -> int:
    if not message_id_hashes:
        return 0
    removed = 0
    with _connect(db_path) as conn:
        for cursor in _run_in_batches(
            conn,
            "DELETE FROM emails WHERE message_id_hash IN ({placeholders})",
            message_id_hashes,
        ):
            removed += cursor.rowcount
    return removed


def reconcile_inbox(
    db_path: str,
    server_hashes: set[str],
    protected_hashes: set[str] | None = None,
    searched_count: int = 0,
    min_ratio: float = 0.9,
    force: bool = False,
) -> int:
    if not force:
        if searched_count and len(server_hashes) / searched_count < min_ratio:
            logger.warning(
                "Skipping reconcile: fetched %d of %d UIDs (<%.0f%%)",
                len(server_hashes),
                searched_count,
                min_ratio * 100,
            )
            return 0
        if protected_hashes is None:
            logger.warning("Skipping reconcile: no protected_hashes (All Mail sync missing)")
            return 0

    with _connect(db_path) as conn:
        rows = conn.execute("SELECT message_id_hash FROM emails WHERE COALESCE(is_sent, 0) = 0").fetchall()
    cached_hashes = {row["message_id_hash"] for row in rows}
    ghosts = cached_hashes - server_hashes
    if protected_hashes:
        ghosts -= protected_hashes
    ghosts = list(ghosts)
    if not ghosts:
        return 0

    if not force:
        cached_count = len(cached_hashes)
        if cached_count >= 20 and len(ghosts) / cached_count > 0.25:
            logger.warning(
                "Skipping reconcile: ghosts %d > 25%% of %d cached",
                len(ghosts),
                cached_count,
            )
            return 0

    return delete_emails_by_hashes(db_path, ghosts)


_FLAG_COLUMNS = frozenset({"is_read", "is_starred"})


def _set_flag_by_hashes(db_path: str, message_id_hashes: list[str], column: str, on: bool) -> int:
    if column not in _FLAG_COLUMNS:
        raise ValueError(f"invalid flag column: {column!r}")
    if not message_id_hashes:
        return 0
    val = 1 if on else 0
    updated = 0
    with _connect(db_path) as conn:
        for cursor in _run_in_batches(
            conn,
            f"UPDATE emails SET {column} = ? WHERE message_id_hash IN ({{placeholders}})",
            message_id_hashes,
            leading=[val],
        ):
            updated += cursor.rowcount
    return updated


def set_read_by_hashes(db_path: str, message_id_hashes: list[str], read: bool) -> int:
    return _set_flag_by_hashes(db_path, message_id_hashes, "is_read", read)


def set_starred_by_hashes(db_path: str, message_id_hashes: list[str], starred: bool) -> int:
    return _set_flag_by_hashes(db_path, message_id_hashes, "is_starred", starred)


def update_thread_id(db_path: str, message_id_hash: str, thread_id: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE emails SET thread_id = ? WHERE message_id_hash = ?",
            (thread_id, message_id_hash),
        )


def refresh_thread_ids(updates: list[tuple[str | None, str | None, str]], db_path: str) -> int:
    if not updates:
        return 0
    changed = 0
    with _connect(db_path) as conn:
        for thread_id, gm_thrid, message_id_hash in updates:
            if thread_id is None:
                continue
            cur = conn.execute(
                "UPDATE emails SET thread_id = ?, gm_thrid = COALESCE(?, gm_thrid) "
                "WHERE message_id_hash = ? AND (thread_id IS NOT ? OR gm_thrid IS NULL)",
                (thread_id, gm_thrid, message_id_hash, thread_id),
            )
            changed += cur.rowcount
    return changed


def get_message_ids_by_hashes(db_path: str, message_id_hashes: list[str]) -> list[tuple[str, str]]:
    if not message_id_hashes:
        return []
    pairs: list[tuple[str, str]] = []
    with _connect(db_path) as conn:
        for cursor in _run_in_batches(
            conn,
            "SELECT message_id_hash, message_id FROM emails WHERE message_id_hash IN ({placeholders})",
            message_id_hashes,
        ):
            pairs.extend((row["message_id_hash"], row["message_id"]) for row in cursor.fetchall())
    return pairs


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
        thread_id = email_data.get("thread_id") or message_id_hash
        prepared.append(
            (
                message_id_hash,
                message_id,
                email_data.get("from", ""),
                email_data.get("subject", ""),
                email_data.get("date", ""),
                date_parsed,
                thread_id,
                email_data.get("in_reply_to"),
                email_data.get("gm_thrid"),
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
                   (message_id_hash, message_id, sender, subject, date, date_parsed, body, status, category, keyword_matches, thread_id, in_reply_to, gm_thrid)
                   VALUES (?, ?, ?, ?, ?, ?, '', 'headers_only', NULL, NULL, ?, ?, ?)""",
                insert_rows,
            )

    return len(insert_rows)


def update_bodies_batch(updates: list[tuple], db_path: str) -> int:
    if not updates:
        return 0
    simple_rows = []
    flag_rows = []
    for entry in updates:
        if len(entry) == 4:
            mid, body, is_read, is_starred = entry
            flag_rows.append(
                (
                    body,
                    STATUS_FETCHED_NO_BODY if not (body or "").strip() else STATUS_FETCHED,
                    int(bool(is_read)),
                    int(bool(is_starred)),
                    _hash_message_id(mid),
                )
            )
        else:
            mid, body = entry[:2]
            simple_rows.append(
                (
                    body,
                    STATUS_FETCHED_NO_BODY if not (body or "").strip() else STATUS_FETCHED,
                    _hash_message_id(mid),
                )
            )

    affected = 0
    with _connect(db_path) as conn:
        if simple_rows:
            cursor = conn.executemany(
                "UPDATE emails SET body = ?, status = ? WHERE message_id_hash = ?",
                simple_rows,
            )
            affected += cursor.rowcount
        if flag_rows:
            cursor = conn.executemany(
                "UPDATE emails SET body = ?, status = ?, is_read = ?, is_starred = ? "
                "WHERE message_id_hash = ?",
                flag_rows,
            )
            affected += cursor.rowcount
    return affected


def update_flags_batch(updates: list[tuple], db_path: str) -> int:
    if not updates:
        return 0
    rows = [
        (int(bool(is_read)), int(bool(is_starred)), message_id_hash) for is_read, is_starred, message_id_hash in updates
    ]
    affected = 0
    with _connect(db_path) as conn:
        cursor = conn.executemany(
            "UPDATE emails SET is_read = ?, is_starred = ? WHERE message_id_hash = ?",
            rows,
        )
        affected = cursor.rowcount
    return affected


def mark_sent(message_id_hashes: list[str], db_path: str) -> int:
    if not message_id_hashes:
        return 0
    affected = 0
    with _connect(db_path) as conn:
        for cursor in _run_in_batches(
            conn,
            "UPDATE emails SET is_sent = 1 WHERE message_id_hash IN ({placeholders})",
            message_id_hashes,
        ):
            affected += cursor.rowcount
    return affected


def get_headers_only_message_ids(db_path: str) -> list[str]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT message_id FROM emails WHERE status = ?", (STATUS_HEADERS_ONLY,)
        ).fetchall()
    return [row["message_id"] for row in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    def _col(name: str, default=0):
        try:
            return row[name]
        except (IndexError, KeyError):
            return default

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
        "is_read": int(_col("is_read", 0) or 0),
        "is_starred": int(_col("is_starred", 0) or 0),
        "is_sent": int(_col("is_sent", 0) or 0),
        "gm_thrid": _col("gm_thrid", None),
    }
