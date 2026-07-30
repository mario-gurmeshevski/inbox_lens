import uuid

from src.scripts.cache.db import _connect
from src.scripts.constants import DB_PATH

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_FAILED = "failed"
STALE_SENDING_TTL_SECONDS = 300


def _row_to_outbox(row) -> dict:
    return {
        "id": row["id"],
        "to_addr": row["to_addr"] or "",
        "cc": row["cc"] or "",
        "bcc": row["bcc"] or "",
        "subject": row["subject"] or "",
        "body": row["body"] or "",
        "body_html": row["body_html"] or "",
        "mode": row["mode"] or "new",
        "original_message_id": row["original_message_id"],
        "thread_id": row["thread_id"],
        "draft_id": row["draft_id"],
        "status": row["status"] or STATUS_PENDING,
        "attempts": int(row["attempts"] or 0),
        "last_error": row["last_error"],
        "next_attempt_at": row["next_attempt_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def enqueue(payload: dict, db_path: str = DB_PATH) -> str:
    outbox_id = payload.get("id") or uuid.uuid4().hex
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO outbox
               (id, to_addr, cc, bcc, subject, body, body_html, mode,
                original_message_id, thread_id, draft_id, status, attempts,
                next_attempt_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL,
                       datetime('now'), datetime('now'))""",
            (
                outbox_id,
                payload.get("to_addr") or "",
                payload.get("cc") or "",
                payload.get("bcc") or "",
                payload.get("subject") or "",
                payload.get("body") or "",
                payload.get("body_html") or "",
                payload.get("mode") or "new",
                payload.get("original_message_id"),
                payload.get("thread_id"),
                payload.get("draft_id"),
                STATUS_PENDING,
            ),
        )
    return outbox_id


def get(outbox_id: str, db_path: str = DB_PATH) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM outbox WHERE id = ?", (outbox_id,)).fetchone()
    return _row_to_outbox(row) if row else None


def list_outbox(db_path: str = DB_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM outbox ORDER BY created_at DESC").fetchall()
    return [_row_to_outbox(row) for row in rows]


def get_due(db_path: str = DB_PATH, limit: int = 20) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM outbox WHERE status = ? "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= datetime('now')) "
            "ORDER BY created_at ASC LIMIT ?",
            (STATUS_PENDING, int(limit)),
        ).fetchall()
    return [_row_to_outbox(row) for row in rows]


def count_pending(db_path: str = DB_PATH) -> int:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM outbox WHERE status IN (?, ?)",
            (STATUS_PENDING, STATUS_FAILED),
        ).fetchone()
    return int(row["n"] if row else 0)


def reclaim_stale_sending(db_path: str = DB_PATH, max_age_seconds: int = STALE_SENDING_TTL_SECONDS) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE outbox SET status = ?, next_attempt_at = NULL, "
            "updated_at = datetime('now') WHERE status = ? "
            "AND updated_at < datetime('now', ?)",
            (STATUS_PENDING, STATUS_SENDING, f"-{int(max_age_seconds)} seconds"),
        )
    return cursor.rowcount


def mark_attempting(outbox_id: str, db_path: str = DB_PATH) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE outbox SET status = ?, updated_at = datetime('now') "
            "WHERE id = ? AND status = ?",
            (STATUS_SENDING, outbox_id, STATUS_PENDING),
        )
    return cursor.rowcount > 0


def mark_failed(
    outbox_id: str, error: str, next_attempt_at: str | None, db_path: str = DB_PATH
) -> bool:
    status = STATUS_FAILED if next_attempt_at is None else STATUS_PENDING
    truncated = (error or "")[:500]
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE outbox SET status = ?, attempts = attempts + 1, "
            "last_error = ?, next_attempt_at = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (status, truncated, next_attempt_at, outbox_id),
        )
    return cursor.rowcount > 0


def mark_pending(outbox_id: str, db_path: str = DB_PATH) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE outbox SET status = ?, next_attempt_at = NULL, "
            "updated_at = datetime('now') WHERE id = ?",
            (STATUS_PENDING, outbox_id),
        )
    return cursor.rowcount > 0


def update(outbox_id: str, fields: dict, db_path: str = DB_PATH) -> bool:
    allowed = (
        "to_addr",
        "cc",
        "bcc",
        "subject",
        "body",
        "body_html",
        "mode",
        "original_message_id",
        "thread_id",
        "draft_id",
    )
    sets = []
    values = []
    for key in allowed:
        if key in fields:
            sets.append(f"{key} = ?")
            values.append(fields[key])
    if not sets:
        return False
    sets.append("status = ?")
    values.append(STATUS_PENDING)
    sets.append("next_attempt_at = NULL")
    sets.append("updated_at = datetime('now')")
    values.append(outbox_id)
    with _connect(db_path) as conn:
        cursor = conn.execute(f"UPDATE outbox SET {', '.join(sets)} WHERE id = ?", values)
    return cursor.rowcount > 0


def delete(outbox_id: str, db_path: str = DB_PATH) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM outbox WHERE id = ?", (outbox_id,))
    return cursor.rowcount > 0
