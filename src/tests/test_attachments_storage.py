import pytest

from src.scripts import cache
from src.scripts.cache import attachments as att


def _items(payloads):
    return [{"filename": name, "mime_type": mime, "payload": data} for name, mime, data in payloads]


class TestStoreListDelete:
    def test_store_bytes_writes_files_and_rows(self, tmp_db):
        created = cache.store_attachment_bytes(
            tmp_db,
            "out-1",
            "outbox",
            _items([("report.pdf", "application/pdf", b"%PDF-1.4"), ("a.txt", "text/plain", b"hi")]),
        )
        assert len(created) == 2
        listed = cache.list_attachments(tmp_db, "out-1", "outbox")
        assert [a["filename"] for a in listed] == ["report.pdf", "a.txt"]
        for a in listed:
            assert cache.attachment_path(a).is_file()
            assert cache.read_attachment_bytes(a) in (b"%PDF-1.4", b"hi")

    def test_delete_all_removes_rows_and_files(self, tmp_db):
        cache.store_attachment_bytes(tmp_db, "d1", "draft", _items([("f.txt", "text/plain", b"x")]))
        rows = cache.list_attachments(tmp_db, "d1", "draft")
        assert len(rows) == 1
        path = cache.attachment_path(rows[0])
        assert path.is_file()
        n = cache.delete_attachments(tmp_db, "d1", "draft")
        assert n == 1
        assert cache.list_attachments(tmp_db, "d1", "draft") == []
        assert not path.exists()

    def test_delete_one_removes_single_file(self, tmp_db):
        created = cache.store_attachment_bytes(
            tmp_db, "d1", "draft", _items([("a.txt", "text/plain", b"a"), ("b.txt", "text/plain", b"b")])
        )
        target = created[0]
        path = cache.attachment_path(target)
        assert cache.delete_attachment(tmp_db, target["id"]) is True
        assert not path.exists()
        remaining = cache.list_attachments(tmp_db, "d1", "draft")
        assert len(remaining) == 1
        assert remaining[0]["filename"] == "b.txt"
        assert cache.delete_attachment(tmp_db, target["id"]) is False

    def test_get_returns_row(self, tmp_db):
        created = cache.store_attachment_bytes(tmp_db, "d1", "draft", _items([("a.txt", "text/plain", b"a")]))
        got = cache.get_attachment(tmp_db, created[0]["id"])
        assert got["filename"] == "a.txt"
        assert got["size"] == 1


class TestTransfer:
    def test_transfer_rekeys_rows(self, tmp_db):
        cache.store_attachment_bytes(tmp_db, "x1", "draft", _items([("a.txt", "text/plain", b"a")]))
        n = cache.transfer_attachments(tmp_db, "x1", "draft", "outbox")
        assert n == 1
        assert cache.list_attachments(tmp_db, "x1", "draft") == []
        assert len(cache.list_attachments(tmp_db, "x1", "outbox")) == 1
        assert cache.transfer_attachments(tmp_db, "x1", "outbox", "outbox") == 0


