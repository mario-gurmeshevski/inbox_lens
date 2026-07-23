import html
import logging
import re
import threading
from itertools import groupby
from email.header import decode_header

import html2text as _html2text_mod

logger = logging.getLogger(__name__)

_html2text_local = threading.local()


def _get_html2text():
    converter = getattr(_html2text_local, "converter", None)
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
            try:
                parts.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                parts.append(part.decode("utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


_RE_IMG = re.compile(r"<img[^>]*>", re.IGNORECASE)
_RE_STYLE = re.compile(r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_RE_NBSP = re.compile(r"&nbsp;")
_RE_SPACES = re.compile(r"[ \t]+")
_RE_BLANK_LINE = re.compile(r"\n[ \t]+\n")
_RE_NEWLINES = re.compile(r"\n{3,}")
_RE_RUNS = re.compile(r"([-*_])\1{2,}")
_RE_PREFIX = re.compile(r"^(Re|Fwd|Fw|Reply)\s*:\s*", re.IGNORECASE)
_RE_QUOTE_PREFIX = re.compile(r"^(>{1,}\s?|\|{1,}\s?)")
_RE_ON_WROTE = re.compile(r"^On\s.+\bwrote:\s*$", re.IGNORECASE)
_RE_LOCALE_WROTE = re.compile(
    r"^.{0,200}"
    r"(?:"
    r"wrote|"  # English
    r"a\s+écrit|"  # French
    r"schrieb|"  # German
    r"verfass(?:te|t)|"  # German (verfasste)
    r"escribió|"  # Spanish
    r"schreef|"  # Dutch
    r"напиш(?:а|ова)|"  # Macedonian
    r"напис(?:ал|ла|а|лав|ао)|"  # ru/uk/bg/sr
    r"escreveu|"  # Portuguese
    r"scrisse|"  # Italian
    r"yazdı"  # Turkish
    r")"
    r".*:\s*$",
    re.IGNORECASE,
)

_RE_DATE_EMAIL_COLON = re.compile(r"^.{0,120}\b[\w.+-]+@[\w-]+\.[\w.-]+.{0,40}:\s*$")
_RE_ORIGINAL_MSG = re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE)
_RE_FORWARDED = re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}", re.IGNORECASE)
_RE_OUTLOOK_FROM = re.compile(r"^From:\s.+$", re.IGNORECASE)
_RE_SHOW_QUOTED = re.compile(r"show quoted text", re.IGNORECASE)


def _has_following_header(lines: list[str], start: int, window: int = 3) -> bool:
    upper = min(window, len(lines) - start - 1)
    for offset in range(1, upper + 1):
        nxt = lines[start + offset].strip().lower()
        if nxt.startswith(("date:", "subject:")):
            return True
    return False


def strip_quoted_history(text: str) -> str:
    if not text:
        return ""
    lines = text.split("\n")
    cut = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _RE_QUOTE_PREFIX.match(line):
            cut = i
            break
        if _RE_SHOW_QUOTED.search(stripped):
            cut = i
            break
        if (
            _RE_ON_WROTE.match(stripped)
            or _RE_LOCALE_WROTE.match(stripped)
            or _RE_ORIGINAL_MSG.match(stripped)
            or _RE_FORWARDED.match(stripped)
            or (_RE_OUTLOOK_FROM.match(stripped) and _has_following_header(lines, i))
            or _RE_DATE_EMAIL_COLON.match(stripped)
        ):
            cut = i
            break
    if cut is not None:
        lines = lines[:cut]
    return _clean_body("\n".join(lines))


def _clean_body(text: str) -> str:
    if not text:
        return ""
    text = _RE_IMG.sub("", text)
    text = _RE_STYLE.sub("", text)
    text = _RE_NBSP.sub(" ", text)
    text = html.unescape(text)
    text = _RE_SPACES.sub(" ", text)
    text = _RE_BLANK_LINE.sub("\n\n", text)
    text = _RE_NEWLINES.sub("\n\n", text)
    text = _RE_RUNS.sub(r"\1\1\1", text)
    cleaned = []
    for is_blank, grp in groupby(text.split("\n"), key=lambda line: not line.strip()):
        if is_blank:
            cleaned.append("")
        else:
            cleaned.extend(line.strip() for line in grp)
    return "\n".join(cleaned).strip()


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


def strip_subject_prefix(subject: str) -> str:
    stripped = subject or ""
    while True:
        new = _RE_PREFIX.sub("", stripped).strip()
        if new == stripped:
            break
        stripped = new
    return stripped


def has_subject_prefix(subject: str) -> bool:
    return bool(_RE_PREFIX.match((subject or "").strip()))
