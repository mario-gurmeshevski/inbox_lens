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
)

logger = logging.getLogger(__name__)

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_TIMEOUT = 30
MAX_WORKERS = 8
FETCH_BATCH_SIZE = 25
RECONNECT_DELAY = 2
_trash_folder_cache: str | None = None
_archive_folder_cache: str | None = None
_sent_folder_cache: str | None = None
GMAIL_DEFAULT_ARCHIVE = "[Gmail]/All Mail"
GMAIL_DEFAULT_SENT = "[Gmail]/Sent Mail"


def reset_folder_caches() -> None:
    global _trash_folder_cache, _archive_folder_cache, _sent_folder_cache
    _trash_folder_cache = None
    _archive_folder_cache = None
    _sent_folder_cache = None


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


def _reconnect(conn, db_path=None, mailbox=None):
    _safe_close(conn)
    time.sleep(RECONNECT_DELAY)
    new_conn = _imap_connect(db_path)
    if mailbox:
        try:
            new_conn.select(mailbox)
        except Exception:
            logger.warning("Could not re-select %s after reconnect", mailbox, exc_info=True)
    return new_conn


def _batch_fetch_loop(conn, items, batch_size, bulk_fn, single_fn, db_path=None, mailbox=None):
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
                    conn = _reconnect(conn, db_path, mailbox=mailbox)
                except Exception:
                    logger.warning("Reconnect failed at batch %d", batch_start, exc_info=True)
                    break

        if not batch_ok:
            for item in batch:
                single_result, conn = single_fn(conn, item)
                results.extend(single_result)

        batch_start = batch_end

    return results, conn


def _make_body_fetchers(db_path, mailbox=None):
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
                        conn = _reconnect(conn, db_path, mailbox=mailbox)
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


def _extract_gm_thrid(envelope):
    match = re.search(r"X-GM-THRID\s+(\d+)", envelope)
    if not match:
        return None
    return cache._hash_message_id(match.group(1))


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


_HEADER_FETCH_FIELDS = (
    "(X-GM-THRID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID IN-REPLY-TO REFERENCES THREAD-INDEX)] FLAGS)"
)


def _parse_header_items(msg_data):
    results = []
    for item in msg_data:
        if not isinstance(item, tuple):
            continue
        envelope = item[0].decode(errors="replace")
        uid = _extract_uid(envelope)
        is_read, is_starred = _extract_flags(envelope)
        gm_thrid = _extract_gm_thrid(envelope)
        msg = email_lib.message_from_bytes(item[1])
        mid = msg.get("Message-ID", "")
        thread_id = gm_thrid or cache._hash_message_id(mid)
        in_reply_to = (msg.get("In-Reply-To", "") or "").strip()
        results.append(
            {
                "message_id": mid,
                "from": decode_str(msg.get("From", "")),
                "subject": decode_str(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": "",
                "thread_id": thread_id,
                "gm_thrid": gm_thrid,
                "in_reply_to": in_reply_to,
                "is_read": is_read,
                "is_starred": is_starred,
                "_uid": uid,
            }
        )
    return results


def _fetch_headers_bulk(mail, email_ids):
    if not email_ids:
        return []
    results = []
    for start in range(0, len(email_ids), FETCH_BATCH_SIZE):
        chunk = email_ids[start : start + FETCH_BATCH_SIZE]
        id_str = b",".join(chunk)
        status, msg_data = mail.uid("fetch", id_str, _HEADER_FETCH_FIELDS)
        if status != "OK":
            status, msg_data = mail.uid("fetch", id_str, _HEADER_FETCH_FIELDS)
        if status != "OK":
            uid_range = f"{chunk[0].decode()}-{chunk[-1].decode()}"
            logger.warning("Header fetch failed for UID batch %s after retry", uid_range)
            continue
        results.extend(_parse_header_items(msg_data))
    return results


def _fetch_bodies_in_folder(db_path, mailbox, message_ids):
    bodies = []
    if not message_ids:
        return bodies
    with imap_session(db_path) as mail:
        mail.select(_quote_mailbox(mailbox))
        for mid in message_ids:
            uid = _resolve_uid(mail, mid)
            if not uid:
                continue
            status, body_data = mail.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for piece in body_data:
                if not isinstance(piece, tuple):
                    continue
                m = email_lib.message_from_bytes(piece[1])
                if (m.get("Message-ID", "") or "").strip() == mid.strip():
                    bodies.append((mid, get_text_body(m) or ""))
                    break
        mail.select("INBOX")
    return bodies


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
                "thread_id": None,
                "in_reply_to": (msg.get("In-Reply-To", "") or "").strip(),
                "is_read": is_read,
                "is_starred": is_starred,
            }
        )
    return results


def _sanitize_imap_search(value):
    return value.replace('"', "").replace(")", "").replace("(", "").replace("\\", "")


_UNSAFE_FOLDER_CHARS = frozenset('"\\\n\r\x00{')