class TestValidation:
    def test_rejects_blocked_extension(self, tmp_db):
        with pytest.raises(cache.AttachmentError):
            cache.store_attachment_bytes(
                tmp_db, "a", "draft", _items([("evil.exe", "application/x-msdownload", b"MZ")])
            )

    @pytest.mark.parametrize(
        "filename",
        [
            "setup.exe",
            "command.com",
            "screensaver.scr",
            "old.pif",
            "install.msi",
            "patch.msp",
            "x.mst",
            "MyApp.app",
            "run.bat",
            "do.cmd",
            "go.command",
            "script.sh",
            "env.bash",
            "rc.csh",
            "c.tcsh",
            "k.ksh",
            "z.zsh",
            "f.fish",
            "lib.so",
            "lib.dylib",
            "plugin.dll",
            "driver.sys",
            "net.drv",
            "mod.ko",
            "app.jar",
            "web.jnlp",
            "mmc.msc",
            "a.vb",
            "b.vbs",
            "c.vbe",
            "d.wsh",
            "e.wsf",
            "f.wsc",
            "g.sct",
            "run.ps1",
            "mod.psm1",
            "cfg.psd1",
            "wf.cdxml",
            "page.hta",
            "x.htr",
            "scr.pl",
            "srv.py",
            "c.pyc",
            "gui.pyw",
            "web.rb",
            "api.php",
            "view.jsp",
            "page.asp",
            "form.aspx",
            "api.cgi",
            "a.action",
            "auto.workflow",
            "run.run",
            "doc.lnk",
            "site.url",
            "link.webloc",
            "r.locvars",
            "import.reg",
            "driver.inf",
            "signup.ins",
            "net.isp",
            "panel.cpl",
            "report.docm",
            "tmpl.dotm",
            "book.xlsm",
            "data.xlsb",
            "sheet.xltm",
            "add.xlam",
            "deck.pptm",
            "master.potm",
            "slides.sldm",
            "db.mde",
            "proj.ade",
            "compiled.accde",
            "macro.adp",
            "app.js",
            "enc.jse",
            "mod.mjs",
            "pkg.cjs",
            "inst.dmg",
            "pkg.pkg",
            "disc.iso",
            "disk.vhd",
            "disk2.vhdx",
            "vm.vmdk",
        ],
    )
    def test_rejects_blocked_extension_expanded(self, tmp_db, filename):
        with pytest.raises(cache.AttachmentError):
            cache.store_attachment_bytes(tmp_db, "a", "draft", _items([(filename, "application/octet-stream", b"x")]))

    def test_rejects_oversized_file(self, tmp_db):
        with pytest.raises(cache.AttachmentError, match="per-file limit"):
            cache.store_attachment_bytes(
                tmp_db, "a", "draft", _items([("big.bin", "application/octet-stream", b"x" * (11 * 1024 * 1024))])
            )

    def test_rejects_empty_payload(self, tmp_db):
        with pytest.raises(cache.AttachmentError, match="empty"):
            cache.store_attachment_bytes(tmp_db, "a", "draft", _items([("e.txt", "text/plain", b"")]))

    def test_rejects_too_many_files(self, tmp_db):
        items = _items([(f"f{i}.txt", "text/plain", b"x") for i in range(11)])
        with pytest.raises(cache.AttachmentError, match="Too many"):
            cache.store_attachment_bytes(tmp_db, "a", "draft", items)

    def test_rejects_total_over_limit(self, tmp_db):
        chunk = b"x" * (9 * 1024 * 1024)
        items = _items([(f"p{i}.bin", "application/octet-stream", chunk) for i in range(3)])
        with pytest.raises(cache.AttachmentError, match="total limit"):
            cache.store_attachment_bytes(tmp_db, "a", "draft", items)

    def test_settings_override_lowers_per_file_limit(self, tmp_db):
        cache.save_setting("max_attachment_size", str(1024 * 1024), tmp_db)
        with pytest.raises(cache.AttachmentError, match="per-file limit"):
            cache.store_attachment_bytes(
                tmp_db, "a", "draft", _items([("m.bin", "application/octet-stream", b"x" * (2 * 1024 * 1024))])
            )

    def test_validation_failure_writes_nothing(self, tmp_db):
        items = [
            {"filename": "ok.txt", "mime_type": "text/plain", "payload": b"ok"},
            {"filename": "bad.exe", "mime_type": "application/x-msdownload", "payload": b"MZ"},
        ]
        with pytest.raises(cache.AttachmentError):
            cache.store_attachment_bytes(tmp_db, "a", "draft", items)
        assert cache.list_attachments(tmp_db, "a", "draft") == []

    def test_total_cap_scales_with_per_file_setting(self, tmp_db):
        cache.save_setting("max_attachment_size", str(25 * 1024 * 1024), tmp_db)
        chunk = b"x" * (9 * 1024 * 1024)
        items = _items([(f"p{i}.bin", "application/octet-stream", chunk) for i in range(2)])
        created = cache.store_attachment_bytes(tmp_db, "a", "draft", items)
        assert len(created) == 2


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("../../etc/passwd", ".._.._etc_passwd"),
            ("a\x00b\n/c", "a_b__c"),
            ("normal.pdf", "normal.pdf"),
            ("", "attachment"),
            ("  trailing.   ", "trailing"),
        ],
    )
    def test_sanitizes(self, raw, expected):
        assert att.sanitize_filename(raw) == expected

    def test_caps_long_name(self):
        long_name = "a" * 500 + ".txt"
        out = att.sanitize_filename(long_name)
        assert len(out) <= 200
        assert out.endswith(".txt")


