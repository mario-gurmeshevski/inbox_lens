from src.scripts.cache.db import _connect, _batch_existing_hashes, _LIST_COLUMNS
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
    from src.scripts.cache.db import _row_to_dict

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
    counts = {"headers_only": 0, "fetched": 0, "checked": 0, "fetched_no_body": 0}
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

    if status == "fetched":
        conditions.append("status = 'fetched'")
    elif status == "checked":
        conditions.append("status = 'checked'")
    elif status == "headers_only":
        conditions.append("status IN ('headers_only', 'fetched_no_body')")

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
        }
        d["keyword_matches"] = _parse_keyword_matches(row["keyword_matches"])
        emails.append(d)

    total_pages = max(1, -(-total_rows // page_size))
    return emails, total_rows, total_pages
