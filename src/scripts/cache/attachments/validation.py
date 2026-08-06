import logging
import os

from .storage import AttachmentError

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
DEFAULT_MAX_TOTAL_SIZE = 25 * 1024 * 1024
RECEIVED_MAX_TOTAL_SIZE = 50 * 1024 * 1024
MAX_FILES = 10
_SETTING_MAX_SIZE = "max_attachment_size"

_BLOCKED_EXTENSIONS = frozenset(
    {
        ".exe",
        ".com",
        ".scr",
        ".pif",
        ".msi",
        ".msp",
        ".mst",
        ".app",
        ".bat",
        ".cmd",
        ".command",
        ".sh",
        ".bash",
        ".csh",
        ".tcsh",
        ".ksh",
        ".zsh",
        ".fish",
        ".so",
        ".dylib",
        ".dll",
        ".sys",
        ".drv",
        ".ko",
        ".jar",
        ".jnlp",
        ".msc",
        ".vb",
        ".vbs",
        ".vbe",
        ".wsh",
        ".wsf",
        ".wsc",
        ".sct",
        ".ps1",
        ".psm1",
        ".psd1",
        ".cdxml",
        ".hta",
        ".htr",
        ".pl",
        ".py",
        ".pyc",
        ".pyw",
        ".rb",
        ".php",
        ".jsp",
        ".asp",
        ".aspx",
        ".cgi",
        ".action",
        ".workflow",
        ".run",
        ".lnk",
        ".url",
        ".webloc",
        ".locvars",
        ".reg",
        ".inf",
        ".ins",
        ".isp",
        ".cpl",
        ".docm",
        ".dotm",
        ".xlsm",
        ".xlsb",
        ".xltm",
        ".xlam",
        ".pptm",
        ".potm",
        ".ppam",
        ".sldm",
        ".mde",
        ".ade",
        ".accde",
        ".adp",
        ".js",
        ".jse",
        ".mjs",
        ".cjs",
        ".dmg",
        ".pkg",
        ".iso",
        ".vhd",
        ".vhdx",
        ".vmdk",
    }
)


def _parse_int_setting(raw, default: int) -> int:
    try:
        value = int((raw or "").strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _parse_size_env(raw) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _extension(filename: str) -> str:
    _, dot, ext = (filename or "").rpartition(".")
    return ("." + ext.lower()) if dot else ""


def _max_attachment_size(db_path: str = None) -> int:
    from src.scripts.constants import DB_PATH
    from src.scripts.cache import crypto

    if db_path is None:
        db_path = DB_PATH

    env = os.getenv("INBOX_LENS_MAX_ATTACHMENT_SIZE")
    default = _parse_size_env(env) or DEFAULT_MAX_ATTACHMENT_SIZE
    try:
        raw = crypto.get_setting(_SETTING_MAX_SIZE, db_path)
    except Exception:
        raw = None
    return _parse_int_setting(raw, default)


def _max_total_size(db_path: str = None) -> int:
    per_file = _max_attachment_size(db_path)
    return max(DEFAULT_MAX_TOTAL_SIZE, int(per_file * 2.5))


def get_max_attachment_size(db_path: str = None) -> int:
    from src.scripts.constants import DB_PATH

    if db_path is None:
        db_path = DB_PATH
    return _max_attachment_size(db_path)


def get_max_total_size(db_path: str = None) -> int:
    from src.scripts.constants import DB_PATH

    if db_path is None:
        db_path = DB_PATH
    return _max_total_size(db_path)


def _validate_one(filename: str, size: int, max_size: int) -> None:
    ext = _extension(filename)
    if ext in _BLOCKED_EXTENSIONS:
        raise AttachmentError(f"File type {ext or '(none)'} is not allowed.")
    if size <= 0:
        raise AttachmentError("Attachment is empty.")
    if size > max_size:
        mb = max_size / (1024 * 1024)
        raise AttachmentError(f"'{filename}' exceeds the {mb:.0f} MB per-file limit.")


def _validate_batch(items: list[dict], db_path: str) -> None:
    if len(items) > MAX_FILES:
        raise AttachmentError(f"Too many attachments (max {MAX_FILES}).")
    max_size = _max_attachment_size(db_path)
    total = 0
    for item in items:
        _validate_one(item["filename"], item["size"], max_size)
        total += item["size"]
    max_total = _max_total_size(db_path)
    if total > max_total:
        mb = max_total / (1024 * 1024)
        raise AttachmentError(f"Attachments exceed the {mb:.0f} MB total limit.")


def _validate_received_one(filename: str, size: int, max_size: int) -> None:
    if size <= 0:
        raise AttachmentError("Attachment is empty.")
    if size > max_size:
        mb = max_size / (1024 * 1024)
        raise AttachmentError(f"'{filename}' exceeds the {mb:.0f} MB per-file limit.")


def validate_received_batch(items: list[dict], db_path: str) -> list[dict]:
    if not items:
        return []

    max_size = _max_attachment_size(db_path)
    prepared = []
    total = 0

    for item in items:
        payload = item.get("payload") or b""
        size = len(payload)
        try:
            _validate_received_one(item.get("filename") or "attachment", size, max_size)
        except AttachmentError as exc:
            logger.warning("Skipping received attachment: %s", exc)
            continue

        if total + size > RECEIVED_MAX_TOTAL_SIZE:
            logger.warning(
                "Skipping received attachment %r: total cap exceeded",
                item.get("filename"),
            )
            continue

        total += size
        prepared.append(
            {
                "filename": item.get("filename") or "attachment",
                "mime_type": item.get("mime_type") or "application/octet-stream",
                "payload": payload,
                "size": size,
            }
        )

    return prepared
