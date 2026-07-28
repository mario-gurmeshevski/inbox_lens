import os
import logging
import smtplib
from contextlib import contextmanager
from email.mime.multipart import MIMEMultipart
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


def build_message(
    from_addr, to_addrs, subject, body, mode, cc_addrs=None, bcc_addrs=None, html=None, original_message_id=None
):
    cc_addrs = list(cc_addrs or [])
    bcc_addrs = list(bcc_addrs or [])
    for label, value in (
        ("to_addr", to_addrs),
        ("cc_addr", cc_addrs),
        ("bcc_addr", bcc_addrs),
    ):
        for item in value:
            if item and ("\n" in item or "\r" in item):
                raise ValueError(f"{label} must not contain newlines")
    if subject and ("\n" in subject or "\r" in subject):
        raise ValueError("subject must not contain newlines")

    subject = (subject or "").strip()
    if mode in ("reply", "forward"):
        prefix = "Re" if mode == "reply" else "Fwd"
        if has_subject_prefix(subject):
            final_subject = subject
        elif subject:
            final_subject = f"{prefix}: {subject}"
        else:
            final_subject = f"{prefix}: (no subject)"
    else:
        final_subject = subject

    to_joined = ", ".join(to_addrs)
    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body or "", "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
    else:
        msg = MIMEText(body or "", "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_joined
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Subject"] = final_subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    if mode == "reply" and original_message_id and original_message_id.strip():
        msg_id = original_message_id.strip()
        msg["In-Reply-To"] = msg_id
        msg["References"] = msg_id
    return msg


def send_message(
    to_addrs,
    cc_addrs,
    bcc_addrs,
    subject,
    body_markdown,
    body_html,
    mode,
    original_message_id=None,
    thread_id=None,
    db_path=None,
):
    if db_path is None:
        db_path = DB_PATH
    from_addr, _ = cache.get_email_credentials(db_path)
    to_addrs = list(to_addrs or [])
    cc_addrs = list(cc_addrs or [])
    bcc_addrs = list(bcc_addrs or [])
    msg = build_message(
        from_addr,
        to_addrs,
        subject,
        body_markdown,
        mode,
        cc_addrs=cc_addrs,
        bcc_addrs=bcc_addrs,
        html=body_html,
        original_message_id=original_message_id,
    )
    with smtp_session(db_path) as conn:
        conn.sendmail(from_addr, to_addrs + cc_addrs + bcc_addrs, msg.as_string())
    try:
        sent_mid = msg["Message-ID"] or ""
        sent_hash = cache._hash_message_id(sent_mid)
        in_reply_to = (msg.get("In-Reply-To", "") or "").strip() if mode == "reply" else ""
        cc_for_db = ", ".join(cc_addrs) if cc_addrs else None
        from src.scripts.email_reader.imap import resolve_sent_folder

        sent_folder = resolve_sent_folder(db_path)
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
                    "cc": cc_for_db,
                    "folder": sent_folder,
                }
            ],
            db_path,
        )
        cache.mark_sent([sent_hash], db_path)
        cache.update_bodies_batch([(sent_mid, body_markdown or "")], db_path)
    except Exception:
        logger.warning("Failed to persist sent message locally", exc_info=True)
