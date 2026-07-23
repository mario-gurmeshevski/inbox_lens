import hashlib
import json
import sqlite3
import pytest

from src.scripts import cache
from src.tests._helpers import save_fetched as _save_fetched, save_fetched_batch as _save_fetched_batch


class TestHashMessageId:
    def test_returns_consistent_16_char_hex(self):
        result = cache._hash_message_id("<test@example.com>")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_inputs_produce_different_hashes(self):
        a = cache._hash_message_id("<a@example.com>")
        b = cache._hash_message_id("<b@example.com>")
        assert a != b

    def test_matches_sha256_first_16(self):
        mid = "<test@example.com>"
        expected = hashlib.sha256(mid.encode()).hexdigest()[:16]
        assert cache._hash_message_id(mid) == expected

    def test_empty_string_produces_hash(self):
        result = cache._hash_message_id("")
        assert len(result) == 16


class TestParseDateIso:
    def test_valid_rfc2822_date(self):
        result = cache._parse_date_iso("Mon, 01 Jan 2024 10:00:00 +0000")
        assert result is not None
        assert "2024-01-01" in result

    def test_invalid_date_returns_none(self):
        assert cache._parse_date_iso("not a date") is None

    def test_empty_string_returns_none(self):
        assert cache._parse_date_iso("") is None

    def test_none_input_returns_none(self):
        assert cache._parse_date_iso(None) is None


class TestConnect:
    def test_commits_on_success(self, tmp_db):
        with cache._connect(tmp_db) as conn:
            conn.execute("INSERT INTO emails (message_id_hash, message_id) VALUES (?, ?)", ("h1", "m1"))
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM emails WHERE message_id_hash = ?", ("h1",)).fetchone()
        assert row is not None

    def test_rolls_back_on_exception(self, tmp_db):
        try:
            with cache._connect(tmp_db) as conn:
                conn.execute("INSERT INTO emails (message_id_hash, message_id) VALUES (?, ?)", ("h2", "m2"))
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM emails WHERE message_id_hash = ?", ("h2",)).fetchone()
        assert row is None

    def test_sets_row_factory(self, tmp_db):
        with cache._connect(tmp_db) as conn:
            assert conn.row_factory == sqlite3.Row

    def test_sets_wal_journal_mode(self, tmp_db):
        with cache._connect(tmp_db) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


class TestInitDb:
    def test_creates_tables_and_indexes(self, tmp_db):
        with cache._connect(tmp_db) as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            assert any(t["name"] == "emails" for t in tables)

    def test_idempotent_multiple_calls(self, tmp_db):
        cache.init_db(tmp_db)
        cache.init_db(tmp_db)
        with cache._connect(tmp_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        assert count == 0


class TestBatchExistingHashes:
    def test_returns_set_of_existing_hashes(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        with cache._connect(tmp_db) as conn:
            result = cache._batch_existing_hashes(conn, [h, "nonexistent"])
        assert h in result
        assert "nonexistent" not in result

    def test_handles_chunking_over_500(self, tmp_db):
        hashes = [f"hash_{i}" for i in range(600)]
        with cache._connect(tmp_db) as conn:
            result = cache._batch_existing_hashes(conn, hashes)
        assert isinstance(result, set)


class TestReadEmails:
    def test_returns_all_emails_ordered_by_date_desc(self, tmp_db, sample_emails_batch):
        _save_fetched_batch(sample_emails_batch, tmp_db)
        emails = cache.read_emails(tmp_db)
        assert len(emails) == 5

    def test_returns_dicts_with_correct_keys(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        emails = cache.read_emails(tmp_db)
        assert len(emails) == 1
        e = emails[0]
        assert "message_id" in e
        assert "from" in e
        assert "subject" in e
        assert "date" in e
        assert "body" in e
        assert "status" in e


class TestDeleteEmail:
    def test_deletes_existing_email_returns_true(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        result = cache.delete_email(sample_email["message_id"], tmp_db)
        assert result is True

    def test_nonexistent_email_returns_false(self, tmp_db):
        result = cache.delete_email("<nonexistent@example.com>", tmp_db)
        assert result is False


class TestClearEmails:
    def test_clears_all_emails(self, tmp_db, sample_emails_batch):
        _save_fetched_batch(sample_emails_batch, tmp_db)
        assert cache.get_total_count(tmp_db) == len(sample_emails_batch)
        cache.clear_emails(tmp_db)
        assert cache.get_total_count(tmp_db) == 0

    def test_clear_when_empty_is_noop(self, tmp_db):
        cache.clear_emails(tmp_db)
        assert cache.get_total_count(tmp_db) == 0


class TestScanKeywords:
    def test_finds_matching_keywords(self, compiled_patterns):
        text = "This is an important problem that needs to be resolved immediately"
        result = cache._scan_keywords(text, compiled_patterns)
        assert "10" in result
        assert "important" in result["10"]
        assert "7" in result

    def test_returns_empty_for_no_matches(self, compiled_patterns):
        text = "hello world nothing to match here"
        assert cache._scan_keywords(text, compiled_patterns) == {}

    def test_returns_empty_for_empty_text(self, compiled_patterns):
        assert cache._scan_keywords("", compiled_patterns) == {}

    def test_returns_empty_for_empty_patterns(self):
        assert cache._scan_keywords("some text", {}) == {}

    def test_case_insensitive_matching(self, compiled_patterns):
        text = "UNSUBSCRIBE from this list"
        result = cache._scan_keywords(text, compiled_patterns)
        assert "1" in result
        assert "unsubscribe" in result["1"]


class TestScanAndUpdate:
    def test_scans_emails_and_updates_db(self, tmp_db, compiled_patterns):
        emails = [
            {"message_id": "<s1@example.com>", "subject": "important!", "body": "resolve this"},
            {"message_id": "<s2@example.com>", "subject": "hello", "body": "nothing here"},
        ]
        result = cache.scan_and_update(emails, tmp_db, compiled_patterns)
        assert result["scanned"] == 2
        assert result["total"] == 2
        assert result["emails_with_matches"]

    def test_skips_already_checked_with_matches(self, tmp_db, compiled_patterns):
        email = {"message_id": "<pre@example.com>", "subject": "important", "body": "test"}
        h = cache._hash_message_id(email["message_id"])
        kw_json = json.dumps({"10": ["important"]})
        with cache._connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, sender, subject, date, body, status, keyword_matches, category) "
                "VALUES (?, ?, '', '', '', '', 'checked', ?, '10')",
                (h, email["message_id"], kw_json),
            )
        result = cache.scan_and_update([email], tmp_db, compiled_patterns)
        assert result["already_checked"] == 1
        assert result["scanned"] == 0

    def test_skips_headers_only_status(self, tmp_db, compiled_patterns):
        email = {"message_id": "<hdr@example.com>", "subject": "test", "body": ""}
        h = cache._hash_message_id(email["message_id"])
        with cache._connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, sender, subject, date, body, status) VALUES (?,?,?,?,?,'','headers_only')",
                (
                    h,
                    email["message_id"],
                    "",
                    "test",
                    "",
                ),
            )
        result = cache.scan_and_update([email], tmp_db, compiled_patterns)
        assert result["skipped_no_body"] == 1

    def test_skips_fetched_no_body_status(self, tmp_db, compiled_patterns):
        email = {"message_id": "<fnb@example.com>", "subject": "test", "body": ""}
        h = cache._hash_message_id(email["message_id"])
        with cache._connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, sender, subject, date, body, status) VALUES (?,?,?,?,?,'','fetched_no_body')",
                (
                    h,
                    email["message_id"],
                    "",
                    "test",
                    "",
                ),
            )
        result = cache.scan_and_update([email], tmp_db, compiled_patterns)
        assert result["skipped_no_body"] == 1
        assert result["scanned"] == 0

    def test_returns_correct_stats(self, tmp_db, compiled_patterns):
        emails = [{"message_id": f"<x{i}@example.com>", "subject": "problem", "body": "test"} for i in range(3)]
        result = cache.scan_and_update(emails, tmp_db, compiled_patterns)
        assert result["total"] == 3
        assert result["scanned"] == 3

    def test_emails_without_message_id_handled(self, tmp_db, compiled_patterns):
        emails = [{"subject": "test", "body": "important"}]
        result = cache.scan_and_update(emails, tmp_db, compiled_patterns)
        assert result["total"] == 1


