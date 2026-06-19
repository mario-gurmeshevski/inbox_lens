import functools
import html
import logging
import re
import threading
import hashlib
from itertools import groupby
from email.header import decode_header

import html2text as _html2text_mod

logger = logging.getLogger(__name__)

_html2text_local = threading.local()


def _get_html2text():
    converter = getattr(_html2text_local, 'converter', None)
    if converter is None:
        converter = _html2text_mod.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True
        converter.body_width = 0
        _html2text_local.converter = converter
    return converter


def decode_str(value):
    if value is None:
        return ""
    decoded = decode_header(value)
    parts = []
    for part, charset in decoded:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


_RE_IMG = re.compile(r'<img[^>]*>', re.IGNORECASE)
_RE_STYLE = re.compile(r'<style[^>]*>.*?</style>', re.IGNORECASE | re.DOTALL)
_RE_NBSP = re.compile(r'&nbsp;')
_RE_SPACES = re.compile(r'[ \t]+')
_RE_BLANK_LINE = re.compile(r'\n[ \t]+\n')
_RE_NEWLINES = re.compile(r'\n{3,}')
_RE_DASHES = re.compile(r'-{3,}')
_RE_UNDERSCORES = re.compile(r'_{3,}')
_RE_STARS = re.compile(r'\*{3,}')
_RE_PREFIX = re.compile(r'^(Re|Fwd|Fw|Reply)\s*:\s*', re.IGNORECASE)


def _clean_body(text: str) -> str:
    if not text:
        return ""
    text = _RE_IMG.sub('', text)
    text = _RE_STYLE.sub('', text)
    text = _RE_NBSP.sub(' ', text)
    text = html.unescape(text)
    text = _RE_SPACES.sub(' ', text)
    text = _RE_BLANK_LINE.sub('\n\n', text)
    text = _RE_NEWLINES.sub('\n\n', text)
    text = _RE_DASHES.sub('---', text)
    text = _RE_UNDERSCORES.sub('___', text)
    text = _RE_STARS.sub('***', text)
    cleaned = []
    for is_blank, grp in groupby(text.split('\n'), key=lambda line: not line.strip()):
        if is_blank:
            cleaned.append('')
        else:
            cleaned.extend(line.strip() for line in grp)
    return '\n'.join(cleaned).strip()


def _html_to_text(html_body: str) -> str:
    return _clean_body(_get_html2text().handle(html_body) or "")


def get_text_body(msg):
    html_body = None
    for part in msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition", ""))
        if "attachment" in content_disposition:
            continue
        try:
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception:
            continue

        if content_type == "text/plain" and html_body is None:
            return _clean_body(decoded)
        if content_type == "text/html" and html_body is None:
            html_body = decoded

    if html_body:
        text = _html_to_text(html_body)
        return text if text else ""
    return ""


@functools.lru_cache(maxsize=4096)
def _hash_thread_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _normalize_subject(subject: str) -> str:
    stripped = _RE_PREFIX.sub('', subject).strip()
    return stripped.lower() if stripped else ""


def extract_thread_info(msg) -> dict:
    in_reply_to = msg.get("In-Reply-To", "") or ""
    references = msg.get("References", "") or ""
    thread_index = msg.get("Thread-Index", "") or ""
    subject = msg.get("Subject", "") or ""

    if references:
        refs = references.strip().split()
        if refs:
            return {"thread_id": _hash_thread_id(refs[0]), "in_reply_to": in_reply_to.strip()}

    if in_reply_to.strip():
        return {"thread_id": _hash_thread_id(in_reply_to.strip()), "in_reply_to": in_reply_to.strip()}

    if thread_index:
        return {"thread_id": _hash_thread_id(thread_index[:22]), "in_reply_to": ""}

    normalized = _normalize_subject(subject)
    if normalized:
        return {"thread_id": _hash_thread_id(normalized), "in_reply_to": ""}

    return {"thread_id": None, "in_reply_to": ""}
