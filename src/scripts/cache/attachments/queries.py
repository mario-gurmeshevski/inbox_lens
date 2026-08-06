import logging
import sqlite3

from src.scripts.cache.db import _connect
from src.scripts.constants import DB_PATH

from .storage import _abs_path, _remove_file, ATTACHMENTS_DIR

logger = logging.getLogger(__name__)

OWNER_OUTBOX = "outbox"
OWNER_DRAFT = "draft"
OWNER_EMAIL = "email"
_VALID_OWNERS = (OWNER_OUTBOX, OWNER_DRAFT, OWNER_EMAIL)

TYPE_BUCKET_CLAUSES: dict[str, str] = {
    "images": "a.mime_type LIKE 'image/%'",
    "pdf": "a.mime_type = 'application/pdf'",
    "documents": (
        "a.mime_type IN ('text/plain','text/csv','application/rtf') "
        "OR a.mime_type LIKE 'application/vnd.openxmlformats-officedocument%' "
        "OR a.mime_type LIKE 'application/vnd.ms-%' "
        "OR a.mime_type LIKE 'application/vnd.oasis.opendocument.%'"
    ),
    "archives": (
        "a.mime_type IN ("
        "'application/zip','application/x-zip-compressed','application/gzip',"
        "'application/x-tar','application/x-7z-compressed',"
        "'application/x-rar-computed','application/x-rar')"
    ),
}


def _row_to_attachment(row) -> dict:
    return {
        "id": row["id"],
        "owner_type": row["owner_type"],
        "owner_id": row["owner_id"],
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "size": int(row["size"] or 0),
        "disk_path": row["disk_path"],
        "created_at": row["created_at"],
    }