class TestGetEmailByHash:
    def test_returns_email_dict_for_existing(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        result = cache.get_email_by_hash(tmp_db, h)
        assert result is not None
        assert result["message_id"] == sample_email["message_id"]
        assert result["_file_hash"] == h
        assert result["_category"] == "8"

    def test_returns_none_for_nonexistent(self, tmp_db):
        assert cache.get_email_by_hash(tmp_db, "nonexistent") is None


class TestGetPriorityCounts:
    def test_returns_category_counts(self, tmp_db):
        for i in range(3):
            e = {
                "message_id": f"<pc{i}@e.com>",
                "subject": f"distinct subject {i}",
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "body": "b",
                "_category": "7",
            }
            _save_fetched(e, tmp_db)
        with cache._connect(tmp_db) as conn:
            conn.execute("UPDATE emails SET status = 'checked', category = '7'")
        counts = cache.get_priority_counts(tmp_db)
        assert counts.get("7") == 3

    def test_excludes_unchecked(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        counts = cache.get_priority_counts(tmp_db)
        assert counts == {}


class TestGetCounts:
    def test_returns_status_breakdown(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        counts = cache.get_counts(tmp_db)
        assert counts["fetched"] == 1
        assert counts["checked"] == 0

    def test_defaults_missing_statuses_to_zero(self, tmp_db):
        counts = cache.get_counts(tmp_db)
        assert counts["headers_only"] == 0
        assert counts["fetched"] == 0
        assert counts["checked"] == 0
        assert counts["fetched_no_body"] == 0

    def test_counts_fetched_no_body(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch([sample_headers_batch[0]], tmp_db)
        cache.update_bodies_batch([(sample_headers_batch[0]["message_id"], "")], tmp_db)
        counts = cache.get_counts(tmp_db)
        assert counts["fetched_no_body"] == 1


class TestSaveHeadersBatch:
    def test_inserts_headers_only_emails(self, tmp_db, sample_headers_batch):
        count = cache.save_headers_batch(sample_headers_batch, tmp_db)
        assert count == 3

    def test_status_is_headers_only(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch(sample_headers_batch, tmp_db)
        h = cache._hash_message_id(sample_headers_batch[0]["message_id"])
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT status FROM emails WHERE message_id_hash = ?", (h,)).fetchone()
        assert row["status"] == "headers_only"

    def test_skips_existing(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch(sample_headers_batch, tmp_db)
        count = cache.save_headers_batch(sample_headers_batch, tmp_db)
        assert count == 0

    def test_empty_list_returns_zero(self, tmp_db):
        assert cache.save_headers_batch([], tmp_db) == 0


class TestUpdateBodiesBatch:
    def test_updates_body_and_status(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch(sample_headers_batch, tmp_db)
        updates = [
            (sample_headers_batch[0]["message_id"], "Full body text"),
            (sample_headers_batch[1]["message_id"], "Another body"),
        ]
        updated = cache.update_bodies_batch(updates, tmp_db)
        assert updated == 2
        h = cache._hash_message_id(sample_headers_batch[0]["message_id"])
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT body, status FROM emails WHERE message_id_hash = ?", (h,)).fetchone()
        assert row["body"] == "Full body text"
        assert row["status"] == "fetched"

    def test_updates_any_body(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        updated = cache.update_bodies_batch([(sample_email["message_id"], "new body")], tmp_db)
        assert updated == 1

    def test_empty_body_marks_fetched_no_body(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch(sample_headers_batch, tmp_db)
        updated = cache.update_bodies_batch([(sample_headers_batch[0]["message_id"], "")], tmp_db)
        assert updated == 1
        h = cache._hash_message_id(sample_headers_batch[0]["message_id"])
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT body, status FROM emails WHERE message_id_hash = ?", (h,)).fetchone()
        assert row["status"] == "fetched_no_body"

    def test_empty_list_returns_zero(self, tmp_db):
        assert cache.update_bodies_batch([], tmp_db) == 0

    def test_mixed_simple_and_flag_rows_counts_both(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch(sample_headers_batch, tmp_db)
        mid_a = sample_headers_batch[0]["message_id"]
        mid_b = sample_headers_batch[1]["message_id"]
        updates = [
            (mid_a, "simple body"),  # 2-tuple
            (mid_b, "flag body", 1, 1),  # 4-tuple
        ]
        updated = cache.update_bodies_batch(updates, tmp_db)
        assert updated == 2
        ha = cache._hash_message_id(mid_a)
        hb = cache._hash_message_id(mid_b)
        with cache._connect(tmp_db) as conn:
            row_a = conn.execute("SELECT body, is_read FROM emails WHERE message_id_hash = ?", (ha,)).fetchone()
            row_b = conn.execute(
                "SELECT body, is_read, is_starred FROM emails WHERE message_id_hash = ?", (hb,)
            ).fetchone()
        assert row_a["body"] == "simple body"
        assert int(row_a["is_read"] or 0) == 0  # simple row leaves flags untouched
        assert row_b["body"] == "flag body"
        assert int(row_b["is_read"]) == 1
        assert int(row_b["is_starred"]) == 1

    def test_no_subject_select_issued(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch(sample_headers_batch, tmp_db)
        mid = sample_headers_batch[0]["message_id"]

        select_calls: list[str] = []

        class _WrappedConn:
            def __init__(self, cm):
                self._cm = cm
                self._real = None

            def __enter__(self):
                self._real = self._cm.__enter__()
                return self

            def __exit__(self, *exc):
                return self._cm.__exit__(*exc)

            def execute(self, sql, *args, **kwargs):
                normalized = " ".join(str(sql).upper().split())
                if normalized.startswith("SELECT") and "FROM EMAILS" in normalized:
                    select_calls.append(str(sql))
                return self._real.execute(sql, *args, **kwargs)

            def executemany(self, sql, *args, **kwargs):
                return self._real.executemany(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._real, name)

        import src.scripts.cache.db as db_mod

        real_connect = db_mod._connect

        def _spy_connect(db_path):
            return _WrappedConn(real_connect(db_path))

        db_mod._connect = _spy_connect
        try:
            updated = cache.update_bodies_batch([(mid, "body")], tmp_db)
        finally:
            db_mod._connect = real_connect
        assert updated == 1
        assert select_calls == [], f"unexpected SELECT on emails: {select_calls}"


class TestUpdateFlagsBatch:
    def test_refreshes_flags_without_disturbing_body_or_status(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        updated = cache.update_flags_batch([(1, 1, h)], tmp_db)
        assert updated == 1
        with cache._connect(tmp_db) as conn:
            row = conn.execute(
                "SELECT body, status, is_read, is_starred FROM emails WHERE message_id_hash = ?",
                (h,),
            ).fetchone()
        assert row["body"] == sample_email["body"]  # untouched
        assert row["status"] == "fetched"  # untouched
        assert int(row["is_read"]) == 1
        assert int(row["is_starred"]) == 1

    def test_clears_flags(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        cache.update_flags_batch([(1, 1, h)], tmp_db)
        cache.update_flags_batch([(0, 0, h)], tmp_db)
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT is_read, is_starred FROM emails WHERE message_id_hash = ?", (h,)).fetchone()
        assert int(row["is_read"]) == 0
        assert int(row["is_starred"]) == 0

    def test_empty_list_returns_zero(self, tmp_db):
        assert cache.update_flags_batch([], tmp_db) == 0

    def test_unknown_hash_updates_zero_rows(self, tmp_db):
        updated = cache.update_flags_batch([(1, 1, "nonexistent")], tmp_db)
        assert updated == 0

    def test_idempotent_reapply_counts_matched_row(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        cache.update_flags_batch([(1, 1, h)], tmp_db)
        updated = cache.update_flags_batch([(1, 1, h)], tmp_db)
        assert updated == 1


class TestGetHeadersOnlyMessageIds:
    def test_returns_message_ids_with_headers_only_status(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch(sample_headers_batch, tmp_db)
        result = cache.get_headers_only_message_ids(tmp_db)
        assert len(result) == 3

    def test_excludes_other_statuses(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        result = cache.get_headers_only_message_ids(tmp_db)
        assert len(result) == 0

    def test_excludes_fetched_no_body(self, tmp_db, sample_email):
        cache.save_headers_batch([sample_email], tmp_db)
        cache.update_bodies_batch([(sample_email["message_id"], "")], tmp_db)
        result = cache.get_headers_only_message_ids(tmp_db)
        assert len(result) == 0


class TestGetRecentEmails:
    def test_returns_recent_emails_limited(self, tmp_db):
        emails = [
            {
                "message_id": f"<r{i}@e.com>",
                "from": "s@e.com",
                "subject": f"Sub {i}",
                "date": f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
                "body": "b",
            }
            for i in range(10)
        ]
        _save_fetched_batch(emails, tmp_db)
        result = cache.get_recent_emails(tmp_db, limit=3)
        assert len(result) == 3

    def test_includes_keyword_matches(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        with cache._connect(tmp_db) as conn:
            conn.execute("UPDATE emails SET status = 'checked' WHERE message_id_hash = ?", (h,))
        result = cache.get_recent_emails(tmp_db, limit=1)
        assert "keyword_matches" in result[0]


class TestSearchEmails:
    def _seed_search_data(self, tmp_db):
        emails = [
            {
                "message_id": f"<se{i}@e.com>",
                "from": f"sender{i}@e.com",
                "subject": f"Subject about topic{i}",
                "date": f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
                "body": "body",
                "_category": "7" if i % 2 == 0 else "3",
            }
            for i in range(10)
        ]
        _save_fetched_batch(emails, tmp_db)
        hashes = [cache._hash_message_id(e["message_id"]) for e in emails]
        with cache._connect(tmp_db) as conn:
            conn.executemany(
                "UPDATE emails SET status = 'fetched' WHERE message_id_hash = ?",
                [(h,) for h in hashes],
            )
        return emails

    def test_no_filters_returns_all(self, tmp_db):
        self._seed_search_data(tmp_db)
        emails, total, pages = cache.search_emails(tmp_db)
        assert total == 10

    def test_filters_by_status(self, tmp_db):
        self._seed_search_data(tmp_db)
        emails, total, pages = cache.search_emails(tmp_db, status="fetched")
        assert total == 10
        assert all(e["status"] == "fetched" for e in emails)

    def test_filters_by_priority(self, tmp_db):
        self._seed_search_data(tmp_db)
        emails, total, pages = cache.search_emails(tmp_db, priority="7")
        assert total == 5

    def test_filters_by_search_text(self, tmp_db):
        self._seed_search_data(tmp_db)
        emails, total, pages = cache.search_emails(tmp_db, search="sender0")
        assert total == 1

    def test_search_in_subject(self, tmp_db):
        self._seed_search_data(tmp_db)
        emails, total, pages = cache.search_emails(tmp_db, search="topic1")
        assert total == 1

    def test_pagination_works(self, tmp_db):
        self._seed_search_data(tmp_db)
        emails_p1, total, pages = cache.search_emails(tmp_db, page=1, page_size=3)
        assert len(emails_p1) == 3
        assert pages == 4

    def test_returns_total_rows_and_pages(self, tmp_db):
        self._seed_search_data(tmp_db)
        emails, total, pages = cache.search_emails(tmp_db, page_size=5)
        assert total == 10
        assert pages == 2

    def test_sent_emails_remain_in_list(self, tmp_db):
        self._seed_search_data(tmp_db)
        emails, total, _ = cache.search_emails(tmp_db)
        assert total == 10
        sent_hash = cache._hash_message_id("<se0@e.com>")
        cache.mark_sent([sent_hash], tmp_db)
        emails2, total2, _ = cache.search_emails(tmp_db)
        assert total2 == 10

    def test_sent_emails_remain_in_recent(self, tmp_db):
        self._seed_search_data(tmp_db)
        before = cache.get_recent_emails(tmp_db, limit=10)
        assert len(before) == 10
        sent_hash = cache._hash_message_id("<se0@e.com>")
        cache.mark_sent([sent_hash], tmp_db)
        after = cache.get_recent_emails(tmp_db, limit=10)
        assert len(after) == 10

    def test_sent_email_remains_in_conversation(self, tmp_db):
        gm_thrid = "sent-reply-1"
        tid = cache._hash_message_id(gm_thrid)
        original = {
            "message_id": "<orig@e.com>",
            "from": "alice@e.com",
            "subject": "Purchase confirmed",
            "date": "Mon, 01 Jan 2024 10:00:00 +0000",
            "body": "thanks",
            "thread_id": tid,
            "gm_thrid": gm_thrid,
            "in_reply_to": "",
        }
        reply = {
            "message_id": "<reply@e.com>",
            "from": "me@e.com",
            "subject": "Re: Purchase confirmed",
            "date": "Tue, 02 Jan 2024 10:00:00 +0000",
            "body": "got it",
            "thread_id": tid,
            "gm_thrid": gm_thrid,
            "in_reply_to": "<orig@e.com>",
        }
        _save_fetched(original, tmp_db)
        _save_fetched(reply, tmp_db)
        cache.mark_sent([cache._hash_message_id(reply["message_id"])], tmp_db)
        conv = cache.get_conversation(tmp_db, cache._hash_message_id(original["message_id"]))
        mids = [c["message_id"] for c in conv]
        assert "<reply@e.com>" in mids
        reply_member = next(c for c in conv if c["message_id"] == "<reply@e.com>")
        assert reply_member["is_sent"] == 1

    def test_mark_sent_is_idempotent_and_ignores_empty(self, tmp_db):
        self._seed_search_data(tmp_db)
        h = cache._hash_message_id("<se0@e.com>")
        assert cache.mark_sent([h], tmp_db) == 1
        assert cache.mark_sent([h], tmp_db) == 1  # re-marking is fine
        assert cache.mark_sent([], tmp_db) == 0


class TestReconcileInbox:
    def test_deletes_cached_rows_absent_from_server(self, tmp_db):
        a = {
            "message_id": "<a@e.com>",
            "from": "x",
            "subject": "A",
            "date": "Mon, 01 Jan 2024 10:00:00 +0000",
            "body": "a",
        }
        b = {
            "message_id": "<b@e.com>",
            "from": "x",
            "subject": "B",
            "date": "Mon, 02 Jan 2024 10:00:00 +0000",
            "body": "b",
        }
        ghost = {
            "message_id": "<ghost@e.com>",
            "from": "x",
            "subject": "Ghost",
            "date": "Mon, 03 Jan 2024 10:00:00 +0000",
            "body": "gone",
        }
        for e in (a, b, ghost):
            _save_fetched(e, tmp_db)
        ha = cache._hash_message_id(a["message_id"])
        hb = cache._hash_message_id(b["message_id"])
        hghost = cache._hash_message_id(ghost["message_id"])
        removed = cache.reconcile_inbox(tmp_db, {ha, hb}, protected_hashes=set())
        assert removed == 1
        assert cache.get_email_by_hash(tmp_db, hghost) is None
        assert cache.get_email_by_hash(tmp_db, ha) is not None
        assert cache.get_email_by_hash(tmp_db, hb) is not None

    def test_preserves_sent_replies(self, tmp_db):
        inbox = {
            "message_id": "<a@e.com>",
            "from": "x",
            "subject": "A",
            "date": "Mon, 01 Jan 2024 10:00:00 +0000",
            "body": "a",
        }
        sent = {
            "message_id": "<sent@e.com>",
            "from": "me",
            "subject": "Re: A",
            "date": "Mon, 02 Jan 2024 10:00:00 +0000",
            "body": "reply",
            "in_reply_to": "<a@e.com>",
        }
        _save_fetched(inbox, tmp_db)
        _save_fetched(sent, tmp_db)
        cache.mark_sent([cache._hash_message_id(sent["message_id"])], tmp_db)

        ha = cache._hash_message_id(inbox["message_id"])
        cache.reconcile_inbox(tmp_db, {ha}, protected_hashes=set())  # only inbox hash in server set
        assert cache.get_email_by_hash(tmp_db, ha) is not None
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id(sent["message_id"])) is not None

    def test_skips_when_fetch_ratio_below_threshold(self, tmp_db):
        _save_fetched(
            {
                "message_id": "<ghost@e.com>",
                "from": "x",
                "subject": "Ghost",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "gone",
            },
            tmp_db,
        )
        hghost = cache._hash_message_id("<ghost@e.com>")
        removed = cache.reconcile_inbox(tmp_db, set(), protected_hashes=set(), searched_count=100)
        assert removed == 0
        assert cache.get_email_by_hash(tmp_db, hghost) is not None

    def test_skips_when_no_protected_hashes(self, tmp_db):
        _save_fetched(
            {
                "message_id": "<ghost@e.com>",
                "from": "x",
                "subject": "Ghost",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "gone",
            },
            tmp_db,
        )
        hghost = cache._hash_message_id("<ghost@e.com>")
        removed = cache.reconcile_inbox(tmp_db, set(), protected_hashes=None)
        assert removed == 0
        assert cache.get_email_by_hash(tmp_db, hghost) is not None

    def test_skips_when_ghosts_exceed_cap(self, tmp_db):
        for i in range(20):
            _save_fetched(
                {
                    "message_id": f"<e{i}@e.com>",
                    "from": "x",
                    "subject": f"E{i}",
                    "date": f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
                    "body": "body",
                },
                tmp_db,
            )
        one_hash = cache._hash_message_id("<e0@e.com>")
        removed = cache.reconcile_inbox(tmp_db, {one_hash}, protected_hashes=set(), searched_count=20)
        assert removed == 0
        # All rows preserved.
        assert cache.get_total_count(tmp_db) == 20

    def test_force_bypasses_guards(self, tmp_db):
        _save_fetched(
            {
                "message_id": "<a@e.com>",
                "from": "x",
                "subject": "A",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "a",
            },
            tmp_db,
        )
        removed = cache.reconcile_inbox(tmp_db, set(), force=True)
        assert removed == 1
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<a@e.com>")) is None

    def test_no_changes_when_everything_present(self, tmp_db):
        _save_fetched(
            {
                "message_id": "<a@e.com>",
                "from": "x",
                "subject": "A",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "a",
            },
            tmp_db,
        )
        removed = cache.reconcile_inbox(tmp_db, {cache._hash_message_id("<a@e.com>")}, protected_hashes=set())
        assert removed == 0


class TestRowToDict:
    def test_converts_row_to_dict_with_all_fields(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM emails LIMIT 1").fetchone()
        d = cache._row_to_dict(row)
        assert d["message_id"] == sample_email["message_id"]
        assert d["from"] == sample_email["from"]
        assert d["subject"] == sample_email["subject"]
        assert d["body"] == sample_email["body"]
        assert d["status"] == "fetched"
        assert d["keyword_matches"] == sample_email["keyword_matches"]
        assert d["thread_id"] == "abc123def456"
        assert d["in_reply_to"] == "<parent@example.com>"
        assert d["is_sent"] == 0

    def test_handles_invalid_keyword_json(self, tmp_db):
        with cache._connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, sender, subject, date, body, keyword_matches) "
                "VALUES ('h','m','s','sub','d','','not-json')"
            )
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM emails LIMIT 1").fetchone()
        d = cache._row_to_dict(row)
        assert d["keyword_matches"] == {}

    def test_handles_null_keyword_matches(self, tmp_db):
        with cache._connect(tmp_db) as conn:
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, sender, subject, date, body) "
                "VALUES ('h2','m2','s','sub','d','')"
            )
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM emails WHERE message_id_hash = 'h2'").fetchone()
        d = cache._row_to_dict(row)
        assert d["keyword_matches"] == {}


class TestGetConnReuse:
    def test_reuses_cached_connection(self, tmp_db):
        conn1 = cache._get_conn(tmp_db)
        conn2 = cache._get_conn(tmp_db)
        assert conn1 is conn2


class TestConnectRollbackFailure:
    def test_clears_thread_local_when_rollback_fails(self, tmp_db, monkeypatch):
        from unittest.mock import MagicMock

        from src.scripts.cache import db as db_mod

        fake = MagicMock()
        fake.commit = MagicMock()
        fake.rollback.side_effect = RuntimeError("rollback broken")
        fake.close = MagicMock()
        key = f"_conn_{tmp_db}"
        setattr(db_mod._local, key, fake)

        with pytest.raises(RuntimeError, match="app error"):
            with cache._connect(tmp_db):
                raise RuntimeError("app error")

        fake.rollback.assert_called_once()
        fake.close.assert_called_once()
        assert getattr(db_mod._local, key, None) is None


class TestMigrateThreadId:
    def test_init_db_creates_thread_columns(self, tmp_db):
        with cache._connect(tmp_db) as conn:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(emails)").fetchall()]
        assert "thread_id" in cols
        assert "in_reply_to" in cols


class TestCheckHashesExist:
    def test_empty_hashes_returns_empty_set(self, tmp_db):
        assert cache.check_hashes_exist(tmp_db, []) == set()

    def test_returns_only_existing_hashes(self, tmp_db, sample_email):
        _save_fetched(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        result = cache.check_hashes_exist(tmp_db, [h, "missing"])
        assert result == {h}


class TestGetTotalCount:
    def test_zero_on_empty_db(self, tmp_db):
        assert cache.get_total_count(tmp_db) == 0

    def test_counts_all_rows(self, tmp_db, sample_emails_batch):
        _save_fetched_batch(sample_emails_batch, tmp_db)
        assert cache.get_total_count(tmp_db) == 5


class TestSearchEmailsStatusBranches:
    def _seed(self, tmp_db):
        emails = []
        for i, status in enumerate(["fetched", "checked", "headers_only"]):
            e = {
                "message_id": f"<st{i}@e.com>",
                "from": "s@e.com",
                "subject": f"Subject {i}",
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "body": "b",
            }
            emails.append(e)
        _save_fetched_batch(emails, tmp_db)
        hashes = [cache._hash_message_id(e["message_id"]) for e in emails]
        statuses = ["checked", "headers_only"]
        with cache._connect(tmp_db) as conn:
            for h, s in zip(hashes[1:], statuses):
                conn.execute("UPDATE emails SET status = ? WHERE message_id_hash = ?", (s, h))
        return emails

    def test_status_checked(self, tmp_db):
        self._seed(tmp_db)
        _, total, _ = cache.search_emails(tmp_db, status="checked")
        assert total == 1

    def test_status_headers_only(self, tmp_db):
        self._seed(tmp_db)
        _, total, _ = cache.search_emails(tmp_db, status="headers_only")
        assert total == 1

    def test_status_headers_only_includes_fetched_no_body(self, tmp_db):
        self._seed(tmp_db)
        email = {
            "message_id": "<fnb@e.com>",
            "from": "s@e.com",
            "subject": "empty",
            "date": "Mon, 01 Jan 2024 00:00:00 +0000",
            "body": "",
        }
        cache.save_headers_batch([email], tmp_db)
        cache.update_bodies_batch([(email["message_id"], "")], tmp_db)
        _, total, _ = cache.search_emails(tmp_db, status="headers_only")
        assert total == 2

    def test_combined_status_and_priority_filters(self, tmp_db):
        emails = [
            {
                "message_id": f"<c{i}@e.com>",
                "from": "s@e.com",
                "subject": f"Sub {i}",
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "body": "b",
                "_category": "7" if i < 2 else "3",
            }
            for i in range(4)
        ]
        _save_fetched_batch(emails, tmp_db)
        hashes = [cache._hash_message_id(e["message_id"]) for e in emails]
        with cache._connect(tmp_db) as conn:
            conn.executemany("UPDATE emails SET status = 'checked' WHERE message_id_hash = ?", [(h,) for h in hashes])
        _, total, _ = cache.search_emails(tmp_db, status="checked", priority="7")
        assert total == 2

    def test_unknown_status_falls_through_no_status_filter(self, tmp_db):
        self._seed(tmp_db)
        _, total, _ = cache.search_emails(tmp_db, status="bogus")
        assert total == 3


class TestScanAndUpdateExtraBranches:
    def test_skips_already_checked_with_empty_json_variants(self, tmp_db, compiled_patterns):
        for empty_json in ("{}", "[]", '""'):
            email = {"message_id": f"<empty{empty_json}@e.com>", "subject": "test", "body": "x"}
            h = cache._hash_message_id(email["message_id"])
            with cache._connect(tmp_db) as conn:
                conn.execute(
                    "INSERT INTO emails (message_id_hash, message_id, sender, subject, date, body, status, keyword_matches, category) "
                    "VALUES (?, ?, '', '', '', '', 'checked', ?, 'unclassified')",
                    (h, email["message_id"], empty_json),
                )
            result = cache.scan_and_update([email], tmp_db, compiled_patterns)
            assert result["already_checked"] == 1
            assert email["keyword_matches"] == {}

    def test_threadpool_path_used_for_many_emails(self, tmp_db, compiled_patterns):
        emails = [{"message_id": f"<tp{i}@e.com>", "subject": f"problem {i}", "body": "test"} for i in range(8)]
        result = cache.scan_and_update(emails, tmp_db, compiled_patterns)
        assert result["scanned"] == 8
        assert result["emails_with_matches"]

    def test_unclassified_fallback_when_no_matches(self, tmp_db, compiled_patterns):
        email = {"message_id": "<nomatch@e.com>", "subject": "nothing here", "body": "xyz"}
        _save_fetched(email, tmp_db)
        result = cache.scan_and_update([email], tmp_db, compiled_patterns)
        assert result["scanned"] == 1
        assert not result["emails_with_matches"]
        h = cache._hash_message_id("<nomatch@e.com>")
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT status, category FROM emails WHERE message_id_hash = ?", (h,)).fetchone()
        assert row["status"] == "checked"
        assert row["category"] == "unclassified"


class TestScanKeywordsEdgeCases:
    def test_handles_none_text(self, compiled_patterns):
        assert cache._scan_keywords(None, compiled_patterns) == {}

    def test_deduplicates_matches(self, compiled_patterns):
        text = "important important important"
        result = cache._scan_keywords(text, compiled_patterns)
        assert result["10"] == ["important"]


class TestReadStarredColumns:
    def _seed(self, db, n=2):
        for i in range(n):
            cache.save_headers_batch(
                [
                    {
                        "message_id": f"<rs{i}@e.com>",
                        "from": f"s{i}@e.com",
                        "subject": f"Subject {i}",
                        "date": f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
                        "thread_id": None,
                        "in_reply_to": None,
                    }
                ],
                db,
            )
            cache.update_bodies_batch([(f"<rs{i}@e.com>", f"body{i}")], db)

    def test_defaults_are_zero(self, tmp_db):
        self._seed(tmp_db)
        emails, _, _ = cache.search_emails(tmp_db)
        assert all(e["is_read"] == 0 for e in emails)
        assert all(e["is_starred"] == 0 for e in emails)

    def test_set_read_by_hashes_bulk(self, tmp_db):
        self._seed(tmp_db, 3)
        hashes = [cache._hash_message_id(f"<rs{i}@e.com>") for i in range(3)]
        updated = cache.set_read_by_hashes(tmp_db, hashes, True)
        assert updated == 3
        for h in hashes:
            assert cache.get_email_by_hash(tmp_db, h)["is_read"] == 1

    def test_set_starred_by_hashes_bulk(self, tmp_db):
        self._seed(tmp_db, 3)
        hashes = [cache._hash_message_id(f"<rs{i}@e.com>") for i in range(3)]
        assert cache.set_starred_by_hashes(tmp_db, hashes, True) == 3
        # Toggle off
        assert cache.set_starred_by_hashes(tmp_db, hashes, False) == 3
        for h in hashes:
            assert cache.get_email_by_hash(tmp_db, h)["is_starred"] == 0

    def test_set_read_by_hashes_empty(self, tmp_db):
        assert cache.set_read_by_hashes(tmp_db, [], True) == 0

    def test_set_flag_by_hashes_rejects_unknown_column(self, tmp_db):
        with pytest.raises(ValueError):
            cache._set_flag_by_hashes(tmp_db, ["h"], "body", True)
        with pytest.raises(ValueError):
            cache._set_flag_by_hashes(tmp_db, ["h"], "is_read; DROP TABLE emails", True)

    def test_get_message_ids_by_hashes(self, tmp_db):
        self._seed(tmp_db, 2)
        h0 = cache._hash_message_id("<rs0@e.com>")
        h1 = cache._hash_message_id("<rs1@e.com>")
        pairs = cache.get_message_ids_by_hashes(tmp_db, [h0, h1, "nonexistent"])
        mids = {p[1] for p in pairs}
        assert "<rs0@e.com>" in mids
        assert "<rs1@e.com>" in mids
        assert len(pairs) == 2

    def test_delete_emails_by_hashes(self, tmp_db):
        self._seed(tmp_db, 3)
        hashes = [cache._hash_message_id(f"<rs{i}@e.com>") for i in range(3)]
        removed = cache.delete_emails_by_hashes(tmp_db, hashes)
        assert removed == 3
        assert cache.get_total_count(tmp_db) == 0

    def test_delete_emails_by_hashes_empty(self, tmp_db):
        assert cache.delete_emails_by_hashes(tmp_db, []) == 0

    def test_update_bodies_batch_with_flags(self, tmp_db):
        cache.save_headers_batch(
            [
                {
                    "message_id": "<flag@e.com>",
                    "from": "s@e.com",
                    "subject": "S",
                    "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                    "thread_id": None,
                    "in_reply_to": None,
                }
            ],
            tmp_db,
        )
        cache.update_bodies_batch([("<flag@e.com>", "body", True, True)], tmp_db)
        h = cache._hash_message_id("<flag@e.com>")
        email = cache.get_email_by_hash(tmp_db, h)
        assert email["is_read"] == 1
        assert email["is_starred"] == 1
        assert email["body"] == "body"


class TestSchemaMigration:
    def test_migrates_existing_database_without_losing_data(self, tmp_path):
        import sqlite3

        db_path = str(tmp_path / "old.db")
        old_schema = """
        CREATE TABLE emails (
            message_id_hash TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            sender TEXT,
            subject TEXT,
            date TEXT,
            date_parsed TEXT,
            body TEXT,
            status TEXT DEFAULT 'fetched',
            category TEXT,
            keyword_matches TEXT,
            thread_id TEXT,
            in_reply_to TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
        with sqlite3.connect(db_path) as conn:
            conn.executescript(old_schema)
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, sender, subject) "
                "VALUES ('h1', '<m@e.com>', 's@e.com', 'preserved')"
            )

        cache.init_db(db_path)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM emails WHERE message_id_hash = 'h1'").fetchone()
            cols = {r[1] for r in conn.execute("PRAGMA table_info(emails)").fetchall()}

        assert "is_read" in cols
        assert "is_starred" in cols
        assert row["subject"] == "preserved"
        assert int(row["is_read"] or 0) == 0
        assert int(row["is_starred"] or 0) == 0

    def test_migrate_is_idempotent(self, tmp_db):
        cache.init_db(tmp_db)
        cache.init_db(tmp_db)
        cache.save_headers_batch(
            [
                {
                    "message_id": "<idem@e.com>",
                    "from": "s@e.com",
                    "subject": "S",
                    "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                    "thread_id": None,
                    "in_reply_to": None,
                }
            ],
            tmp_db,
        )
        assert cache.get_total_count(tmp_db) == 1

    def test_migrate_backfills_null_thread_id(self, tmp_path):
        import sqlite3

        db_path = str(tmp_path / "legacy.db")
        old_schema = """
        CREATE TABLE emails (
            message_id_hash TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            sender TEXT,
            subject TEXT,
            date TEXT,
            date_parsed TEXT,
            body TEXT,
            status TEXT DEFAULT 'fetched',
            category TEXT,
            keyword_matches TEXT,
            thread_id TEXT,
            in_reply_to TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
        with sqlite3.connect(db_path) as conn:
            conn.executescript(old_schema)
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, thread_id) VALUES ('h1', '<m1@e.com>', NULL)"
            )
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, thread_id) VALUES ('h2', '<m2@e.com>', NULL)"
            )
            conn.execute(
                "INSERT INTO emails (message_id_hash, message_id, thread_id) VALUES ('h3', '<m3@e.com>', 'keep-me')"
            )

        cache.init_db(db_path)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = {
                r["message_id_hash"]: r["thread_id"]
                for r in conn.execute("SELECT message_id_hash, thread_id FROM emails")
            }

        assert rows["h1"] == "h1"
        assert rows["h2"] == "h2"
        assert rows["h3"] == "keep-me"


class TestGetConversation:
    GM_THRID = "1809095669921875987"

    def _save(
        self,
        db,
        message_id,
        in_reply_to=None,
        subject="Topic",
        date="Mon, 01 Jan 2024 10:00:00 +0000",
        gm_thrid=GM_THRID,
    ):
        tid = cache._hash_message_id(gm_thrid) if gm_thrid else None
        _save_fetched(
            {
                "message_id": message_id,
                "from": "sender@example.com",
                "subject": subject,
                "date": date,
                "body": "body",
                "thread_id": tid,
                "gm_thrid": gm_thrid,
                "in_reply_to": in_reply_to,
            },
            db,
        )

    def test_includes_seed_and_replies_oldest_first(self, tmp_db):
        self._save(tmp_db, "<root@e.com>", None, date="Mon, 01 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<reply1@e.com>", "<root@e.com>", date="Tue, 02 Jan 2024 10:00:00 +0000")
        root_hash = cache._hash_message_id("<root@e.com>")
        conv = cache.get_conversation(tmp_db, root_hash)
        assert [c["message_id"] for c in conv] == ["<root@e.com>", "<reply1@e.com>"]

    def test_same_conversation_regardless_of_which_message_clicked(self, tmp_db):
        self._save(tmp_db, "<root@e.com>", None, date="Mon, 01 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<reply1@e.com>", "<root@e.com>", date="Tue, 02 Jan 2024 10:00:00 +0000")
        from_root = [c["message_id"] for c in cache.get_conversation(tmp_db, cache._hash_message_id("<root@e.com>"))]
        from_reply = [c["message_id"] for c in cache.get_conversation(tmp_db, cache._hash_message_id("<reply1@e.com>"))]
        assert from_root == from_reply == ["<root@e.com>", "<reply1@e.com>"]

    def test_includes_sibling_replies_ordered_oldest_first(self, tmp_db):
        self._save(tmp_db, "<root@e.com>", None, date="Mon, 01 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<r1@e.com>", "<root@e.com>", date="Tue, 02 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<r2@e.com>", "<root@e.com>", date="Wed, 03 Jan 2024 10:00:00 +0000")
        root_hash = cache._hash_message_id("<root@e.com>")
        conv = cache.get_conversation(tmp_db, root_hash)
        assert [c["message_id"] for c in conv] == ["<root@e.com>", "<r1@e.com>", "<r2@e.com>"]

    def test_multi_level_chain_all_chronological(self, tmp_db):
        self._save(tmp_db, "<a@e.com>", None, date="Mon, 01 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<b@e.com>", "<a@e.com>", date="Tue, 02 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<c@e.com>", "<b@e.com>", date="Wed, 03 Jan 2024 10:00:00 +0000")
        mid_hash = cache._hash_message_id("<b@e.com>")
        conv = cache.get_conversation(tmp_db, mid_hash)
        assert [c["message_id"] for c in conv] == ["<a@e.com>", "<b@e.com>", "<c@e.com>"]

    def test_single_message_returns_just_itself(self, tmp_db):
        self._save(tmp_db, "<lonely@e.com>", None)
        h = cache._hash_message_id("<lonely@e.com>")
        conv = cache.get_conversation(tmp_db, h)
        assert [c["message_id"] for c in conv] == ["<lonely@e.com>"]

    def test_returns_empty_for_unknown_hash(self, tmp_db):
        assert cache.get_conversation(tmp_db, "nonexistent") == []

    def test_result_rows_include_hash_and_category(self, tmp_db):
        self._save(tmp_db, "<root@e.com>", None)
        self._save(tmp_db, "<r1@e.com>", "<root@e.com>")
        root_hash = cache._hash_message_id("<root@e.com>")
        conv = cache.get_conversation(tmp_db, root_hash)
        assert len(conv) == 2
        assert conv[1]["_file_hash"] == cache._hash_message_id("<r1@e.com>")
        assert conv[1]["_category"] == "unclassified"

    def test_get_conversation_merges_after_thread_refresh(self, tmp_db):
        self._save(tmp_db, "<orig@e.com>", None, gm_thrid=None)
        self._save(tmp_db, "<reply@e.com>", "<orig@e.com>", gm_thrid="shared-thrid")

        root_hash = cache._hash_message_id("<orig@e.com>")
        assert [c["message_id"] for c in cache.get_conversation(tmp_db, root_hash)] == ["<orig@e.com>"]

        shared_tid = cache._hash_message_id("shared-thrid")
        cache.refresh_thread_ids(
            [
                (shared_tid, "shared-thrid", cache._hash_message_id("<orig@e.com>")),
                (shared_tid, "shared-thrid", cache._hash_message_id("<reply@e.com>")),
            ],
            tmp_db,
        )
        conv = cache.get_conversation(tmp_db, root_hash)
        assert [c["message_id"] for c in conv] == ["<orig@e.com>", "<reply@e.com>"]

    def test_limit_returns_oldest_n(self, tmp_db):
        self._save(tmp_db, "<m1@e.com>", None, date="Mon, 01 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<m2@e.com>", "<m1@e.com>", date="Tue, 02 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<m3@e.com>", "<m2@e.com>", date="Wed, 03 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<m4@e.com>", "<m3@e.com>", date="Thu, 04 Jan 2024 10:00:00 +0000")
        root_hash = cache._hash_message_id("<m1@e.com>")
        conv = cache.get_conversation(tmp_db, root_hash, limit=2)
        assert [c["message_id"] for c in conv] == ["<m1@e.com>", "<m2@e.com>"]

    def test_limit_zero_returns_all(self, tmp_db):
        self._save(tmp_db, "<m1@e.com>", None, date="Mon, 01 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<m2@e.com>", "<m1@e.com>", date="Tue, 02 Jan 2024 10:00:00 +0000")
        root_hash = cache._hash_message_id("<m1@e.com>")
        conv = cache.get_conversation(tmp_db, root_hash, limit=0)
        assert len(conv) == 2

    def test_limit_exceeds_thread_returns_all(self, tmp_db):
        self._save(tmp_db, "<m1@e.com>", None, date="Mon, 01 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<m2@e.com>", "<m1@e.com>", date="Tue, 02 Jan 2024 10:00:00 +0000")
        self._save(tmp_db, "<m3@e.com>", "<m2@e.com>", date="Wed, 03 Jan 2024 10:00:00 +0000")
        root_hash = cache._hash_message_id("<m1@e.com>")
        conv = cache.get_conversation(tmp_db, root_hash, limit=10)
        assert len(conv) == 3


class TestConversationGrouping:
    _thread_counter = 0

    def _thread(self, db, subject, dates):
        type(self)._thread_counter += 1
        gm_thrid = f"thr{type(self)._thread_counter}"
        tid = cache._hash_message_id(gm_thrid)
        mids = [f"<{subject}-{i}@e.com>" for i in range(len(dates))]
        _save_fetched(
            {
                "message_id": mids[0],
                "from": "a@e.com",
                "subject": subject,
                "date": dates[0],
                "body": "orig",
                "thread_id": tid,
                "gm_thrid": gm_thrid,
            },
            db,
        )
        for i, d in enumerate(dates[1:], start=1):
            _save_fetched(
                {
                    "message_id": mids[i],
                    "from": "a@e.com",
                    "subject": f"Re: {subject}",
                    "date": d,
                    "body": f"reply{i}",
                    "thread_id": tid,
                    "gm_thrid": gm_thrid,
                },
                db,
            )
        return mids

    def test_replies_collapse_to_one_conversation(self, tmp_db):
        self._thread(
            tmp_db,
            "Hello",
            [
                "Mon, 01 Jan 2024 10:00:00 +0000",
                "Tue, 02 Jan 2024 10:00:00 +0000",
                "Wed, 03 Jan 2024 10:00:00 +0000",
            ],
        )
        emails, total, _ = cache.search_emails(tmp_db)
        assert total == 1
        assert len(emails) == 1
        assert emails[0]["reply_count"] == 3

    def test_latest_message_is_representative(self, tmp_db):
        mids = self._thread(
            tmp_db,
            "Hello",
            [
                "Mon, 01 Jan 2024 10:00:00 +0000",
                "Tue, 02 Jan 2024 10:00:00 +0000",
                "Wed, 03 Jan 2024 10:00:00 +0000",
            ],
        )
        emails, _, _ = cache.search_emails(tmp_db)
        assert emails[0]["message_id"] == mids[-1]

    def test_distinct_conversations_stay_separate(self, tmp_db):
        self._thread(tmp_db, "Alpha", ["Mon, 01 Jan 2024 10:00:00 +0000"])
        self._thread(tmp_db, "Beta", ["Mon, 01 Jan 2024 10:00:00 +0000"])
        _, total, _ = cache.search_emails(tmp_db)
        assert total == 2

    def test_list_view_shows_latest_while_conversation_shows_all_oldest_first(self, tmp_db):
        mids = self._thread(
            tmp_db,
            "Hello",
            [
                "Mon, 01 Jan 2024 10:00:00 +0000",  # root (oldest)
                "Tue, 02 Jan 2024 10:00:00 +0000",  # reply 1
                "Wed, 03 Jan 2024 10:00:00 +0000",  # reply 2 (newest)
            ],
        )
        emails, total, _ = cache.search_emails(tmp_db)
        assert total == 1
        assert [e["message_id"] for e in emails] == [mids[-1]]
        recent = cache.get_recent_emails(tmp_db, limit=10)
        assert [r["message_id"] for r in recent] == [mids[-1]]
        root_hash = cache._hash_message_id(mids[0])
        conv = cache.get_conversation(tmp_db, root_hash)
        assert [c["message_id"] for c in conv] == mids

    def test_get_list_count_counts_conversations(self, tmp_db):
        self._thread(
            tmp_db,
            "Alpha",
            [
                "Mon, 01 Jan 2024 10:00:00 +0000",
                "Tue, 02 Jan 2024 10:00:00 +0000",
            ],
        )
        self._thread(tmp_db, "Beta", ["Mon, 01 Jan 2024 10:00:00 +0000"])
        assert cache.get_total_count(tmp_db) == 3
        assert cache.get_list_count(tmp_db) == 2

    def test_checked_can_never_exceed_total(self, tmp_db):
        self._thread(
            tmp_db,
            "Hello",
            [
                "Mon, 01 Jan 2024 10:00:00 +0000",
                "Tue, 02 Jan 2024 10:00:00 +0000",
            ],
        )
        with cache._connect(tmp_db) as conn:
            conn.execute("UPDATE emails SET status = 'checked', category = '7'")
        counts = cache.get_counts(tmp_db)
        total = cache.get_list_count(tmp_db)
        checked = counts["checked"]
        assert checked <= total
        assert checked == total

    def test_recent_emails_one_per_conversation(self, tmp_db):
        self._thread(
            tmp_db,
            "Hello",
            [
                "Mon, 01 Jan 2024 10:00:00 +0000",
                "Tue, 02 Jan 2024 10:00:00 +0000",
            ],
        )
        recent = cache.get_recent_emails(tmp_db, limit=10)
        assert len(recent) == 1
        assert recent[0]["reply_count"] == 2

    def test_same_subject_different_senders_stay_separate(self, tmp_db):
        _save_fetched(
            {
                "message_id": "<a@e.com>",
                "from": "bank-a@e.com",
                "subject": "Verification code",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "code 1111",
            },
            tmp_db,
        )
        _save_fetched(
            {
                "message_id": "<b@e.com>",
                "from": "bank-b@e.com",
                "subject": "Verification code",
                "date": "Tue, 02 Jan 2024 10:00:00 +0000",
                "body": "code 2222",
            },
            tmp_db,
        )
        _, total, _ = cache.search_emails(tmp_db)
        assert total == 2

    def test_transitive_chain_collapses(self, tmp_db):
        gm_thrid = "chain1"
        tid = cache._hash_message_id(gm_thrid)
        _save_fetched(
            {
                "message_id": "<a@e.com>",
                "from": "alice@e.com",
                "subject": "Root",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "a",
                "thread_id": tid,
                "gm_thrid": gm_thrid,
            },
            tmp_db,
        )
        _save_fetched(
            {
                "message_id": "<b@e.com>",
                "from": "bob@e.com",
                "subject": "Re: Root",
                "date": "Tue, 02 Jan 2024 10:00:00 +0000",
                "body": "b",
                "in_reply_to": "<a@e.com>",
                "thread_id": tid,
                "gm_thrid": gm_thrid,
            },
            tmp_db,
        )
        _save_fetched(
            {
                "message_id": "<c@e.com>",
                "from": "carol@e.com",
                "subject": "Re: Root",
                "date": "Wed, 03 Jan 2024 10:00:00 +0000",
                "body": "c",
                "in_reply_to": "<b@e.com>",
                "thread_id": tid,
                "gm_thrid": gm_thrid,
            },
            tmp_db,
        )
        emails, total, _ = cache.search_emails(tmp_db)
        assert total == 1
        assert emails[0]["reply_count"] == 3


class TestGmThridAuthority:
    def _gm_tid(self, thrid):
        return cache._hash_message_id(thrid)

    def _save(self, db, mid, gm_thrid, date="Mon, 01 Jan 2024 10:00:00 +0000", subject="Topic"):
        _save_fetched(
            {
                "message_id": mid,
                "from": "a@e.com",
                "subject": subject,
                "date": date,
                "body": "body",
                "thread_id": self._gm_tid(gm_thrid) if gm_thrid else None,
                "gm_thrid": gm_thrid,
            },
            db,
        )

    def test_shared_gm_thrid_groups_into_one_conversation(self, tmp_db):
        self._save(tmp_db, "<a@e.com>", "1809095669921875987", date="Mon, 01 Jan 2024 10:00:00 +0000")
        self._save(
            tmp_db, "<b@e.com>", "1809095669921875987", date="Tue, 02 Jan 2024 10:00:00 +0000", subject="Re: Topic"
        )
        emails, total, _ = cache.search_emails(tmp_db)
        assert total == 1
        assert emails[0]["reply_count"] == 2

    def test_distinct_gm_thrids_stay_separate(self, tmp_db):
        self._save(tmp_db, "<a@e.com>", "111")
        self._save(tmp_db, "<b@e.com>", "222")
        _, total, _ = cache.search_emails(tmp_db)
        assert total == 2

    def test_message_without_gm_thrid_is_own_thread(self, tmp_db):
        self._save(tmp_db, "<a@e.com>", None)
        self._save(tmp_db, "<b@e.com>", None)
        _, total, _ = cache.search_emails(tmp_db)
        assert total == 2
        with cache._connect(tmp_db) as conn:
            tids = {r["thread_id"] for r in conn.execute("SELECT thread_id FROM emails")}
        assert len(tids) == 2

    def test_save_headers_batch_inserts_gm_thrid(self, tmp_db):
        gm_tid = self._gm_tid("1809095669921875987")
        cache.save_headers_batch(
            [
                {
                    "message_id": "<g@e.com>",
                    "from": "a@e.com",
                    "subject": "G",
                    "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                    "thread_id": gm_tid,
                    "gm_thrid": "1809095669921875987",
                }
            ],
            tmp_db,
        )
        d = cache.get_email_by_hash(tmp_db, cache._hash_message_id("<g@e.com>"))
        assert d["thread_id"] == gm_tid
        assert d["gm_thrid"] == "1809095669921875987"

    def test_refresh_thread_ids_persists_gm_thrid(self, tmp_db):
        _save_fetched(
            {
                "message_id": "<x@e.com>",
                "from": "a@e.com",
                "subject": "X",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "x",
            },
            tmp_db,
        )
        h = cache._hash_message_id("<x@e.com>")
        gm_tid = self._gm_tid("1809095669921875987")

        changed = cache.refresh_thread_ids([(gm_tid, "1809095669921875987", h)], tmp_db)
        assert changed == 1

        d = cache.get_email_by_hash(tmp_db, h)
        assert d["thread_id"] == gm_tid
        assert d["gm_thrid"] == "1809095669921875987"
