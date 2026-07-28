from src.scripts.cache.db import _connect
from src.scripts.constants import DB_PATH


def _row_to_draft(row) -> dict:
    return {
        "id": row["id"],
        "to_addr": row["to_addr"] or "",
        "cc": row["cc"] or "",
        "bcc": row["bcc"] or "",
        "subject": row["subject"] or "",
        "body": row["body"] or "",
        "mode": row["mode"] or "new",
        "thread_id": row["thread_id"],
        "in_reply_to": row["in_reply_to"],
        "updated_at": row["updated_at"],
    }


def save_draft(draft: dict, db_path: str = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO drafts (id, to_addr, cc, bcc, subject, body, mode, thread_id, in_reply_to, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                   to_addr = excluded.to_addr,
                   cc = excluded.cc,
                   bcc = excluded.bcc,
                   subject = excluded.subject,
                   body = excluded.body,
                   mode = excluded.mode,
                   thread_id = excluded.thread_id,
                   in_reply_to = excluded.in_reply_to,
                   updated_at = datetime('now')""",
            (
                draft["id"],
                draft.get("to_addr") or "",
                draft.get("cc") or "",
                draft.get("bcc") or "",
                draft.get("subject") or "",
                draft.get("body") or "",
                draft.get("mode") or "new",
                draft.get("thread_id"),
                draft.get("in_reply_to"),
            ),
        )


def get_draft(draft_id: str, db_path: str = DB_PATH) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return _row_to_draft(row) if row else None


def list_drafts(db_path: str = DB_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM drafts ORDER BY updated_at DESC").fetchall()
    return [_row_to_draft(row) for row in rows]


def delete_draft(draft_id: str, db_path: str = DB_PATH) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
    return cursor.rowcount > 0


def prune_stale_drafts(db_path: str = DB_PATH, max_age_days: int = 30) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM drafts WHERE updated_at < datetime('now', ?)",
            (f"-{int(max_age_days)} days",),
        )
    return cursor.rowcount
