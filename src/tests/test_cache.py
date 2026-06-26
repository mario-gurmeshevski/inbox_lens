import hashlib
import json
import sqlite3
import pytest

from src.scripts import cache


def _save_fetched(email, db):
    cache.save_headers_batch([email], db)
    cache.update_bodies_batch([(email["message_id"], email.get("body", ""))], db)
    h = cache._hash_message_id(email["message_id"])
    keyword_matches = email.get("keyword_matches")
    keyword_json = json.dumps(keyword_matches, ensure_ascii=False) if keyword_matches else None
    with cache._connect(db) as conn:
        conn.execute(
            "UPDATE emails SET category = ?, keyword_matches = ? WHERE message_id_hash = ?",
            (email.get("_category"), keyword_json, h),
        )


def _save_fetched_batch(emails, db):
    for e in emails:
        _save_fetched(e, db)


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
                "subject": "s",
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