class TestPathSafety:
    def test_path_for_is_under_attachments_dir(self, tmp_db):
        from src.scripts.cache import attachments as _att

        created = cache.store_attachment_bytes(tmp_db, "d1", "draft", _items([("f.txt", "text/plain", b"x")]))
        path = cache.attachment_path(created[0])
        assert _att.ATTACHMENTS_DIR.resolve() in path.resolve().parents


class TestPruneOrphans:
    def test_removes_draft_owned_rows_with_no_draft(self, tmp_db):
        created = cache.store_attachment_bytes(
            tmp_db,
            "ghost-draft",
            cache.OWNER_DRAFT,
            _items([("f.txt", "text/plain", b"x"), ("g.txt", "text/plain", b"y")]),
        )
        paths = [cache.attachment_path(c) for c in created]
        assert all(p.is_file() for p in paths)

        removed = cache.prune_attachment_orphans(tmp_db)
        assert removed == 2
        assert cache.list_attachments(tmp_db, "ghost-draft", cache.OWNER_DRAFT) == []
        assert all(not p.exists() for p in paths)

    def test_removes_outbox_owned_rows_with_no_outbox(self, tmp_db):
        created = cache.store_attachment_bytes(
            tmp_db,
            "ghost-outbox",
            cache.OWNER_OUTBOX,
            _items([("h.txt", "text/plain", b"z")]),
        )
        path = cache.attachment_path(created[0])
        assert path.is_file()

        removed = cache.prune_attachment_orphans(tmp_db)
        assert removed == 1
        assert cache.list_attachments(tmp_db, "ghost-outbox", cache.OWNER_OUTBOX) == []
        assert not path.exists()

    def test_keeps_rows_whose_owner_exists(self, tmp_db):
        cache.save_draft(
            {
                "id": "real-draft",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            tmp_db,
        )
        kept = cache.store_attachment_bytes(
            tmp_db, "real-draft", cache.OWNER_DRAFT, _items([("k.txt", "text/plain", b"k")])
        )[0]
        oid = cache.enqueue(
            {
                "to_addr": "to@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            tmp_db,
        )
        cache.store_attachment_bytes(tmp_db, oid, cache.OWNER_OUTBOX, _items([("o.txt", "text/plain", b"o")]))

        removed = cache.prune_attachment_orphans(tmp_db)
        assert removed == 0
        assert len(cache.list_attachments(tmp_db, "real-draft", cache.OWNER_DRAFT)) == 1
        assert len(cache.list_attachments(tmp_db, oid, cache.OWNER_OUTBOX)) == 1
        assert cache.attachment_path(kept).is_file()

    def test_no_orphans_returns_zero(self, tmp_db):
        assert cache.prune_attachment_orphans(tmp_db) == 0


class TestAtRestEncryption:
    def test_attachment_round_trips_through_encryption(self, tmp_db):
        plaintext = b"super-secret-invoice-data" * 100
        created = cache.store_attachment_bytes(
            tmp_db,
            "enc-1",
            cache.OWNER_DRAFT,
            _items([("secret.bin", "application/octet-stream", plaintext)]),
        )[0]
        on_disk = cache.attachment_path(created).read_bytes()
        assert on_disk != plaintext
        assert b"super-secret" not in on_disk
        assert cache.read_attachment_bytes(created) == plaintext

    def test_size_column_stores_plaintext_length(self, tmp_db):
        created = cache.store_attachment_bytes(
            tmp_db,
            "enc-2",
            cache.OWNER_DRAFT,
            _items([("f.txt", "text/plain", b"x" * 4096)]),
        )[0]
        assert created["size"] == 4096


class TestPathConfinementGuard:
    @pytest.mark.parametrize(
        "disk_path",
        [
            "../../etc/passwd",
            "/etc/passwd",
            "/absolute/path/file.txt",
            "../escape.txt",
        ],
    )
    def test_abs_path_rejects_poisoned_disk_path(self, disk_path):
        from src.scripts.cache.attachments import storage

        row = {"id": "x", "disk_path": disk_path}
        with pytest.raises(att.AttachmentError):
            storage.path_for(row)

    def test_read_attachment_bytes_rejects_poisoned_disk_path(self):
        from src.scripts.cache.attachments import storage

        row = {"id": "x", "disk_path": "../../etc/passwd"}
        with pytest.raises(att.AttachmentError):
            storage.read_bytes_for(row)


class TestDecryptionFailure:
    def test_corrupt_blob_raises_on_read(self, tmp_db):
        from cryptography.fernet import InvalidToken

        created = cache.store_attachment_bytes(
            tmp_db,
            "corrupt-1",
            cache.OWNER_DRAFT,
            _items([("f.txt", "text/plain", b"original")]),
        )[0]
        path = cache.attachment_path(created)
        path.write_bytes(b"this-is-not-valid-fernet-ciphertext")
        with pytest.raises(InvalidToken):
            cache.read_attachment_bytes(created)


class TestAtomicPersist:
    def test_partial_store_failure_writes_no_rows_and_no_orphan_blobs(self, tmp_db, monkeypatch):
        from src.scripts.cache.attachments import queries as _queries

        items = _items(
            [
                ("a.txt", "text/plain", b"aaa"),
                ("b.txt", "text/plain", b"bbb"),
            ]
        )

        def boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(_queries, "_insert_many", boom)
        with pytest.raises(RuntimeError):
            cache.store_attachment_bytes(tmp_db, "atomic", cache.OWNER_DRAFT, items)

        assert cache.list_attachments(tmp_db, "atomic", cache.OWNER_DRAFT) == []
        remaining = [p for p in _queries.ATTACHMENTS_DIR.rglob("*") if p.is_file()]
        assert remaining == []


class FakeUpload:
    def __init__(self, filename, content_type, payload, size=None):
        self.filename = filename
        self.content_type = content_type
        self._payload = payload
        self.size = size
        self.reads = 0

    async def read(self):
        self.reads += 1
        return self._payload


class TestStoreUploadSingleRead:
    def test_reads_only_when_size_unknown(self, tmp_db):
        import asyncio

        upload = FakeUpload("f.txt", "text/plain", b"payload-here", size=None)
        created = asyncio.run(att.store_upload(tmp_db, "u1", cache.OWNER_DRAFT, [upload]))
        assert len(created) == 1
        assert upload.reads == 1
        assert cache.read_attachment_bytes(created[0]) == b"payload-here"

    def test_trusted_size_is_not_read_during_validation(self, tmp_db):
        import asyncio

        upload = FakeUpload("f.txt", "text/plain", b"payload-here", size=12)
        created = asyncio.run(att.store_upload(tmp_db, "u2", cache.OWNER_DRAFT, [upload]))
        assert upload.reads == 1
        assert cache.read_attachment_bytes(created[0]) == b"payload-here"


class TestReceivedAttachments:
    def test_owner_email_is_valid_owner_type(self, tmp_db):
        created = cache.store_attachment_bytes(
            tmp_db, "hash1", cache.OWNER_EMAIL, _items([("a.txt", "text/plain", b"a")])
        )
        assert len(created) == 1
        assert created[0]["owner_type"] == cache.OWNER_EMAIL

    def test_store_received_is_idempotent(self, tmp_db):
        items = _items([("a.txt", "text/plain", b"a"), ("b.txt", "text/plain", b"bb")])
        first = cache.store_received_attachments(tmp_db, "hash1", items)
        assert len(first) == 2
        second = cache.store_received_attachments(tmp_db, "hash1", items)
        assert {a["id"] for a in first} == {a["id"] for a in second}
        assert len(cache.list_attachments(tmp_db, "hash1", cache.OWNER_EMAIL)) == 2

    def test_store_received_bypasses_blocked_extensions(self, tmp_db):
        created = cache.store_received_attachments(
            tmp_db,
            "hash1",
            _items(
                [
                    ("script.py", "text/x-python", b"print('hi')"),
                    ("macro.docm", "application/vnd.ms-word.document.macroEnabled", b"x"),
                ]
            ),
        )
        assert len(created) == 2
        names = {a["filename"] for a in created}
        assert names == {"script.py", "macro.docm"}

    def test_store_received_skips_oversized_keeps_rest(self, tmp_db):
        big = b"x" * (cache.RECEIVED_MAX_TOTAL_SIZE + 1)
        created = cache.store_received_attachments(
            tmp_db,
            "hash1",
            _items(
                [
                    ("big.bin", "application/octet-stream", big),
                    ("small.txt", "text/plain", b"ok"),
                ]
            ),
        )
        assert [a["filename"] for a in created] == ["small.txt"]

    def test_store_received_enforces_total_cap(self, tmp_db):
        chunk = b"x" * (10 * 1024 * 1024)
        items = _items([(f"f{i}.bin", "application/octet-stream", chunk) for i in range(6)])
        created = cache.store_received_attachments(tmp_db, "hash1", items)
        assert len(created) == 5
        assert [a["filename"] for a in created] == [
            "f0.bin",
            "f1.bin",
            "f2.bin",
            "f3.bin",
            "f4.bin",
        ]

    def test_store_received_empty_returns_empty(self, tmp_db):
        assert cache.store_received_attachments(tmp_db, "hash1", []) == []
        assert cache.list_attachments(tmp_db, "hash1", cache.OWNER_EMAIL) == []


class TestListForOwners:
    def test_batches_multiple_owners(self, tmp_db):
        cache.store_received_attachments(tmp_db, "h1", _items([("a.txt", "text/plain", b"a")]))
        cache.store_received_attachments(
            tmp_db,
            "h2",
            _items([("b.txt", "text/plain", b"b"), ("c.txt", "text/plain", b"c")]),
        )
        out = cache.list_attachments_for_owners(tmp_db, ["h1", "h2", "h-empty"], cache.OWNER_EMAIL)
        assert sorted(out.keys()) == ["h1", "h2"]
        assert [a["filename"] for a in out["h1"]] == ["a.txt"]
        assert [a["filename"] for a in out["h2"]] == ["b.txt", "c.txt"]

    def test_empty_input_returns_empty(self, tmp_db):
        assert cache.list_attachments_for_owners(tmp_db, [], cache.OWNER_EMAIL) == {}


class TestPruneOrphansEmail:
    def _insert_email(self, db_path, message_id_hash):
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id) VALUES (?, ?)",
                (message_id_hash, f"<{message_id_hash}@e.com>"),
            )

    def test_removes_email_owned_rows_with_no_email(self, tmp_db):
        created = cache.store_received_attachments(tmp_db, "ghosthash", _items([("a.txt", "text/plain", b"a")]))
        path = cache.attachment_path(created[0])
        assert path.is_file()

        removed = cache.prune_attachment_orphans(tmp_db)
        assert removed == 1
        assert cache.list_attachments(tmp_db, "ghosthash", cache.OWNER_EMAIL) == []
        assert not path.exists()

    def test_keeps_email_owned_rows_whose_email_exists(self, tmp_db):
        self._insert_email(tmp_db, "realhash")
        created = cache.store_received_attachments(tmp_db, "realhash", _items([("a.txt", "text/plain", b"a")]))[0]
        removed = cache.prune_attachment_orphans(tmp_db)
        assert removed == 0
        assert len(cache.list_attachments(tmp_db, "realhash", cache.OWNER_EMAIL)) == 1
        assert cache.attachment_path(created).is_file()


