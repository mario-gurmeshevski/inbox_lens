import logging
import os
import re
from pathlib import Path

from src.scripts.cache import crypto
import src.scripts.constants as constants

logger = logging.getLogger(__name__)

MAX_FILENAME_LEN = 200
_RE_BAD_CHARS = re.compile(r"[\x00-\x1f/\\]")


def _get_attachments_dir():
    return constants.ATTACHMENTS_DIR


class _AttachmentsDirProxy:
    def resolve(self):
        return _get_attachments_dir().resolve()

    def __truediv__(self, other):
        return _get_attachments_dir() / other

    def __getattr__(self, name):
        return getattr(_get_attachments_dir(), name)


ATTACHMENTS_DIR = _AttachmentsDirProxy()


def sanitize_filename(name: str) -> str:
    cleaned = _RE_BAD_CHARS.sub("_", name or "").strip().rstrip(".") or "attachment"
    if len(cleaned) > MAX_FILENAME_LEN:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) <= 16:
            cleaned = stem[: MAX_FILENAME_LEN - len(ext) - 1] + "." + ext
        else:
            cleaned = cleaned[:MAX_FILENAME_LEN]
    return cleaned


def _abs_path(disk_path: str) -> Path:
    root = _get_attachments_dir().resolve()
    if not disk_path or os.path.isabs(disk_path):
        raise AttachmentError("Invalid attachment path")
    target = (root / disk_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise AttachmentError("Invalid attachment path")
    return target


def path_for(attachment: dict) -> Path:
    return _abs_path(attachment["disk_path"])


def _write_blob(payload: bytes, attachment_id: str, filename: str) -> str:
    safe = sanitize_filename(filename)
    rel_dir = Path(attachment_id)
    abs_dir = _get_attachments_dir() / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    rel_path = rel_dir / safe
    abs_path = _get_attachments_dir() / rel_path
    abs_path.write_bytes(crypto.encrypt_bytes(payload))
    return str(rel_path).replace(os.sep, "/")


def read_bytes_for(attachment: dict) -> bytes:
    return crypto.decrypt_bytes(path_for(attachment).read_bytes())


def _remove_file(row: dict) -> None:
    try:
        target = _abs_path(row["disk_path"])
    except AttachmentError:
        logger.warning("Skipping removal of attachment %s: invalid path", row.get("id"))
        return
    try:
        if target.is_file():
            target.unlink()
        parent = target.parent
        if parent != _get_attachments_dir().resolve() and parent.exists():
            import shutil

            shutil.rmtree(parent, ignore_errors=True)
    except Exception:
        logger.warning("Failed to remove attachment file %s", target, exc_info=True)


class AttachmentError(ValueError):
    pass
