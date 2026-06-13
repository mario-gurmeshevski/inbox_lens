import hashlib
import json
import sqlite3

from src.scripts import cache


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


class TestSaveEmail:
    def test_inserts_new_email_returns_true(self, tmp_db, sample_email):
        result = cache.save_email(sample_email, tmp_db)
        assert result is True

    def test_rejects_empty_message_id_returns_false(self, tmp_db):
        result = cache.save_email({"message_id": ""}, tmp_db)
        assert result is False

    def test_duplicate_insert_returns_false(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        result = cache.save_email(sample_email, tmp_db)
        assert result is False

    def test_stores_all_fields_correctly(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM emails WHERE message_id_hash = ?",
                               (cache._hash_message_id(sample_email["message_id"]),)).fetchone()
        assert row["message_id"] == sample_email["message_id"]
        assert row["sender"] == sample_email["from"]
        assert row["subject"] == sample_email["subject"]
        assert row["body"] == sample_email["body"]
        assert row["category"] == "8"
        assert row["thread_id"] == "abc123def456"

    def test_updates_keyword_matches_on_existing(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        sample_email["keyword_matches"] = {"10": ["итно"]}
        sample_email["_category"] = "10"
        cache.save_email(sample_email, tmp_db)
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM emails WHERE message_id_hash = ?",
                               (cache._hash_message_id(sample_email["message_id"]),)).fetchone()
        assert json.loads(row["keyword_matches"]) == {"10": ["итно"]}
        assert row["category"] == "10"


class TestBatchExistingHashes:
    def test_returns_set_of_existing_hashes(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
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


class TestSaveEmailsBatch:
    def test_inserts_multiple_new_emails(self, tmp_db, sample_emails_batch):
        count = cache.save_emails_batch(sample_emails_batch, tmp_db)
        assert count == 5

    def test_returns_count_of_new_inserts(self, tmp_db, sample_emails_batch):
        count = cache.save_emails_batch(sample_emails_batch, tmp_db)
        assert count == 5
        count2 = cache.save_emails_batch(sample_emails_batch, tmp_db)
        assert count2 == 0

    def test_skips_existing_hashes(self, tmp_db, sample_emails_batch):
        cache.save_emails_batch(sample_emails_batch, tmp_db)
        count = cache.save_emails_batch(sample_emails_batch, tmp_db)
        assert count == 0

    def test_updates_keyword_matches_for_existing(self, tmp_db, sample_emails_batch):
        cache.save_emails_batch(sample_emails_batch, tmp_db)
        for e in sample_emails_batch:
            e["keyword_matches"] = {"10": ["итно"]}
            e["_category"] = "10"
        cache.save_emails_batch(sample_emails_batch, tmp_db)
        h = cache._hash_message_id(sample_emails_batch[0]["message_id"])
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT * FROM emails WHERE message_id_hash = ?", (h,)).fetchone()
        assert row["category"] == "10"

    def test_empty_list_returns_zero(self, tmp_db):
        assert cache.save_emails_batch([], tmp_db) == 0

    def test_emails_without_message_id_are_skipped(self, tmp_db):
        count = cache.save_emails_batch([{"from": "a@b.com"}], tmp_db)
        assert count == 0


class TestReadEmails:
    def test_returns_all_emails_ordered_by_date_desc(self, tmp_db, sample_emails_batch):
        cache.save_emails_batch(sample_emails_batch, tmp_db)
        emails = cache.read_emails(tmp_db)
        assert len(emails) == 5

    def test_respects_max_emails_limit(self, tmp_db, sample_emails_batch):
        cache.save_emails_batch(sample_emails_batch, tmp_db)
        emails = cache.read_emails(tmp_db, max_emails=2)
        assert len(emails) == 2

    def test_filters_by_since_date(self, tmp_db, sample_email):
        sample_email["date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
        cache.save_email(sample_email, tmp_db)
        emails = cache.read_emails(tmp_db, since_date="01-Jan-2024")
        assert len(emails) >= 1
        emails_future = cache.read_emails(tmp_db, since_date="01-Jan-2030")
        assert len(emails_future) == 0

    def test_invalid_since_date_ignored(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        emails = cache.read_emails(tmp_db, since_date="invalid-date")
        assert len(emails) == 1

    def test_returns_dicts_with_correct_keys(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
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
        cache.save_email(sample_email, tmp_db)
        result = cache.delete_email(sample_email["message_id"], tmp_db)
        assert result is True

    def test_nonexistent_email_returns_false(self, tmp_db):
        result = cache.delete_email("<nonexistent@example.com>", tmp_db)
        assert result is False


class TestScanKeywords:
    def test_finds_matching_keywords(self, compiled_patterns):
        text = "ова е итно проблем што треба да се реши веднаш"
        result = cache._scan_keywords(text, compiled_patterns)
        assert "10" in result
        assert "итно" in result["10"]
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
            {"message_id": "<s1@example.com>", "subject": "итно!", "body": "resolve this"},
            {"message_id": "<s2@example.com>", "subject": "hello", "body": "nothing here"},
        ]
        result = cache.scan_and_update(emails, tmp_db, compiled_patterns)
        assert result["scanned"] == 2
        assert result["total"] == 2
        assert result["emails_with_matches"]

    def test_skips_already_checked_with_matches(self, tmp_db, compiled_patterns):
        email = {"message_id": "<pre@example.com>", "subject": "итно", "body": "test"}
        h = cache._hash_message_id(email["message_id"])
        kw_json = json.dumps({"10": ["итно"]})
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
                (h, email["message_id"], "", "test", "", ),
            )
        result = cache.scan_and_update([email], tmp_db, compiled_patterns)
        assert result["skipped_no_body"] == 1

    def test_returns_correct_stats(self, tmp_db, compiled_patterns):
        emails = [
            {"message_id": f"<x{i}@example.com>", "subject": "проблем", "body": "test"}
            for i in range(3)
        ]
        result = cache.scan_and_update(emails, tmp_db, compiled_patterns)
        assert result["total"] == 3
        assert result["scanned"] == 3

    def test_emails_without_message_id_handled(self, tmp_db, compiled_patterns):
        emails = [{"subject": "test", "body": "итно"}]
        result = cache.scan_and_update(emails, tmp_db, compiled_patterns)
        assert result["total"] == 1


class TestGetEmailByHash:
    def test_returns_email_dict_for_existing(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        result = cache.get_email_by_hash(tmp_db, h)
        assert result is not None
        assert result["message_id"] == sample_email["message_id"]
        assert result["_file_hash"] == h
        assert result["_category"] == "8"

    def test_returns_none_for_nonexistent(self, tmp_db):
        assert cache.get_email_by_hash(tmp_db, "nonexistent") is None


class TestListOrderedEmails:
    def _seed_checked(self, tmp_db):
        emails = [
            {"message_id": f"<o{i}@example.com>", "from": f"s{i}@e.com", "subject": f"Sub {i}",
             "date": f"Mon, 0{i+1} Jan 2024 10:00:00 +0000", "body": "body"}
            for i in range(4)
        ]
        categories = ["10", "7", "7", "3"]
        for e, cat in zip(emails, categories):
            e["_category"] = cat
            e["keyword_matches"] = {cat: ["word"]}
        cache.save_emails_batch(emails, tmp_db)
        h_list = [cache._hash_message_id(e["message_id"]) for e in emails]
        with cache._connect(tmp_db) as conn:
            conn.executemany(
                "UPDATE emails SET status = 'checked', keyword_matches = ?, category = ? WHERE message_id_hash = ?",
                [(json.dumps(e.get("keyword_matches")), e.get("_category"), h) for e, h in zip(emails, h_list)],
            )

    def test_returns_checked_emails_sorted(self, tmp_db):
        self._seed_checked(tmp_db)
        results = cache.list_ordered_emails(tmp_db)
        assert len(results) == 4
        cats = [r["_category"] for r in results]
        assert cats == sorted(cats, key=lambda x: int(x) if x.isdigit() else 0, reverse=True)

    def test_filters_by_priority_level(self, tmp_db):
        self._seed_checked(tmp_db)
        results = cache.list_ordered_emails(tmp_db, priority_level="7")
        assert all(r["_category"] == "7" for r in results)

    def test_returns_empty_when_no_checked_emails(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        assert cache.list_ordered_emails(tmp_db) == []


class TestGetOrderedLevels:
    def test_returns_distinct_categories_sorted_desc(self, tmp_db):
        emails = [
            {"message_id": f"<l{i}@e.com>", "subject": "s", "date": "Mon, 01 Jan 2024 00:00:00 +0000", "body": "b", "_category": cat}
            for i, cat in enumerate(["3", "10", "7"])
        ]
        cache.save_emails_batch(emails, tmp_db)
        hashes = [cache._hash_message_id(e["message_id"]) for e in emails]
        with cache._connect(tmp_db) as conn:
            conn.executemany(
                "UPDATE emails SET status = 'checked' WHERE message_id_hash = ?",
                [(h,) for h in hashes],
            )
        levels = cache.get_ordered_levels(tmp_db)
        assert levels == ["10", "7", "3"]

    def test_returns_empty_list_when_no_checked(self, tmp_db):
        assert cache.get_ordered_levels(tmp_db) == []


class TestGetPriorityCounts:
    def test_returns_category_counts(self, tmp_db):
        for i in range(3):
            e = {"message_id": f"<pc{i}@e.com>", "subject": "s", "date": "Mon, 01 Jan 2024 00:00:00 +0000", "body": "b", "_category": "7"}
            cache.save_email(e, tmp_db)
        with cache._connect(tmp_db) as conn:
            conn.execute("UPDATE emails SET status = 'checked', category = '7'")
        counts = cache.get_priority_counts(tmp_db)
        assert counts.get("7") == 3

    def test_excludes_unchecked(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        counts = cache.get_priority_counts(tmp_db)
        assert counts == {}


class TestGetCounts:
    def test_returns_status_breakdown(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        counts = cache.get_counts(tmp_db)
        assert counts["fetched"] == 1
        assert counts["checked"] == 0

    def test_defaults_missing_statuses_to_zero(self, tmp_db):
        counts = cache.get_counts(tmp_db)
        assert counts["headers_only"] == 0
        assert counts["fetched"] == 0
        assert counts["checked"] == 0


class TestCountUnscanned:
    def test_counts_non_checked_emails(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        assert cache.count_unscanned(tmp_db) == 1

    def test_filters_by_since_date(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        assert cache.count_unscanned(tmp_db, since_date="01-Jan-2024") >= 1
        assert cache.count_unscanned(tmp_db, since_date="01-Jan-2030") == 0

    def test_invalid_date_returns_all_unscanned(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        assert cache.count_unscanned(tmp_db, since_date="bad-date") == 1


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
        cache.save_email(sample_email, tmp_db)
        updated = cache.update_bodies_batch(
            [(sample_email["message_id"], "new body")], tmp_db
        )
        assert updated == 1

    def test_empty_list_returns_zero(self, tmp_db):
        assert cache.update_bodies_batch([], tmp_db) == 0


class TestGetHeadersOnlyMessageIds:
    def test_returns_message_ids_with_headers_only_status(self, tmp_db, sample_headers_batch):
        cache.save_headers_batch(sample_headers_batch, tmp_db)
        result = cache.get_headers_only_message_ids(tmp_db)
        assert len(result) == 3

    def test_excludes_other_statuses(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        result = cache.get_headers_only_message_ids(tmp_db)
        assert len(result) == 0


class TestUpdateEmailBody:
    def test_updates_body_and_status(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        result = cache.update_email_body(sample_email["message_id"], "new body", tmp_db)
        assert result is True
        h = cache._hash_message_id(sample_email["message_id"])
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT body, status FROM emails WHERE message_id_hash = ?", (h,)).fetchone()
        assert row["body"] == "new body"
        assert row["status"] == "fetched"

    def test_nonexistent_returns_false(self, tmp_db):
        result = cache.update_email_body("<nope@example.com>", "body", tmp_db)
        assert result is False


class TestGetRecentEmails:
    def test_returns_recent_emails_limited(self, tmp_db):
        emails = [
            {"message_id": f"<r{i}@e.com>", "from": "s@e.com", "subject": f"Sub {i}",
             "date": f"Mon, 0{i+1} Jan 2024 10:00:00 +0000", "body": "b"}
            for i in range(10)
        ]
        cache.save_emails_batch(emails, tmp_db)
        result = cache.get_recent_emails(tmp_db, limit=3)
        assert len(result) == 3

    def test_includes_keyword_matches(self, tmp_db, sample_email):
        cache.save_email(sample_email, tmp_db)
        h = cache._hash_message_id(sample_email["message_id"])
        with cache._connect(tmp_db) as conn:
            conn.execute("UPDATE emails SET status = 'checked' WHERE message_id_hash = ?", (h,))
        result = cache.get_recent_emails(tmp_db, limit=1)
        assert "keyword_matches" in result[0]


class TestSearchEmails:
    def _seed_search_data(self, tmp_db):
        emails = [
            {"message_id": f"<se{i}@e.com>", "from": f"sender{i}@e.com",
             "subject": f"Subject about topic{i}", "date": f"Mon, 0{i+1} Jan 2024 10:00:00 +0000",
             "body": "body", "_category": "7" if i % 2 == 0 else "3"}
            for i in range(10)
        ]
        cache.save_emails_batch(emails, tmp_db)
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
        cache.save_email(sample_email, tmp_db)
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
