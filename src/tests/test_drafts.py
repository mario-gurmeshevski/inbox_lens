from src.scripts import cache


class TestDrafts:
    def test_save_and_get_draft(self, tmp_db):
        draft = {
            "id": "abc123",
            "to_addr": "alice@example.com",
            "cc": "bob@example.com",
            "bcc": "secret@example.com",
            "subject": "Hi",
            "body": "Hello **world**",
            "mode": "new",
            "thread_id": None,
            "in_reply_to": None,
        }
        cache.save_draft(draft, tmp_db)
        got = cache.get_draft("abc123", tmp_db)
        assert got is not None
        assert got["id"] == "abc123"
        assert got["to_addr"] == "alice@example.com"
        assert got["cc"] == "bob@example.com"
        assert got["bcc"] == "secret@example.com"
        assert got["body"] == "Hello **world**"
        assert got["mode"] == "new"

    def test_save_draft_upserts_on_same_id(self, tmp_db):
        cache.save_draft(
            {
                "id": "d1",
                "to_addr": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "v1",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            tmp_db,
        )
        cache.save_draft(
            {
                "id": "d1",
                "to_addr": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "v2",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            tmp_db,
        )
        got = cache.get_draft("d1", tmp_db)
        assert got["body"] == "v2"

    def test_save_draft_refreshes_updated_at(self, tmp_db):
        cache.save_draft(
            {
                "id": "d2",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "",
                "body": "x",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            tmp_db,
        )
        with cache._connect(tmp_db) as conn:
            conn.execute("UPDATE drafts SET updated_at = '2000-01-01 00:00:00' WHERE id = 'd2'")
        cache.save_draft(
            {
                "id": "d2",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "",
                "body": "y",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            tmp_db,
        )
        with cache._connect(tmp_db) as conn:
            second = conn.execute("SELECT updated_at FROM drafts WHERE id = 'd2'").fetchone()["updated_at"]
        assert second > "2000-01-01 00:00:00"

    def test_get_missing_draft_returns_none(self, tmp_db):
        assert cache.get_draft("nope", tmp_db) is None

    def test_list_drafts_newest_first(self, tmp_db):
        for i, body in enumerate(["a", "b", "c"]):
            cache.save_draft(
                {
                    "id": f"d{i}",
                    "to_addr": "",
                    "cc": "",
                    "bcc": "",
                    "subject": f"s{i}",
                    "body": body,
                    "mode": "new",
                    "thread_id": None,
                    "in_reply_to": None,
                },
                tmp_db,
            )
        listed = cache.list_drafts(tmp_db)
        assert [d["id"] for d in listed] == ["d2", "d1", "d0"]

    def test_delete_draft(self, tmp_db):
        cache.save_draft(
            {
                "id": "d9",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "",
                "body": "x",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            tmp_db,
        )
        assert cache.delete_draft("d9", tmp_db) is True
        assert cache.get_draft("d9", tmp_db) is None
        assert cache.delete_draft("d9", tmp_db) is False

    def test_prune_stale_drafts(self, tmp_db):
        cache.save_draft(
            {
                "id": "old",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "",
                "body": "x",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            tmp_db,
        )
        with cache._connect(tmp_db) as conn:
            conn.execute("UPDATE drafts SET updated_at = '2000-01-01 00:00:00' WHERE id = 'old'")
        removed = cache.prune_stale_drafts(tmp_db, max_age_days=30)
        assert removed == 1
        assert cache.get_draft("old", tmp_db) is None
