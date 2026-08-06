import logging
import uuid
from pathlib import Path

from src.scripts.constants import DB_PATH

from .storage import AttachmentError, sanitize_filename, path_for, read_bytes_for, _write_blob
from .validation import (
    _validate_batch,
    validate_received_batch,
)
from . import queries

logger = logging.getLogger(__name__)


def store_bytes(
    db_path: str,
    owner_id: str,
    owner_type: str,
    items: list[dict],
) -> list[dict]:
    if owner_type not in queries._VALID_OWNERS:
        raise AttachmentError(f"Invalid owner_type {owner_type!r}")
    if not owner_id:
        raise AttachmentError("owner_id is required")

    sized = []
    for item in items:
        payload = item.get("payload") or b""
        sized.append(
            {
                "filename": item.get("filename") or "attachment",
                "mime_type": item.get("mime_type") or "application/octet-stream",
                "payload": payload,
                "size": len(payload),
            }
        )

    _validate_batch(sized, db_path)
    return _persist(db_path, owner_id, owner_type, sized)


def _persist(
    db_path: str,
    owner_id: str,
    owner_type: str,
    prepared: list[dict],
) -> list[dict]:
    created = []
    try:
        for item in prepared:
            attachment_id = uuid.uuid4().hex
            disk_path = _write_blob(item["payload"], attachment_id, item["filename"])
            created.append(
                {
                    "id": attachment_id,
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                    "filename": sanitize_filename(item["filename"]),
                    "mime_type": item["mime_type"],
                    "size": item["size"],
                    "disk_path": disk_path,
                }
            )
        queries._insert_many(db_path, created)
    except Exception:
        for row in created:
            try:
                path_for(row).unlink(missing_ok=True)
            except Exception:
                logger.warning("Failed to remove staged blob %s", row.get("disk_path"), exc_info=True)
        raise
    return created


async def store_upload(
    db_path: str,
    owner_id: str,
    owner_type: str,
    upload_files,
) -> list[dict]:
    if owner_type not in queries._VALID_OWNERS:
        raise AttachmentError(f"Invalid owner_type {owner_type!r}")
    if not owner_id:
        raise AttachmentError("owner_id is required")

    uploads = [u for u in upload_files if u is not None and getattr(u, "filename", None)]
    prepared = []
    for upload in uploads:
        filename = upload.filename or "attachment"
        payload = await upload.read()
        size = len(payload)
        prepared.append(
            {
                "filename": filename,
                "mime_type": getattr(upload, "content_type", None) or "application/octet-stream",
                "payload": payload,
                "size": size,
            }
        )

    _validate_batch([{"filename": i["filename"], "size": i["size"]} for i in prepared], db_path)

    return _persist(db_path, owner_id, owner_type, prepared)


def store_received_attachments(db_path: str, owner_id: str, items: list[dict]) -> list[dict]:
    if not owner_id:
        raise AttachmentError("owner_id is required")

    existing = queries.list_for(db_path, owner_id, queries.OWNER_EMAIL)
    if existing:
        return existing

    prepared = validate_received_batch(items, db_path)
    if not prepared:
        return []

    return _persist(db_path, owner_id, queries.OWNER_EMAIL, prepared)


def store_staged_received(
    db_path: str,
    owner_id: str,
    owner_type: str,
    items: list[dict],
) -> list[dict]:
    if owner_type not in queries._VALID_OWNERS:
        raise AttachmentError(f"Invalid owner_type {owner_type!r}")
    if not owner_id:
        raise AttachmentError("owner_id is required")

    prepared = validate_received_batch(items, db_path)
    if not prepared:
        return []

    return _persist(db_path, owner_id, owner_type, prepared)


def delete_attachment(db_path: str, attachment_id: str) -> bool:
    return queries.delete_one(db_path, attachment_id)


def delete_attachments(db_path: str, owner_id: str, owner_type: str) -> int:
    return queries.delete_all(db_path, owner_id, owner_type)


def transfer_attachments(db_path: str, owner_id: str, from_type: str, to_type: str) -> int:
    return queries.transfer(db_path, owner_id, from_type, to_type)


def rekey_attachments(
    db_path: str,
    from_owner_id: str,
    from_type: str,
    to_owner_id: str,
    to_type: str,
) -> int:
    return queries.rekey(db_path, from_owner_id, from_type, to_owner_id, to_type)


def prune_attachment_orphans(db_path: str = DB_PATH) -> int:
    return queries.prune_orphans(db_path)


def attachment_path(attachment: dict) -> Path:
    return path_for(attachment)


def read_attachment_bytes(attachment: dict) -> bytes:
    return read_bytes_for(attachment)


def get_attachment(db_path: str, attachment_id: str) -> dict | None:
    return queries.get(db_path, attachment_id)


def list_attachments(db_path: str, owner_id: str, owner_type: str) -> list[dict]:
    return queries.list_for(db_path, owner_id, owner_type)


def list_attachments_for_owners(db_path: str, owner_ids: list[str], owner_type: str) -> dict[str, list[dict]]:
    return queries.list_for_owners(db_path, owner_ids, owner_type)