_QUOTE_CHARS = frozenset(' (){%*"\\') | {chr(c) for c in range(0, 0x21)} | {chr(0x7F)}


def _quote_mailbox(name: str) -> str:
    if not name:
        return name
    if any(ch in _QUOTE_CHARS for ch in name):
        return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return name


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

    status, _ = mail.uid("copy", email_id, _quote_mailbox(trash_folder))
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


def find_sent_folder(mail) -> str:
    return find_folder_by_attr(mail, "\\Sent", GMAIL_DEFAULT_SENT)


def resolve_sent_folder(db_path=None) -> str:
    global _sent_folder_cache
    if _sent_folder_cache is not None:
        return _sent_folder_cache
    if db_path is None:
        db_path = DB_PATH
    try:
        with imap_session(db_path) as mail:
            _sent_folder_cache = find_sent_folder(mail)
    except Exception:
        logger.warning("Failed to resolve Sent folder", exc_info=True)
        _sent_folder_cache = GMAIL_DEFAULT_SENT
    return _sent_folder_cache


def sync_sent_replies(db_path=None) -> dict:
    if db_path is None:
        db_path = DB_PATH
    if not cache.has_email_credentials(db_path):
        return {"synced": 0, "skipped": 0}

    cache.init_db(db_path)
    synced = 0
    skipped = 0
    try:
        with imap_session(db_path) as mail:
            sent_folder = resolve_sent_folder(db_path)
            status, _ = mail.select(_quote_mailbox(sent_folder))
            if status != "OK":
                logger.warning("Could not select Sent folder: %s", sent_folder)
                return {"synced": 0, "skipped": 0}

            status, data = mail.uid("search", None, "ALL")
            uids = data[0].split() if (status == "OK" and data and data[0]) else []
            if not uids:
                mail.select("INBOX")
                return {"synced": 0, "skipped": 0}

            parsed_headers = _fetch_headers_bulk(mail, uids)
            mail.select("INBOX")  # restore INBOX selection for other callers

            new_headers = []
            new_message_ids = []
            all_sent_hashes = []
            thread_id_refreshes: list[tuple[str | None, str | None, str]] = []
            for header in parsed_headers:
                header.pop("_uid", None)
                header.pop("is_read", None)
                header.pop("is_starred", None)
                mid = header.get("message_id", "")
                if not mid:
                    continue
                h = cache._hash_message_id(mid)
                all_sent_hashes.append(h)
                if cache.check_hashes_exist(db_path, [h]):
                    skipped += 1
                    thread_id = header.get("thread_id") or h
                    if thread_id:
                        thread_id_refreshes.append((thread_id, header.get("gm_thrid"), h))
                    continue
                new_headers.append(header)
                new_message_ids.append(mid)

            if not new_headers:
                if all_sent_hashes:
                    cache.mark_sent(all_sent_hashes, db_path)
                if thread_id_refreshes:
                    cache.refresh_thread_ids(thread_id_refreshes, db_path)
                return {"synced": 0, "skipped": skipped}

            cache.save_headers_batch(new_headers, db_path)

            if all_sent_hashes:
                cache.mark_sent(all_sent_hashes, db_path)
            if thread_id_refreshes:
                cache.refresh_thread_ids(thread_id_refreshes, db_path)

            bodies = _fetch_bodies_in_folder(db_path, sent_folder, new_message_ids)
            if bodies:
                cache.update_bodies_batch(bodies, db_path)
                synced = len(bodies)
    except Exception:
        logger.warning("Sent folder sync failed", exc_info=True)
        return {"synced": synced, "skipped": skipped}

    return {"synced": synced, "skipped": skipped}


