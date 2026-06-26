import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.scripts.cache.db import _connect, _hash_message_id

logger = logging.getLogger(__name__)


def scan_and_update(emails: list[dict], db_path: str, compiled_patterns: dict) -> dict:
    emails_with_matches = []
    scanned = 0
    already_checked = 0
    skipped_no_body = 0

    hashes_map: dict[str, dict] = {}
    for e in emails:
        message_id = e.get("message_id", "")
        if message_id:
            hashes_map[_hash_message_id(message_id)] = e

    existing_status: dict[str, tuple[str, str | None]] = {}

    with _connect(db_path) as conn:
        if hashes_map:
            hash_list = list(hashes_map.keys())
            for i in range(0, len(hash_list), 500):
                chunk = hash_list[i : i + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT message_id_hash, keyword_matches, status FROM emails WHERE message_id_hash IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    existing_status[row["message_id_hash"]] = (row["status"], row["keyword_matches"])

    to_scan = []
    for e in emails:
        message_id = e.get("message_id", "")
        message_id_hash = _hash_message_id(message_id) if message_id else ""

        if message_id_hash and message_id_hash in existing_status:
            status, kw_json = existing_status[message_id_hash]
            if status == "checked" and kw_json:
                if kw_json in ("{}", "[]", '""'):
                    e["keyword_matches"] = {}
                    already_checked += 1
                    continue
                existing_matches = json.loads(kw_json)
                if existing_matches:
                    emails_with_matches.append((e, existing_matches))
                e["keyword_matches"] = existing_matches
                already_checked += 1
                continue
            if status in ("headers_only", "fetched_no_body"):
                e["keyword_matches"] = {}
                skipped_no_body += 1
                continue

        to_scan.append((e, message_id_hash))

    scan_results = {}
    if to_scan:
        workers = min(4, len(to_scan))
        if workers <= 1:
            for e, message_id_hash in to_scan:
                scan_text = f"{e.get('subject', '')} {e.get('body', '')}"
                matches = _scan_keywords(scan_text, compiled_patterns)
                scan_results[id(e)] = (e, message_id_hash, matches)
        else:

            def _scan_one(item):
                e, message_id_hash = item
                scan_text = f"{e.get('subject', '')} {e.get('body', '')}"
                return id(e), (e, message_id_hash, _scan_keywords(scan_text, compiled_patterns))

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_scan_one, item): item for item in to_scan}
                for future in as_completed(futures):
                    key, value = future.result()
                    scan_results[key] = value

    update_rows = []
    for e, message_id_hash, matches in scan_results.values():
        e["keyword_matches"] = matches
        scanned += 1
        if matches:
            emails_with_matches.append((e, matches))
        if message_id_hash:
            highest = max(matches.keys(), key=lambda k: int(k) if k.isdigit() else 0) if matches else "unclassified"
            keyword_json = json.dumps(matches, ensure_ascii=False) if matches else None
            update_rows.append(("checked", highest, keyword_json, message_id_hash))

    if update_rows:
        with _connect(db_path) as conn:
            conn.executemany(
                """UPDATE emails
                   SET status = ?, category = ?, keyword_matches = ?
                   WHERE message_id_hash = ?""",
                update_rows,
            )

    return {
        "emails_with_matches": emails_with_matches,
        "total": len(emails),
        "scanned": scanned,
        "already_checked": already_checked,
        "skipped_no_body": skipped_no_body,
    }


def _scan_keywords(text: str, compiled_patterns: dict) -> dict:
    if not text or not compiled_patterns:
        return {}
    text_lower = text.lower()
    matches = {}
    for category, (words, pattern) in compiled_patterns.items():
        found = pattern.findall(text_lower)
        if found:
            matches[category] = list(dict.fromkeys(found))
    return matches


def rescan_all(db_path: str, compiled_patterns: dict) -> dict:
    with _connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        conn.execute(
            "UPDATE emails SET status = 'fetched_no_body', category = NULL, keyword_matches = NULL "
            "WHERE (body IS NULL OR body = '') AND status IN ('fetched', 'checked')"
        )
        rows = conn.execute(
            "SELECT message_id_hash, subject, body FROM emails WHERE body IS NOT NULL AND body != ''"
        ).fetchall()

    if not rows:
        return {"scanned": 0, "skipped": total}

    items = [(r["message_id_hash"], f"{r['subject'] or ''} {r['body'] or ''}") for r in rows]

    def _scan_one(item):
        h, text = item
        return h, _scan_keywords(text, compiled_patterns)

    results: dict[str, dict] = {}
    workers = min(4, len(items))
    if workers <= 1:
        for item in items:
            h, matches = _scan_one(item)
            results[h] = matches
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_scan_one, item): item for item in items}
            for future in as_completed(futures):
                h, matches = future.result()
                results[h] = matches

    update_rows = []
    scanned = 0
    for h, matches in results.items():
        scanned += 1
        if matches:
            highest = max(matches.keys(), key=lambda k: int(k) if str(k).isdigit() else 0)
            keyword_json = json.dumps(matches, ensure_ascii=False)
        else:
            highest = "unclassified"
            keyword_json = None
        update_rows.append(("checked", highest, keyword_json, h))

    if update_rows:
        with _connect(db_path) as conn:
            conn.executemany(
                """UPDATE emails
                   SET status = ?, category = ?, keyword_matches = ?
                   WHERE message_id_hash = ?""",
                update_rows,
            )

    return {"scanned": scanned, "skipped": total - scanned}
