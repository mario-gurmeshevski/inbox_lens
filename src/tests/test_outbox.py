from src.scripts import cache
from src.scripts.cache import outbox
from src.scripts.cache.db import _connect, _SCHEMA_VERSION


def _payload(**overrides):
    base = {
        "to_addr": "a@x.com",
        "cc": "",
        "bcc": "",
        "subject": "Hi",
        "body": "Body",
        "body_html": "<p>Body</p>",
        "mode": "new",
        "original_message_id": None,
        "thread_id": None,
        "draft_id": None,
    }
    base.update(overrides)
    return base


class TestOutboxCRUD:
    def test_enqueue_returns_id_and_lists(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        assert isinstance(oid, str) and oid
        rows = outbox.list_outbox(tmp_db)
        assert len(rows) == 1
        assert rows[0]["id"] == oid
        assert rows[0]["status"] == "pending"
        assert rows[0]["attempts"] == 0

    def test_get_returns_none_when_missing(self, tmp_db):
        assert outbox.get("nope", tmp_db) is None

    def test_delete(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        assert outbox.delete(oid, tmp_db) is True
        assert outbox.delete(oid, tmp_db) is False
        assert outbox.list_outbox(tmp_db) == []


class TestDueAndClaiming:
    def test_due_includes_immediate_pending(self, tmp_db):
        outbox.enqueue(_payload(), tmp_db)
        outbox.enqueue(_payload(), tmp_db)
        due = outbox.get_due(tmp_db)
        assert len(due) == 2

    def test_due_excludes_future_backoff(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        outbox.mark_failed(oid, "boom", "9999-01-01 00:00:00", tmp_db)
        assert outbox.get_due(tmp_db) == []
        assert outbox.count_pending(tmp_db) == 1

    def test_due_includes_past_backoff(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        outbox.mark_failed(oid, "boom", "2000-01-01 00:00:00", tmp_db)
        due = outbox.get_due(tmp_db)
        assert len(due) == 1
        assert due[0]["attempts"] == 1

    def test_due_excludes_real_worker_backoff_value(self, tmp_db):
        from src.scripts import outbox_worker

        oid = outbox.enqueue(_payload(), tmp_db)
        next_at = outbox_worker._next_attempt_at(0)
        assert next_at and not next_at.startswith("+")
        outbox.mark_failed(oid, "boom", next_at, tmp_db)
        assert outbox.get_due(tmp_db) == []
        assert outbox.count_pending(tmp_db) == 1

    def test_mark_attempting_claims_only_pending(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        assert outbox.mark_attempting(oid, tmp_db) is True
        assert outbox.mark_attempting(oid, tmp_db) is False
        assert outbox.get(oid, tmp_db)["status"] == "sending"

    def test_reclaim_skips_freshly_claimed_in_flight_row(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        assert outbox.mark_attempting(oid, tmp_db) is True
        assert outbox.reclaim_stale_sending(tmp_db) == 0
        assert outbox.get(oid, tmp_db)["status"] == "sending"
        assert outbox.get_due(tmp_db) == []

    def test_reclaim_stale_sending_resets_orphans(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        assert outbox.mark_attempting(oid, tmp_db) is True
        assert outbox.get_due(tmp_db) == []
        assert outbox.reclaim_stale_sending(tmp_db) == 0
        with _connect(tmp_db) as conn:
            conn.execute(
                "UPDATE outbox SET updated_at = datetime('now', '-1 hour') WHERE id = ?",
                (oid,),
            )

        reclaimed = outbox.reclaim_stale_sending(tmp_db)
        assert reclaimed == 1
        row = outbox.get(oid, tmp_db)
        assert row["status"] == "pending"
        assert row["next_attempt_at"] is None
        assert len(outbox.get_due(tmp_db)) == 1

    def test_reclaim_only_touches_sending_rows(self, tmp_db):
        pending = outbox.enqueue(_payload(), tmp_db)
        failed = outbox.enqueue(_payload(), tmp_db)
        outbox.mark_failed(failed, "boom", None, tmp_db)
        assert outbox.reclaim_stale_sending(tmp_db) == 0
        assert outbox.get(pending, tmp_db)["status"] == "pending"
        assert outbox.get(failed, tmp_db)["status"] == "failed"


class TestStatusTransitions:
    def test_mark_failed_increments_and_sets_next(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        outbox.mark_failed(oid, "err", "2020-01-01 00:00:00", tmp_db)
        row = outbox.get(oid, tmp_db)
        assert row["attempts"] == 1
        assert row["status"] == "pending"
        assert row["last_error"] == "err"

    def test_mark_failed_terminal_when_no_next(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        outbox.mark_failed(oid, "err", None, tmp_db)
        row = outbox.get(oid, tmp_db)
        assert row["status"] == "failed"
        assert row["next_attempt_at"] is None

    def test_mark_failed_truncates_long_error(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        long_err = "x" * 5000
        outbox.mark_failed(oid, long_err, None, tmp_db)
        assert len(outbox.get(oid, tmp_db)["last_error"]) == 500

    def test_mark_pending_resets_for_retry(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        outbox.mark_failed(oid, "boom", None, tmp_db)
        assert outbox.get(oid, tmp_db)["status"] == "failed"
        assert outbox.mark_pending(oid, tmp_db) is True
        row = outbox.get(oid, tmp_db)
        assert row["status"] == "pending"
        assert row["next_attempt_at"] is None

    def test_count_pending_counts_pending_and_failed(self, tmp_db):
        a = outbox.enqueue(_payload(), tmp_db)
        b = outbox.enqueue(_payload(), tmp_db)
        outbox.mark_failed(a, "boom", None, tmp_db)
        assert outbox.count_pending(tmp_db) == 2
        outbox.delete(b, tmp_db)
        assert outbox.count_pending(tmp_db) == 1


class TestUpdate:
    def test_update_fields_and_resets_pending(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        outbox.mark_failed(oid, "boom", None, tmp_db)
        outbox.update(oid, {"to_addr": "new@x.com", "subject": "Edited"}, tmp_db)
        row = outbox.get(oid, tmp_db)
        assert row["to_addr"] == "new@x.com"
        assert row["subject"] == "Edited"
        assert row["status"] == "pending"
        assert row["next_attempt_at"] is None

    def test_update_ignores_disallowed_keys(self, tmp_db):
        oid = outbox.enqueue(_payload(), tmp_db)
        outbox.update(oid, {"status": "sent", "attempts": 99}, tmp_db)
        row = outbox.get(oid, tmp_db)
        assert row["status"] == "pending"
        assert row["attempts"] == 0


class TestReexport:
    def test_cache_reexports(self, tmp_db):
        oid = cache.enqueue(_payload(), tmp_db)
        assert cache.get_outbox(oid, tmp_db)["id"] == oid
        assert cache.list_outbox(tmp_db)
        assert cache.count_pending(tmp_db) == 1
        assert cache.mark_outbox_attempting(oid, tmp_db) is True
        assert isinstance(cache.reclaim_stale_sending(tmp_db), int)
        assert cache.delete_outbox(oid, tmp_db) is True

    def test_cache_mark_sent_is_the_emails_version_not_outbox(self, tmp_db):
        cache.save_headers_batch([{"message_id": "<m@e.com>", "from": "a@b.com", "subject": "S", "date": ""}], tmp_db)
        h = cache._hash_message_id("<m@e.com>")
        cache.mark_sent([h], tmp_db)
        assert not hasattr(cache.outbox, "mark_sent")
        assert callable(cache.mark_sent)

    def test_outbox_submodule_accessible_on_cache(self, tmp_db):
        assert cache.outbox.STATUS_PENDING == "pending"
        oid = cache.outbox.enqueue(_payload(), tmp_db)
        assert cache.outbox.mark_attempting(oid, tmp_db) is True


class TestMigration:
    def test_schema_version_is_five(self):
        assert _SCHEMA_VERSION == 5

    def test_outbox_table_created_on_fresh_db(self, tmp_db):
        with _connect(tmp_db) as conn:
            names = {row["name"] for row in conn.execute("PRAGMA table_info(outbox)").fetchall()}
        assert "id" in names
        assert "next_attempt_at" in names
        assert "attempts" in names

    def test_user_version_bumped(self, tmp_db):
        with _connect(tmp_db) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 5

    def test_migrate_adds_outbox_to_pre_existing_db(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        cache.init_db(db_path)
        with _connect(db_path) as conn:
            conn.execute("DROP TABLE outbox")
            conn.execute("DROP INDEX IF EXISTS idx_outbox_status")
            conn.execute("DROP INDEX IF EXISTS idx_outbox_next")
            conn.execute("DROP TABLE attachments")
            conn.execute("DROP INDEX IF EXISTS idx_attachments_owner")
            conn.execute("PRAGMA user_version = 3")
        cache.init_db(db_path)
        with _connect(db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            assert "outbox" in tables
            assert "attachments" in tables
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        oid = outbox.enqueue(_payload(), db_path)
        assert outbox.get(oid, db_path)["status"] == "pending"