def sync_all_mail(db_path=None) -> dict:
    if db_path is None:
        db_path = DB_PATH
    if not cache.has_email_credentials(db_path):
        return {"synced": 0, "skipped": 0, "hashes": set()}

    cache.init_db(db_path)
    synced = 0
    skipped = 0
    all_hashes: set[str] = set()
    try:
        with imap_session(db_path) as mail:
            archive_folder = resolve_archive_folder(db_path)
            status, _ = mail.select(_quote_mailbox(archive_folder))
            if status != "OK":
                logger.warning("Could not select All Mail folder: %s", archive_folder)
                return {"synced": 0, "skipped": 0, "hashes": set()}

            status, data = mail.uid("search", None, "ALL")
            uids = data[0].split() if (status == "OK" and data and data[0]) else []
            if not uids:
                mail.select("INBOX")
                return {"synced": 0, "skipped": 0, "hashes": set()}

            parsed_headers = _fetch_headers_bulk(mail, uids)
            mail.select("INBOX")  # restore INBOX selection for other callers

            new_headers = []
            new_message_ids = []
            thread_id_refreshes: list[tuple[str | None, str | None, str]] = []
            for header in parsed_headers:
                header.pop("_uid", None)
                header.pop("is_read", None)
                header.pop("is_starred", None)
                mid = header.get("message_id", "")
                if not mid:
                    continue
                h = cache._hash_message_id(mid)
                all_hashes.add(h)
                gm_thrid = header.get("gm_thrid")
                thread_id = header.get("thread_id") or h
                if cache.check_hashes_exist(db_path, [h]):
                    skipped += 1
                    if thread_id:
                        thread_id_refreshes.append((thread_id, gm_thrid, h))
                    continue
                new_headers.append(header)
                new_message_ids.append(mid)

            if new_headers:
                cache.save_headers_batch(new_headers, db_path)
            if thread_id_refreshes:
                cache.refresh_thread_ids(thread_id_refreshes, db_path)

            bodies = _fetch_bodies_in_folder(db_path, archive_folder, new_message_ids)
            if bodies:
                cache.update_bodies_batch(bodies, db_path)
                synced = len(bodies)
    except Exception:
        logger.warning("All Mail folder sync failed", exc_info=True)
        return {"synced": synced, "skipped": skipped, "hashes": all_hashes}

    return {"synced": synced, "skipped": skipped, "hashes": all_hashes}


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

    status, _ = mail.uid("copy", email_id, _quote_mailbox(folder))
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
        status, _ = mail.uid("copy", uid, _quote_mailbox(folder))
        if status == "OK":
            moved_uids.append(uid)
    if moved_uids:
        mail.uid("store", b",".join(moved_uids), "+FLAGS", "\\Deleted")
        mail.expunge()
    return len(moved_uids)


def fetch_headers_and_cache(db_path=None, protected_hashes=None):
    if db_path is None:
        db_path = DB_PATH

    if not cache.has_email_credentials(db_path):
        return {"error": "Email account not configured. Please log in via the web dashboard."}

    cache.init_db(db_path)

    try:
        with imap_session(db_path) as mail:
            email_ids = _get_email_ids(mail)
            if not email_ids:
                cache.reconcile_inbox(db_path, set(), force=True)
                return {"new_count": 0, "existing_count": 0, "emails": [], "imap_id_pairs": []}

            headers = _fetch_headers_bulk(mail, email_ids)

            header_entries = []
            server_hashes: set[str] = set()
            for header in headers:
                uid = header.pop("_uid", None)
                mid = header.get("message_id", "")
                h = cache._hash_message_id(mid)
                server_hashes.add(h)
                if not uid:
                    continue
                header_entries.append((header, uid, mid, h))

            searched_count = len(email_ids)
            parsed_count = len(headers)
            if parsed_count > 0:
                deleted = cache.reconcile_inbox(
                    db_path,
                    server_hashes,
                    protected_hashes=protected_hashes,
                    searched_count=searched_count,
                )
                logger.info(
                    "Inbox reconcile: searched=%d fetched=%d ghosts_deleted=%d",
                    searched_count,
                    parsed_count,
                    deleted,
                )
            else:
                logger.warning("Header fetch returned nothing for %d UIDs; skipping reconcile", searched_count)
                return {
                    "new_count": 0,
                    "existing_count": cache.get_total_count(db_path),
                    "emails": [],
                    "imap_id_pairs": [],
                }

            existing_hashes = cache.check_hashes_exist(db_path, [h for _, _, _, h in header_entries])

            new_headers = []
            new_ids = []
            existing_flag_updates = []
            thread_id_refreshes: list[tuple[str | None, str | None, str]] = []
            for header, eid, mid, h in header_entries:
                if h not in existing_hashes:
                    new_headers.append(header)
                    new_ids.append((eid, mid))
                else:
                    existing_flag_updates.append((header.get("is_read", False), header.get("is_starred", False), h))
                    tid = header.get("thread_id")
                    if tid:
                        thread_id_refreshes.append((tid, header.get("gm_thrid"), h))

            existing = cache.get_total_count(db_path)

            if existing_flag_updates:
                cache.update_flags_batch(existing_flag_updates, db_path)
            if thread_id_refreshes:
                cache.refresh_thread_ids(thread_id_refreshes, db_path)

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
        archive_mailbox = None
        try:
            archive_folder = resolve_archive_folder(db_path)
            archive_mailbox = _quote_mailbox(archive_folder)
            conn.select(archive_mailbox)
        except Exception:
            logger.warning("Could not select All Mail for body fetch; falling back to INBOX", exc_info=True)

        target_pairs = []
        for mid in message_ids:
            uid = _resolve_uid(conn, mid)
            if uid:
                target_pairs.append((uid, mid))
            else:
                failed += 1

        if failed > 0:
            logger.warning("UIDs not found for %d message-id(s)", failed)

        bulk_fn, single_fn = _make_body_fetchers(db_path, mailbox=archive_mailbox)
        raw_results, conn = _batch_fetch_loop(
            conn, target_pairs, FETCH_BATCH_SIZE, bulk_fn, single_fn, db_path, mailbox=archive_mailbox
        )

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
