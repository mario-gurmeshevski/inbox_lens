import os
import logging
import smtplib
from contextlib import contextmanager
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from src.scripts import cache
from src.scripts.constants import DB_PATH
from src.scripts.email_reader.parser import has_subject_prefix

logger = logging.getLogger(__name__)


def _parse_port(raw, default=465):
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid SMTP_PORT %r, falling back to %d", raw, default)
        return default


SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = _parse_port(os.getenv("SMTP_PORT", "465"))
SMTP_TIMEOUT = 30


@contextmanager
def smtp_session(db_path=None):
    if db_path is None:
        db_path = DB_PATH
    email_user, email_pass = cache.get_email_credentials(db_path)
    conn = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT)
    try:
        conn.login(email_user, email_pass)
        yield conn
    finally:
        try:
            conn.quit()
        except Exception:
            logger.warning("Failed to close SMTP connection cleanly", exc_info=True)


def build_message(from_addr, to_addr, subject, body, mode, original_message_id=None):
    for label, value in (("to_addr", to_addr), ("subject", subject)):
        if value and ("\n" in value or "\r" in value):
            raise ValueError(f"{label} must not contain newlines")

    prefix = "Re" if mode == "reply" else "Fwd"
    subject = (subject or "").strip()
    if has_subject_prefix(subject):
        final_subject = subject
    elif subject:
        final_subject = f"{prefix}: {subject}"
    else:
        final_subject = f"{prefix}: (no subject)"

    msg = MIMEText(body or "", "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = final_subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    if mode == "reply" and original_message_id and original_message_id.strip():
        msg_id = original_message_id.strip()
        msg["In-Reply-To"] = msg_id
        msg["References"] = msg_id
    return msg


def send_message(to_addr, subject, body, mode, original_message_id=None, thread_id=None, db_path=None):
    if db_path is None:
        db_path = DB_PATH
    from_addr, _ = cache.get_email_credentials(db_path)
    msg = build_message(from_addr, to_addr, subject, body, mode, original_message_id)
    with smtp_session(db_path) as conn:
        conn.sendmail(from_addr, [to_addr], msg.as_string())
    try:
        sent_mid = msg["Message-ID"] or ""
        sent_hash = cache._hash_message_id(sent_mid)
        in_reply_to = (msg.get("In-Reply-To", "") or "").strip() if mode == "reply" else ""
        cache.save_headers_batch(
            [
                {
                    "message_id": sent_mid,
                    "from": from_addr,
                    "subject": msg["Subject"] or "",
                    "date": msg["Date"] or "",
                    "thread_id": thread_id or sent_hash,
                    "gm_thrid": None,
                    "in_reply_to": in_reply_to,
                }
            ],
            db_path,
        )
        cache.mark_sent([sent_hash], db_path)
        cache.update_bodies_batch([(sent_mid, body or "")], db_path)
    except Exception:
        logger.warning("Failed to persist sent message locally", exc_info=True)
