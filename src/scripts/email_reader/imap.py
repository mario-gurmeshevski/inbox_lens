import os
import re
import ssl
import logging
import imaplib
import time
import email as email_lib
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.scripts import cache
from src.scripts.constants import DB_PATH
from src.scripts.email_reader.parser import (
    decode_str,
    get_text_body,
    extract_thread_info,
)

logger = logging.getLogger(__name__)

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_TIMEOUT = 30
MAX_WORKERS = 8
FETCH_BATCH_SIZE = 25
RECONNECT_DELAY = 2
_trash_folder_cache: str | None = None
_archive_folder_cache: str | None = None
GMAIL_DEFAULT_ARCHIVE = "[Gmail]/All Mail"


def reset_folder_caches() -> None:
    global _trash_folder_cache, _archive_folder_cache
    _trash_folder_cache = None
    _archive_folder_cache = None


def _imap_connect(db_path=None):
    if db_path is None:
        db_path = DB_PATH
    email_user, email_pass = cache.get_email_credentials(db_path)
    conn = imaplib.IMAP4_SSL(IMAP_SERVER, timeout=IMAP_TIMEOUT)
    conn.login(email_user, email_pass)
    conn.select("INBOX")
    return conn


def _safe_close(conn):
    try:
        conn.close()
    except Exception:
        pass
    try:
        conn.logout()
    except Exception:
        pass


@contextmanager
def imap_session(db_path=None):
    if db_path is None:
        db_path = DB_PATH
    mail = None
    try:
        mail = _imap_connect(db_path)
        yield mail
    finally:
        if mail:
            _safe_close(mail)


def _chunk_list(lst, n):
    return [lst[i::n] for i in range(n)]


def _reconnect(conn, db_path=None):
    _safe_close(conn)
    time.sleep(RECONNECT_DELAY)
    return _imap_connect(db_path)


def _batch_fetch_loop(conn, items, batch_size, bulk_fn, single_fn, db_path=None):
    results = []
    batch_start = 0
    while batch_start < len(items):
        batch_end = min(batch_start + batch_size, len(items))
        batch = items[batch_start:batch_end]
        batch_ok = False

        for attempt in range(2):
            try:
                batch_result = bulk_fn(conn, batch)
                if batch_result is not None:
                    results.extend(batch_result)
                    batch_ok = True
                    break
            except (imaplib.IMAP4.abort, ssl.SSLError, OSError):
                pass

            if attempt == 0:
                logger.warning("Bulk fetch failed at batch %d, reconnecting...", batch_start)
                try:
                    conn = _reconnect(conn, db_path)
                except Exception:
                    logger.warning("Reconnect failed at batch %d", batch_start, exc_info=True)
                    break

        if not batch_ok:
            for item in batch:
                single_result, conn = single_fn(conn, item)
                results.extend(single_result)

        batch_start = batch_end

    return results, conn


def _make_body_fetchers(db_path):
    def bulk(conn, batch):
        id_str = b",".join(eid for eid, _ in batch)
        try:
            status, msg_data = conn.uid("fetch", id_str, "(BODY.PEEK[] FLAGS)")
        except Exception:
            return None
        if status != "OK":
            return None
        parsed = _parse_fetched_email(msg_data)
        fetched_mids = {mid for _, mid in batch}
        results_batch = []
        for p in parsed:
            if p.get("message_id", "") in fetched_mids:
                p["_message_id"] = p["message_id"]
            results_batch.append(p)
        if len(parsed) < len(batch):
            return None
        return results_batch

    def single(conn, item):
        eid, message_id = item
        for attempt in range(2):
            try:
                status, msg_data = conn.uid("fetch", eid, "(BODY.PEEK[] FLAGS)")
                if status == "OK":
                    parsed = _parse_fetched_email(msg_data)
                    for p in parsed:
                        p["_message_id"] = message_id
                    return parsed, conn
                return [], conn
            except (imaplib.IMAP4.abort, ssl.SSLError, OSError):
                if attempt == 0:
                    logger.warning("Connection lost fetching body %s, reconnecting...", eid)
                    try:
                        conn = _reconnect(conn, db_path)
                    except Exception:
                        logger.warning("Reconnect failed for body %s", eid, exc_info=True)
                        return [], conn
                else:
                    logger.warning("Failed to fetch body for %s after reconnect", eid, exc_info=True)
                    return [], conn
        return [], conn

    return bulk, single