class TestEmailDeleteCleanup:
    def _insert_email(self, db_path, message_id_hash):
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id) VALUES (?, ?)",
                (message_id_hash, f"<{message_id_hash}@e.com>"),
            )

    def test_delete_emails_by_hashes_removes_attachments(self, tmp_db):
        self._insert_email(tmp_db, "realhash")
        created = cache.store_received_attachments(
            tmp_db, "realhash", _items([("a.txt", "text/plain", b"a"), ("b.txt", "text/plain", b"b")])
        )
        path = cache.attachment_path(created[0])

        removed = cache.delete_emails_by_hashes(tmp_db, ["realhash"])

        assert removed == 1
        assert cache.list_attachments(tmp_db, "realhash", cache.OWNER_EMAIL) == []
        assert not path.exists()

    def test_delete_email_removes_attachments(self, tmp_db):
        message_id = "<cleanup@example.com>"
        import sqlite3

        owner_hash = cache._hash_message_id(message_id)
        with sqlite3.connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id) VALUES (?, ?)",
                (owner_hash, message_id),
            )
        created = cache.store_received_attachments(
            tmp_db, owner_hash, _items([("doc.pdf", "application/pdf", b"%PDF-1.4")])
        )[0]
        path = cache.attachment_path(created)

        deleted = cache.delete_email(message_id, tmp_db)

        assert deleted is True
        assert cache.list_attachments(tmp_db, owner_hash, cache.OWNER_EMAIL) == []
        assert not path.exists()