def _insert_many(db_path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with _connect(db_path) as conn:
        conn.executemany(
            """INSERT INTO attachments
               (id, owner_type, owner_id, filename, mime_type, size, disk_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    row["id"],
                    row["owner_type"],
                    row["owner_id"],
                    row["filename"],
                    row["mime_type"],
                    row["size"],
                    row["disk_path"],
                )
                for row in rows
            ],
        )


def list_for(db_path: str, owner_id: str, owner_type: str) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM attachments WHERE owner_type = ? AND owner_id = ? ORDER BY created_at ASC",
            (owner_type, owner_id),
        ).fetchall()
    return [_row_to_attachment(row) for row in rows]


def list_for_owners(db_path: str, owner_ids: list[str], owner_type: str) -> dict[str, list[dict]]:
    if not owner_ids:
        return {}
    placeholders = ",".join("?" for _ in owner_ids)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM attachments WHERE owner_type = ? AND owner_id IN ({placeholders}) ORDER BY created_at ASC",
            (owner_type, *owner_ids),
        ).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["owner_id"], []).append(_row_to_attachment(row))
    return out


def _bucket_clause(bucket: str) -> str | None:
    if bucket in TYPE_BUCKET_CLAUSES:
        return TYPE_BUCKET_CLAUSES[bucket]
    if bucket == "other":
        known = " OR ".join(f"({c})" for c in TYPE_BUCKET_CLAUSES.values())
        return f"NOT ({known})"
    return None


def search_received_attachments(
    db_path: str,
    search: str | None = None,
    type_bucket: str | None = None,
    size_sort: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int, int]:
    conditions = ["a.owner_type = ?"]
    params: list = [OWNER_EMAIL]
    if search:
        conditions.append("a.filename LIKE ? ESCAPE '\\'")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    if type_bucket:
        clause = _bucket_clause(type_bucket)
        if clause:
            conditions.append(clause)
    where = " AND ".join(conditions)
    if size_sort == "asc":
        order = "a.size ASC"
    elif size_sort == "desc":
        order = "a.size DESC"
    else:
        order = "a.created_at DESC"
    offset = max(0, (page - 1) * page_size)

    select_sql = (
        "SELECT a.id, a.owner_id, a.filename, a.mime_type, a.size, a.created_at, "
        "e.sender, e.subject, e.date "
        "FROM attachments a "
        "LEFT JOIN emails e ON e.message_id_hash = a.owner_id "
        f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?"
    )
    count_sql = f"SELECT COUNT(*) FROM attachments a WHERE {where}"
    with _connect(db_path) as conn:
        total_rows = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(select_sql, params + [page_size, offset]).fetchall()
    items = [dict(row) for row in rows]
    total_pages = max(1, -(-total_rows // page_size))
    return items, total_rows, total_pages


def received_attachment_type_counts(db_path: str) -> dict[str, int]:
    query = """
        SELECT
            SUM(CASE WHEN a.mime_type LIKE 'image/%' THEN 1 ELSE 0 END) as images,
            SUM(CASE WHEN a.mime_type = 'application/pdf' THEN 1 ELSE 0 END) as pdf,
            SUM(CASE WHEN a.mime_type IN ('text/plain','text/csv','application/rtf')
                       OR a.mime_type LIKE 'application/vnd.openxmlformats-officedocument%'
                       OR a.mime_type LIKE 'application/vnd.ms-%'
                       OR a.mime_type LIKE 'application/vnd.oasis.opendocument.%' THEN 1 ELSE 0 END) as documents,
            SUM(CASE WHEN a.mime_type IN (
                       'application/zip','application/x-zip-compressed','application/gzip',
                       'application/x-tar','application/x-7z-compressed',
                       'application/x-rar-computed','application/x-rar') THEN 1 ELSE 0 END) as archives,
            SUM(CASE WHEN NOT (
                       a.mime_type LIKE 'image/%'
                       OR a.mime_type = 'application/pdf'
                       OR a.mime_type IN ('text/plain','text/csv','application/rtf')
                       OR a.mime_type LIKE 'application/vnd.openxmlformats-officedocument%'
                       OR a.mime_type LIKE 'application/vnd.ms-%'
                       OR a.mime_type LIKE 'application/vnd.oasis.opendocument.%'
                       OR a.mime_type IN (
                           'application/zip','application/x-zip-compressed','application/gzip',
                           'application/x-tar','application/x-7z-compressed',
                           'application/x-rar-computed','application/x-rar')
                       ) THEN 1 ELSE 0 END) as other,
            COUNT(*) as total_all
        FROM attachments a
        WHERE a.owner_type = ?
    """
    with _connect(db_path) as conn:
        row = conn.execute(query, (OWNER_EMAIL,)).fetchone()

    return {
        "images": row["images"] or 0,
        "pdf": row["pdf"] or 0,
        "documents": row["documents"] or 0,
        "archives": row["archives"] or 0,
        "other": row["other"] or 0,
        "all": row["total_all"] or 0,
    }


def get(db_path: str, attachment_id: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    return _row_to_attachment(row) if row else None


def delete_one(db_path: str, attachment_id: str) -> bool:
    row = get(db_path, attachment_id)
    if not row:
        return False
    _remove_file(row)
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    return cursor.rowcount > 0


def delete_all(db_path: str, owner_id: str, owner_type: str) -> int:
    rows = list_for(db_path, owner_id, owner_type)
    for row in rows:
        _remove_file(row)
    if not rows:
        return 0
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM attachments WHERE owner_type = ? AND owner_id = ?",
            (owner_type, owner_id),
        )
    return cursor.rowcount


def transfer(db_path: str, owner_id: str, from_type: str, to_type: str) -> int:
    return rekey(db_path, owner_id, from_type, owner_id, to_type)


def rekey(
    db_path: str,
    from_owner_id: str,
    from_type: str,
    to_owner_id: str,
    to_type: str,
) -> int:
    if from_owner_id == to_owner_id and from_type == to_type:
        return 0
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE attachments SET owner_id = ?, owner_type = ? WHERE owner_id = ? AND owner_type = ?",
            (to_owner_id, to_type, from_owner_id, from_type),
        )
    return cursor.rowcount


def prune_orphans(db_path: str = DB_PATH) -> int:
    with _connect(db_path) as conn:
        try:
            cursor = conn.execute(
                "DELETE FROM attachments WHERE "
                "(owner_type = ? AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.id = attachments.owner_id)) "
                "OR (owner_type = ? AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.id = attachments.owner_id)) "
                "OR (owner_type = ? AND NOT EXISTS (SELECT 1 FROM emails e WHERE e.message_id_hash = attachments.owner_id)) "
                "RETURNING id, disk_path",
                (OWNER_DRAFT, OWNER_OUTBOX, OWNER_EMAIL),
            )
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT id, disk_path FROM attachments WHERE "
                "(owner_type = ? AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.id = attachments.owner_id)) "
                "OR (owner_type = ? AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.id = attachments.owner_id)) "
                "OR (owner_type = ? AND NOT EXISTS (SELECT 1 FROM emails e WHERE e.message_id_hash = attachments.owner_id))",
                (OWNER_DRAFT, OWNER_OUTBOX, OWNER_EMAIL),
            ).fetchall()
            if rows:
                ids = [row["id"] for row in rows]
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"DELETE FROM attachments WHERE id IN ({placeholders})",
                    ids,
                )

        if not rows:
            return 0
        for row in rows:
            try:
                target = _abs_path(row["disk_path"])
                if target.is_file():
                    target.unlink()
                parent = target.parent
                if parent != ATTACHMENTS_DIR.resolve() and parent.exists():
                    import shutil

                    shutil.rmtree(parent, ignore_errors=True)
            except Exception:
                logger.warning("Failed to remove attachment file %s", row["disk_path"], exc_info=True)
    return len(rows)
