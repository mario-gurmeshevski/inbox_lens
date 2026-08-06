from .storage import AttachmentError, sanitize_filename, path_for, read_bytes_for, ATTACHMENTS_DIR
from .validation import (
    get_max_attachment_size,
    get_max_total_size,
    RECEIVED_MAX_TOTAL_SIZE,
)
from .queries import (
    OWNER_OUTBOX,
    OWNER_DRAFT,
    OWNER_EMAIL,
    search_received_attachments,
    received_attachment_type_counts,
    _insert_many,
)
from .api import (
    store_bytes,
    store_upload,
    store_received_attachments,
    store_staged_received,
    delete_attachment,
    delete_attachments,
    transfer_attachments,
    rekey_attachments,
    prune_attachment_orphans,
    attachment_path,
    read_attachment_bytes,
    get_attachment,
    list_attachments,
    list_attachments_for_owners,
)

# Aliases for backward compatibility
store_attachment_bytes = store_bytes
store_upload_attachments = store_upload
store_received_attachments = store_received_attachments
transfer_attachments = transfer_attachments
rekey = rekey_attachments
prune_attachment_orphans = prune_attachment_orphans

__all__ = [
    "AttachmentError",
    "OWNER_OUTBOX",
    "OWNER_DRAFT",
    "OWNER_EMAIL",
    "RECEIVED_MAX_TOTAL_SIZE",
    "ATTACHMENTS_DIR",
    "sanitize_filename",
    "path_for",
    "read_attachment_bytes",
    "read_bytes_for",
    "get_max_attachment_size",
    "get_max_total_size",
    "store_bytes",
    "store_upload",
    "store_received_attachments",
    "store_staged_received",
    "delete_attachment",
    "delete_attachments",
    "transfer_attachments",
    "rekey_attachments",
    "prune_attachment_orphans",
    "attachment_path",
    "get_attachment",
    "list_attachments",
    "list_attachments_for_owners",
    "_insert_many",
    "store_attachment_bytes",
    "store_upload_attachments",
    "rekey",
    "search_received_attachments",
    "received_attachment_type_counts",
]
