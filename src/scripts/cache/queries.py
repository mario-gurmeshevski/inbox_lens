from src.scripts.cache.db import (
    _connect,
    _batch_existing_hashes,
    _LIST_COLUMNS,
    _row_to_dict,
    STATUS_HEADERS_ONLY,
    STATUS_FETCHED,
    STATUS_CHECKED,
    STATUS_FETCHED_NO_BODY,
    HEADER_FILTER_STATUSES,
)
from src.scripts.utils import _parse_keyword_matches


def check_hashes_exist(db_path: str, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    with _connect(db_path) as conn:
        return _batch_existing_hashes(conn, hashes)


def get_total_count(db_path: str) -> int:
    with _connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]


def get_email_by_hash(db_path: str, message_id_hash: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM emails WHERE message_id_hash = ?",
            (message_id_hash,),
        ).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    d["_file_hash"] = row["message_id_hash"]
    d["_category"] = row["category"] or "unclassified"
    return d


def get_priority_counts(db_path: str) -> dict[str, int]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM emails "
            "WHERE status = 'checked' AND category IS NOT NULL "
            "GROUP BY category"
        ).fetchall()
    return {row["category"]: row["cnt"] for row in rows}


def get_counts(db_path: str) -> dict:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT status, COUNT(*) as cnt FROM emails GROUP BY status").fetchall()
    counts = dict.fromkeys((STATUS_HEADERS_ONLY, STATUS_FETCHED, STATUS_CHECKED, STATUS_FETCHED_NO_BODY), 0)
    for row in rows:
        if row["status"] in counts:
            counts[row["status"]] = row["cnt"]
    return counts


def get_recent_emails(db_path: str, limit: int = 10) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT message_id_hash, message_id, sender, subject, date, keyword_matches "
            "FROM emails ORDER BY date_parsed DESC LIMIT ?",
            (limit,),
        ).fetchall()
    results = []
    for row in rows:
        d = {
            "message_id_hash": row["message_id_hash"],
            "message_id": row["message_id"],
            "from": row["sender"],
            "subject": row["subject"],
            "date": row["date"],
        }
        d["keyword_matches"] = _parse_keyword_matches(row["keyword_matches"])
        results.append(d)
    return results


def search_emails(
    db_path: str,
    status: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int, int]:
    conditions = []
    params: list = []

    if status == STATUS_FETCHED:
        conditions.append("status = 'fetched'")
    elif status == STATUS_CHECKED:
        conditions.append("status = 'checked'")
    elif status == STATUS_HEADERS_ONLY:
        placeholders = ",".join("?" * len(HEADER_FILTER_STATUSES))
        conditions.append(f"status IN ({placeholders})")
        params.extend(HEADER_FILTER_STATUSES)

    if priority:
        conditions.append("category = ?")
        params.append(priority)

    if search:
        conditions.append("(subject LIKE ? OR sender LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    with _connect(db_path) as conn:
        total_rows = conn.execute(f"SELECT COUNT(*) FROM emails{where_clause}", params).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM emails{where_clause} ORDER BY date_parsed DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()

    emails = []
    for row in rows:
        d = {
            "message_id": row["message_id"],
            "message_id_hash": row["message_id_hash"],
            "from": row["sender"],
            "subject": row["subject"],
            "date": row["date"],
            "status": row["status"],
            "category": row["category"] or "unclassified",
            "is_read": int(row["is_read"] or 0),
            "is_starred": int(row["is_starred"] or 0),
        }
        d["keyword_matches"] = _parse_keyword_matches(row["keyword_matches"])
        emails.append(d)

    total_pages = max(1, -(-total_rows // page_size))
    return emails, total_rows, total_pages