class TestSearchReceivedAttachments:
    def _insert_email(
        self, db_path, message_id_hash, sender="from@x.com", subject="Subject", date="Mon, 01 Jan 2024 10:00:00 +0000"
    ):
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, sender, subject, date) VALUES (?, ?, ?, ?, ?)",
                (message_id_hash, f"<{message_id_hash}@e.com>", sender, subject, date),
            )

    def _insert_attachments_with_timestamps(self, db_path, owner_id, items):
        import uuid
        import sqlite3
        from datetime import datetime
        import time

        base = time.time()
        with sqlite3.connect(db_path) as conn:
            for i, (name, mime, payload) in enumerate(items):
                attachment_id = uuid.uuid4().hex
                created_at = datetime.fromtimestamp(base + i).isoformat(sep=" ")
                conn.execute(
                    """INSERT INTO attachments (id, owner_type, owner_id, filename, mime_type, size, disk_path, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        attachment_id,
                        cache.OWNER_EMAIL,
                        owner_id,
                        name,
                        mime,
                        len(payload),
                        f"{attachment_id}/{name}",
                        created_at,
                    ),
                )
            conn.commit()

    def test_returns_rows_total_pages(self, tmp_db):

        self._insert_email(tmp_db, "h1")
        self._insert_attachments_with_timestamps(
            tmp_db,
            "h1",
            [
                ("a.txt", "text/plain", b"a"),
                ("b.pdf", "application/pdf", b"b"),
                ("c.png", "image/png", b"c"),
            ],
        )
        rows, total, pages = cache.search_received_attachments(tmp_db, page_size=10)
        assert total == 3
        assert pages == 1
        assert len(rows) == 3
        assert rows[0]["filename"] == "c.png"
        assert rows[-1]["filename"] == "a.txt"

    def test_search_substring_match(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("invoice_2024.pdf", "application/pdf", b"x"),
                    ("report.txt", "text/plain", b"y"),
                ]
            ),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, search="invoice", page_size=10)
        assert total == 1
        assert rows[0]["filename"] == "invoice_2024.pdf"

    def test_search_extension_match(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("doc.txt", "text/plain", b"x"),
                    ("notes.txt", "text/plain", b"y"),
                    ("image.png", "image/png", b"z"),
                ]
            ),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, search=".txt", page_size=10)
        assert total == 2
        assert {r["filename"] for r in rows} == {"doc.txt", "notes.txt"}

    def test_type_bucket_images(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("a.png", "image/png", b"x"),
                    ("b.jpg", "image/jpeg", b"y"),
                    ("c.txt", "text/plain", b"z"),
                ]
            ),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, type_bucket="images", page_size=10)
        assert total == 2
        assert {r["filename"] for r in rows} == {"a.png", "b.jpg"}

    def test_type_bucket_pdf(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("a.pdf", "application/pdf", b"x"),
                    ("b.txt", "text/plain", b"y"),
                ]
            ),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, type_bucket="pdf", page_size=10)
        assert total == 1
        assert rows[0]["filename"] == "a.pdf"

    def test_type_bucket_documents(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("a.txt", "text/plain", b"x"),
                    ("b.csv", "text/csv", b"y"),
                    ("c.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"z"),
                    ("d.png", "image/png", b"w"),
                ]
            ),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, type_bucket="documents", page_size=10)
        assert total == 3
        assert {r["filename"] for r in rows} == {"a.txt", "b.csv", "c.docx"}

    def test_type_bucket_archives(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("a.zip", "application/zip", b"x"),
                    ("b.gz", "application/gzip", b"y"),
                    ("c.txt", "text/plain", b"z"),
                ]
            ),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, type_bucket="archives", page_size=10)
        assert total == 2
        assert {r["filename"] for r in rows} == {"a.zip", "b.gz"}

    def test_type_bucket_other(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("a.txt", "text/plain", b"x"),
                    ("b.pdf", "application/pdf", b"y"),
                    ("c.unknown", "application/octet-stream", b"z"),
                ]
            ),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, type_bucket="other", page_size=10)
        assert total == 1
        assert rows[0]["filename"] == "c.unknown"

    def test_size_sort_asc(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("a.txt", "text/plain", b"aaa"),  # 3
                    ("b.txt", "text/plain", b"bb"),  # 2
                    ("c.txt", "text/plain", b"ccccc"),  # 5
                ]
            ),
        )
        rows, _, _ = cache.search_received_attachments(tmp_db, size_sort="asc", page_size=10)
        assert [r["filename"] for r in rows] == ["b.txt", "a.txt", "c.txt"]

    def test_size_sort_desc(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("a.txt", "text/plain", b"aaa"),  # 3
                    ("b.txt", "text/plain", b"bb"),  # 2
                    ("c.txt", "text/plain", b"ccccc"),  # 5
                ]
            ),
        )
        rows, _, _ = cache.search_received_attachments(tmp_db, size_sort="desc", page_size=10)
        assert [r["filename"] for r in rows] == ["c.txt", "a.txt", "b.txt"]

    def test_pagination(self, tmp_db):

        self._insert_email(tmp_db, "h1")
        self._insert_attachments_with_timestamps(tmp_db, "h1", [(f"f{i}.txt", "text/plain", b"x") for i in range(5)])
        rows, total, pages = cache.search_received_attachments(tmp_db, page=1, page_size=2)
        assert total == 5
        assert pages == 3
        assert len(rows) == 2
        assert rows[0]["filename"] == "f4.txt"

        rows2, _, _ = cache.search_received_attachments(tmp_db, page=2, page_size=2)
        assert len(rows2) == 2
        assert rows2[0]["filename"] == "f2.txt"

        rows3, _, _ = cache.search_received_attachments(tmp_db, page=3, page_size=2)
        assert len(rows3) == 1
        assert rows3[0]["filename"] == "f0.txt"

    def test_joins_email_fields(self, tmp_db):
        self._insert_email(
            tmp_db, "h1", sender="alice@test.com", subject="Hello", date="Tue, 02 Jan 2024 11:00:00 +0000"
        )
        cache.store_received_attachments(tmp_db, "h1", _items([("a.txt", "text/plain", b"a")]))
        rows, _, _ = cache.search_received_attachments(tmp_db, page_size=10)
        assert rows[0]["sender"] == "alice@test.com"
        assert rows[0]["subject"] == "Hello"
        assert rows[0]["date"] == "Tue, 02 Jan 2024 11:00:00 +0000"

    def test_left_join_includes_orphan_attachments(self, tmp_db):
        cache.store_received_attachments(tmp_db, "ghost", _items([("orphan.txt", "text/plain", b"x")]))
        rows, total, _ = cache.search_received_attachments(tmp_db, page_size=10)
        assert total == 1
        assert rows[0]["sender"] is None
        assert rows[0]["subject"] is None
        assert rows[0]["date"] is None
        assert rows[0]["filename"] == "orphan.txt"


class TestReceivedAttachmentTypeCounts:
    def _insert_email(self, db_path, message_id_hash):
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id) VALUES (?, ?)",
                (message_id_hash, f"<{message_id_hash}@e.com>"),
            )

    def test_counts_all_buckets(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items(
                [
                    ("a.png", "image/png", b"x"),
                    ("b.pdf", "application/pdf", b"y"),
                    ("c.txt", "text/plain", b"z"),
                    ("d.zip", "application/zip", b"w"),
                    ("e.unknown", "application/octet-stream", b"v"),
                ]
            ),
        )
        counts = cache.received_attachment_type_counts(tmp_db)
        assert counts["all"] == 5
        assert counts["images"] == 1
        assert counts["pdf"] == 1
        assert counts["documents"] == 1
        assert counts["archives"] == 1
        assert counts["other"] == 1

    def test_empty_db_returns_zeros(self, tmp_db):
        counts = cache.received_attachment_type_counts(tmp_db)
        assert all(v == 0 for v in counts.values())
        assert set(counts.keys()) == {"all", "images", "pdf", "documents", "archives", "other"}


class TestPublicMaxSizeAccessors:
    def test_get_max_attachment_size_returns_configured_value(self, tmp_db):
        assert cache.get_max_attachment_size(tmp_db) == 10 * 1024 * 1024
        cache.save_setting("max_attachment_size", str(25 * 1024 * 1024), tmp_db)
        assert cache.get_max_attachment_size(tmp_db) == 25 * 1024 * 1024

    def test_get_max_total_size_scales_with_per_file(self, tmp_db):
        assert cache.get_max_total_size(tmp_db) == 25 * 1024 * 1024
        cache.save_setting("max_attachment_size", str(20 * 1024 * 1024), tmp_db)
        assert cache.get_max_total_size(tmp_db) == 50 * 1024 * 1024


class TestSearchReceivedAttachmentsWildcardEscape:
    def _insert_email(self, db_path, message_id_hash):
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id) VALUES (?, ?)",
                (message_id_hash, f"<{message_id_hash}@e.com>"),
            )

    def test_percent_wildcard_escaped(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items([("100%.txt", "text/plain", b"x"), ("normal.txt", "text/plain", b"y")]),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, search="100%", page_size=10)
        assert total == 1
        assert rows[0]["filename"] == "100%.txt"

    def test_underscore_wildcard_escaped(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items([("a_b.txt", "text/plain", b"x"), ("aXb.txt", "text/plain", b"y")]),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, search="a_b", page_size=10)
        assert total == 1
        assert rows[0]["filename"] == "a_b.txt"

    def test_backslash_escaped(self, tmp_db):
        self._insert_email(tmp_db, "h1")
        cache.store_received_attachments(
            tmp_db,
            "h1",
            _items([("path~to.txt", "text/plain", b"x"), ("pathXto.txt", "text/plain", b"y")]),
        )
        rows, total, _ = cache.search_received_attachments(tmp_db, search="path~to", page_size=10)
        assert total == 1
        assert rows[0]["filename"] == "path~to.txt"