def _extract_uid(envelope):
    match = re.search(r"\bUID (\d+)", envelope)
    return match.group(1).encode() if match else None


def _extract_flags(envelope: str) -> tuple[bool, bool]:
    is_read = "\\Seen" in envelope
    is_starred = "\\Flagged" in envelope
    return is_read, is_starred


def _get_email_ids(mail):
    mail.select("INBOX")
    status, data = mail.uid("search", None, "ALL")
    if status != "OK":
        return []
    return data[0].split()


def _fetch_headers_bulk(mail, email_ids):
    if not email_ids:
        return []
    id_str = b",".join(email_ids)
    status, msg_data = mail.uid(
        "fetch",
        id_str,
        "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID IN-REPLY-TO REFERENCES THREAD-INDEX)] FLAGS)",
    )
    if status != "OK":
        return []
    results = []
    for item in msg_data:
        if not isinstance(item, tuple):
            continue
        envelope = item[0].decode(errors="replace")
        uid = _extract_uid(envelope)
        is_read, is_starred = _extract_flags(envelope)
        msg = email_lib.message_from_bytes(item[1])
        thread_info = extract_thread_info(msg)
        results.append(
            {
                "message_id": msg.get("Message-ID", ""),
                "from": decode_str(msg.get("From", "")),
                "subject": decode_str(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": "",
                "thread_id": thread_info["thread_id"],
                "in_reply_to": thread_info["in_reply_to"],
                "is_read": is_read,
                "is_starred": is_starred,
                "_uid": uid,
            }
        )
    return results


def _parse_fetched_email(msg_data_list):
    results = []
    for item in msg_data_list:
        if not isinstance(item, tuple):
            continue
        envelope = item[0].decode(errors="replace") if isinstance(item[0], bytes) else str(item[0])
        raw = item[1]
        msg = email_lib.message_from_bytes(raw)

        subject = decode_str(msg.get("Subject", ""))
        from_addr = decode_str(msg.get("From", ""))
        date = msg.get("Date", "")
        message_id = msg.get("Message-ID", "")
        body = get_text_body(msg)
        thread_info = extract_thread_info(msg)

        if body:
            body = body.strip()

        is_read, is_starred = _extract_flags(envelope)

        results.append(
            {
                "message_id": message_id,
                "from": from_addr,
                "subject": subject,
                "date": date,
                "body": body,
                "thread_id": thread_info["thread_id"],
                "in_reply_to": thread_info["in_reply_to"],
                "is_read": is_read,
                "is_starred": is_starred,
            }
        )
    return results


def _sanitize_imap_search(value):
    return value.replace('"', "").replace(")", "").replace("(", "").replace("\\", "")


_UNSAFE_FOLDER_CHARS = frozenset('"\\\n\r\x00{')


def _validate_folder_name(folder: str) -> bool:
    if not folder:
        return False
    return not any(ch in _UNSAFE_FOLDER_CHARS for ch in folder)


def _resolve_uid(mail, message_id):
    safe_id = _sanitize_imap_search(message_id)
    status, data = mail.uid("search", None, f'(HEADER "Message-ID" "{safe_id}")')
    if status == "OK" and data[0].split():
        return data[0].split()[0]
    return None


def find_trash_folder(mail):
    status, folders = mail.list()
    if status == "OK":
        for folder in folders:
            decoded = folder.decode()
            if "\\Trash" in decoded:
                name = _parse_list_folder_name(decoded)
                if name:
                    return name
    return "[Gmail]/Trash"


def _parse_list_folder_name(folder_line):
    parts = folder_line.split('"/"')
    if len(parts) == 2:
        return parts[1].strip().strip('"')
    return None


def move_to_trash(mail, message_id):
    global _trash_folder_cache
    mail.select("INBOX")
    email_id = _resolve_uid(mail, message_id)
    if not email_id:
        return False

    if _trash_folder_cache is None:
        _trash_folder_cache = find_trash_folder(mail)
    trash_folder = _trash_folder_cache

    status, _ = mail.uid("copy", email_id, trash_folder)
    if status != "OK":
        return False

    mail.uid("store", email_id, "+FLAGS", "\\Deleted")
    mail.expunge()
    return True


def find_folder_by_attr(mail, attr: str, fallback: str) -> str:
    status, folders = mail.list()
    if status == "OK":
        for folder in folders:
            decoded = folder.decode(errors="replace")
            if attr in decoded:
                name = _parse_list_folder_name(decoded)
                if name:
                    return name
    return fallback


def find_archive_folder(mail) -> str:
    return find_folder_by_attr(mail, "\\All", GMAIL_DEFAULT_ARCHIVE)


def resolve_archive_folder(db_path=None) -> str:
    global _archive_folder_cache
    if _archive_folder_cache is not None:
        return _archive_folder_cache
    if db_path is None:
        db_path = DB_PATH
    try:
        with imap_session(db_path) as mail:
            _archive_folder_cache = find_archive_folder(mail)
    except Exception:
        logger.warning("Failed to resolve archive folder", exc_info=True)
        _archive_folder_cache = GMAIL_DEFAULT_ARCHIVE
    return _archive_folder_cache


def list_folders(db_path=None) -> list[str]:
    # Return all selectable mailbox names for the configured account.
    if db_path is None:
        db_path = DB_PATH
    if not cache.has_email_credentials(db_path):
        return []
    folders: list[str] = []
    try:
        with imap_session(db_path) as mail:
            status, raw = mail.list()
            if status != "OK":
                return []
            for folder in raw:
                decoded = folder.decode(errors="replace")
                if "\\Noselect" in decoded:
                    continue
                name = _parse_list_folder_name(decoded)
                if name:
                    folders.append(name)
    except Exception:
        logger.warning("Failed to list IMAP folders", exc_info=True)
        return []
    return folders


def move_to_folder(mail, message_id, folder: str) -> bool:
    if not _validate_folder_name(folder):
        return False
    mail.select("INBOX")
    email_id = _resolve_uid(mail, message_id)
    if not email_id:
        return False

    status, _ = mail.uid("copy", email_id, folder)
    if status != "OK":
        return False

    mail.uid("store", email_id, "+FLAGS", "\\Deleted")
    mail.expunge()
    return True


def set_flags_bulk(mail, message_ids: list[str], flag: str, on: bool) -> int:
    if not message_ids:
        return 0
    mail.select("INBOX")
    op = "+FLAGS" if on else "-FLAGS"
    updated = 0
    for mid in message_ids:
        uid = _resolve_uid(mail, mid)
        if not uid:
            continue
        status, _ = mail.uid("store", uid, op, flag)
        if status == "OK":
            updated += 1
    return updated


def _bulk_move_uids(mail, message_ids: list[str], folder: str) -> int:
    if not message_ids or not _validate_folder_name(folder):
        return 0
    mail.select("INBOX")
    moved_uids: list[bytes] = []
    for mid in message_ids:
        uid = _resolve_uid(mail, mid)
        if not uid:
            continue
        status, _ = mail.uid("copy", uid, folder)
        if status == "OK":
            moved_uids.append(uid)
    if moved_uids:
        mail.uid("store", b",".join(moved_uids), "+FLAGS", "\\Deleted")
        mail.expunge()
    return len(moved_uids)


def fetch_headers_and_cache(db_path=None):
    if db_path is None:
        db_path = DB_PATH

    if not cache.has_email_credentials(db_path):
        return {"error": "Email account not configured. Please log in via the web dashboard."}

    cache.init_db(db_path)

    try:
        with imap_session(db_path) as mail:
            email_ids = _get_email_ids(mail)
            if not email_ids:
                return {"new_count": 0, "existing_count": 0, "emails": [], "imap_id_pairs": []}

            headers = _fetch_headers_bulk(mail, email_ids)

            header_entries = []
            for header in headers:
                uid = header.pop("_uid", None)
                if not uid:
                    continue
                mid = header.get("message_id", "")
                h = cache._hash_message_id(mid)
                header_entries.append((header, uid, mid, h))

            existing_hashes = cache.check_hashes_exist(db_path, [h for _, _, _, h in header_entries])

            new_headers = []
            new_ids = []
            existing_flag_updates = []
            for header, eid, mid, h in header_entries:
                if h not in existing_hashes:
                    new_headers.append(header)
                    new_ids.append((eid, mid))
                else:
                    existing_flag_updates.append(
                        (header.get("is_read", False), header.get("is_starred", False), h)
                    )

            existing = cache.get_total_count(db_path)

            if existing_flag_updates:
                cache.update_flags_batch(existing_flag_updates, db_path)

            cache.save_headers_batch(new_headers, db_path)

            return {
                "new_count": len(new_headers),
                "existing_count": existing,
                "emails": new_headers,
                "imap_id_pairs": new_ids,
            }
    except Exception as e:
        return {"error": str(e)}


def _fetch_body_chunk(chunk_pairs, db_path=None):
    bulk_fn, single_fn = _make_body_fetchers(db_path)
    results = []
    try:
        conn = _imap_connect(db_path)
        results, conn = _batch_fetch_loop(conn, chunk_pairs, FETCH_BATCH_SIZE, bulk_fn, single_fn, db_path)
        _safe_close(conn)
    except Exception:
        logger.warning("Failed to connect for body chunk fetch", exc_info=True)

    return results


def fetch_bodies_for_ids(imap_id_pairs, db_path=None, max_workers=MAX_WORKERS):
    if db_path is None:
        db_path = DB_PATH

    if not imap_id_pairs:
        return []

    cache.init_db(db_path)

    num_workers = max(1, min(max_workers, MAX_WORKERS))
    chunks = [c for c in _chunk_list(imap_id_pairs, num_workers) if c]

    results = []
    with ThreadPoolExecutor(max_workers=min(len(chunks), MAX_WORKERS)) as executor:
        futures = [executor.submit(_fetch_body_chunk, chunk, db_path) for chunk in chunks]
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception:
                continue

    updates = [
        (
            r.get("_message_id", r.get("message_id", "")),
            r.get("body", ""),
            r.get("is_read", False),
            r.get("is_starred", False),
        )
        for r in results
    ]
    updated = cache.update_bodies_batch(updates, db_path)
    if updated < len(updates):
        logger.warning("Body DB update: %d/%d rows updated", updated, len(updates))
    return results


def fetch_bodies_by_message_ids(message_ids, db_path=None):
    if db_path is None:
        db_path = DB_PATH

    if not message_ids or not cache.has_email_credentials(db_path):
        return {"fetched": 0, "failed": 0}

    cache.init_db(db_path)
    fetched = 0
    failed = 0

    conn = None
    try:
        conn = _imap_connect(db_path)

        target_pairs = []
        for mid in message_ids:
            uid = _resolve_uid(conn, mid)
            if uid:
                target_pairs.append((uid, mid))
            else:
                failed += 1

        if failed > 0:
            logger.warning("UIDs not found for %d message-id(s)", failed)

        bulk_fn, single_fn = _make_body_fetchers(db_path)
        raw_results, conn = _batch_fetch_loop(conn, target_pairs, FETCH_BATCH_SIZE, bulk_fn, single_fn, db_path)

        db_updates = [
            (
                r.get("_message_id", r.get("message_id", "")),
                r.get("body", ""),
                r.get("is_read", False),
                r.get("is_starred", False),
            )
            for r in raw_results
            if r.get("_message_id") or r.get("message_id")
        ]
        fetched = len(db_updates)
        failed += len(target_pairs) - fetched

        if db_updates:
            updated = cache.update_bodies_batch(db_updates, db_path)
            if updated < len(db_updates):
                logger.warning("Body DB update: %d/%d rows updated", updated, len(db_updates))

        _safe_close(conn)
    except Exception:
        logger.warning("Failed to connect for batch body fetch by message_id", exc_info=True)
        _safe_close(conn)
        failed = len(message_ids) - fetched

    return {"fetched": fetched, "failed": failed}


def delete_email(message_id, db_path=None):
    if db_path is None:
        db_path = DB_PATH

    if not cache.has_email_credentials(db_path):
        return {"error": "Email account not configured. Please log in via the web dashboard."}

    try:
        with imap_session(db_path) as mail:
            success = move_to_trash(mail, message_id)
            if success:
                cache.delete_email(message_id, db_path)
                return {"deleted": True, "message_id": message_id}
            else:
                return {"deleted": False, "error": f"Message not found: {message_id}"}
    except Exception as e:
        return {"error": str(e)}


def archive_remote(message_id, db_path=None) -> bool:
    if db_path is None:
        db_path = DB_PATH
    if not message_id or not cache.has_email_credentials(db_path):
        return False
    try:
        folder = resolve_archive_folder(db_path)
        with imap_session(db_path) as mail:
            return move_to_folder(mail, message_id, folder)
    except Exception:
        logger.warning("Background IMAP archive failed", exc_info=True)
        return False


def move_remote(message_id, folder: str, db_path=None) -> bool:
    if db_path is None:
        db_path = DB_PATH
    if not message_id or not folder or not cache.has_email_credentials(db_path):
        return False
    try:
        with imap_session(db_path) as mail:
            return move_to_folder(mail, message_id, folder)
    except Exception:
        logger.warning("Background IMAP move failed", exc_info=True)
        return False


def bulk_set_flag_remote(message_ids: list[str], flag: str, on: bool, db_path=None) -> int:
    if db_path is None:
        db_path = DB_PATH
    if not message_ids or not cache.has_email_credentials(db_path):
        return 0
    try:
        with imap_session(db_path) as mail:
            return set_flags_bulk(mail, message_ids, flag, on)
    except Exception:
        logger.warning("Background bulk IMAP flag sync failed", exc_info=True)
        return 0


def bulk_move_remote(message_ids: list[str], folder: str, db_path=None) -> int:
    if db_path is None:
        db_path = DB_PATH
    if not message_ids or not folder or not cache.has_email_credentials(db_path):
        return 0
    moved = 0
    try:
        with imap_session(db_path) as mail:
            moved = _bulk_move_uids(mail, message_ids, folder)
    except Exception:
        logger.warning("Background bulk IMAP move failed", exc_info=True)
        return moved
    return moved


def bulk_archive_remote(message_ids: list[str], db_path=None) -> int:
    if db_path is None:
        db_path = DB_PATH
    if not message_ids or not cache.has_email_credentials(db_path):
        return 0
    folder = resolve_archive_folder(db_path)
    return bulk_move_remote(message_ids, folder, db_path)


def bulk_delete_remote(message_ids: list[str], db_path=None) -> int:
    if db_path is None:
        db_path = DB_PATH
    if not message_ids or not cache.has_email_credentials(db_path):
        return 0
    deleted = 0
    try:
        with imap_session(db_path) as mail:
            global _trash_folder_cache
            if _trash_folder_cache is None:
                _trash_folder_cache = find_trash_folder(mail)
            deleted = _bulk_move_uids(mail, message_ids, _trash_folder_cache)
    except Exception:
        logger.warning("Background bulk IMAP delete failed", exc_info=True)
        return deleted
    return deleted


def test_connection(imap_server, email_user, email_pass):
    try:
        conn = imaplib.IMAP4_SSL(imap_server, timeout=IMAP_TIMEOUT)
        conn.login(email_user, email_pass)
        conn.select("INBOX")
        status, data = conn.uid("search", None, "ALL")
        count = len(data[0].split()) if status == "OK" and data[0] else 0
        _safe_close(conn)
        return {"success": True, "inbox_count": count}
    except imaplib.IMAP4.error:
        return {"success": False, "error": "Invalid email or password. Please check your credentials and try again."}
    except Exception as e:
        return {"success": False, "error": str(e)}
