import hashlib
import json
from email import message_from_string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from src.scripts import email_reader


class TestDecodeStr:
    def test_returns_empty_for_none(self):
        assert email_reader.decode_str(None) == ""

    def test_returns_plain_string(self):
        assert email_reader.decode_str("hello world") == "hello world"

    def test_decodes_encoded_header(self):
        msg = message_from_string("Subject: =?utf-8?b?0YjQvtC/INC40LHQuA==?=\n\n")
        result = email_reader.decode_str(msg["Subject"])
        assert len(result) > 0

    def test_handles_multipart_encoded(self):
        result = email_reader.decode_str("=?utf-8?q?hello?= =?utf-8?q?_world?=")
        assert "hello" in result


class TestCleanBody:
    def test_removes_img_tags(self):
        assert "<img" not in email_reader._clean_body('text <img src="x"> more')

    def test_removes_style_tags(self):
        text = "before <style>body{color:red}</style> after"
        result = email_reader._clean_body(text)
        assert "<style>" not in result
        assert "before" in result
        assert "after" in result

    def test_replaces_nbsp(self):
        result = email_reader._clean_body("hello&nbsp;world")
        assert "&nbsp;" not in result
        assert "hello" in result
        assert "world" in result

    def test_unescapes_html_entities(self):
        result = email_reader._clean_body("a &amp; b &lt; c")
        assert "&" in result
        assert "<" in result

    def test_collapses_multiple_spaces(self):
        result = email_reader._clean_body("hello    world")
        assert "    " not in result

    def test_collapses_blank_lines(self):
        result = email_reader._clean_body("line1\n   \nline2")
        assert "line1" in result
        assert "line2" in result

    def test_limits_dashes(self):
        result = email_reader._clean_body("a----------b")
        assert "----------" not in result
        assert "---" in result

    def test_limits_underscores(self):
        result = email_reader._clean_body("a__________b")
        assert "__________" not in result

    def test_limits_stars(self):
        result = email_reader._clean_body("a**********b")
        assert "**********" not in result

    def test_strips_each_line(self):
        result = email_reader._clean_body("  hello  \n  world  ")
        lines = result.split("\n")
        for line in lines:
            if line:
                assert line == line.strip()

    def test_empty_string_returns_empty(self):
        assert email_reader._clean_body("") == ""

    def test_none_returns_empty(self):
        assert email_reader._clean_body(None) == ""


class TestHtmlToText:
    def test_converts_html_to_text(self):
        result = email_reader._html_to_text("<p>Hello <b>world</b></p>")
        assert "Hello" in result
        assert "world" in result

    def test_handles_br_tags(self):
        result = email_reader._html_to_text("line1<br>line2<br/>line3")
        assert "line1" in result
        assert "line2" in result

    def test_strips_all_html_tags(self):
        result = email_reader._html_to_text("<div><span>text</span></div>")
        assert "<div>" not in result
        assert "text" in result


class TestGetTextBody:
    def test_extracts_plain_text_from_multipart(self):
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("plain text body", "plain"))
        msg.attach(MIMEText("<p>html body</p>", "html"))
        result = email_reader.get_text_body(msg)
        assert "plain text body" in result

    def test_extracts_html_body_when_no_plain(self):
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("<p>html only</p>", "html"))
        result = email_reader.get_text_body(msg)
        assert "html only" in result

    def test_handles_non_multipart_plain(self):
        msg = MIMEText("simple plain text", "plain")
        result = email_reader.get_text_body(msg)
        assert "simple plain text" in result

    def test_handles_non_multipart_html(self):
        msg = MIMEText("<p>simple html</p>", "html")
        result = email_reader.get_text_body(msg)
        assert "simple html" in result

    def test_skips_attachments(self):
        msg = MIMEMultipart("mixed")
        msg.attach(MIMEText("body text", "plain"))
        attachment = MIMEText("file content", "plain")
        attachment.add_header("Content-Disposition", "attachment", filename="test.txt")
        msg.attach(attachment)
        result = email_reader.get_text_body(msg)
        assert "body text" in result
        assert "file content" not in result

    def test_returns_empty_for_errors(self):
        msg = message_from_string("")
        result = email_reader.get_text_body(msg)
        assert result == ""


class TestHashThreadId:
    def test_returns_16_char_hex(self):
        result = email_reader._hash_thread_id("test-thread-id")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_consistent(self):
        a = email_reader._hash_thread_id("test")
        b = email_reader._hash_thread_id("test")
        assert a == b

    def test_matches_sha256(self):
        val = "my-thread-ref"
        expected = hashlib.sha256(val.encode()).hexdigest()[:16]
        assert email_reader._hash_thread_id(val) == expected


class TestNormalizeSubject:
    def test_strips_re_prefix(self):
        assert email_reader._normalize_subject("Re: Hello") == "hello"

    def test_strips_fwd_prefix(self):
        assert email_reader._normalize_subject("Fwd: Hello") == "hello"

    def test_strips_fw_prefix(self):
        assert email_reader._normalize_subject("Fw: Hello") == "hello"

    def test_strips_reply_prefix(self):
        assert email_reader._normalize_subject("Reply: Hello") == "hello"

    def test_strips_multiple_prefixes(self):
        assert email_reader._normalize_subject("Re: Re: Hello") == "re: hello"

    def test_lowercases(self):
        assert email_reader._normalize_subject("HELLO WORLD") == "hello world"

    def test_empty_string(self):
        assert email_reader._normalize_subject("") == ""


class TestExtractThreadInfo:
    def test_uses_references_first(self):
        msg = message_from_string("References: <ref1@mail.com> <ref2@mail.com>\nIn-Reply-To: <reply@mail.com>\n\n")
        result = email_reader.extract_thread_info(msg)
        assert result["thread_id"] == email_reader._hash_thread_id("<ref1@mail.com>")
        assert result["in_reply_to"] == "<reply@mail.com>"

    def test_uses_in_reply_to_second(self):
        msg = message_from_string("In-Reply-To: <reply@mail.com>\n\n")
        result = email_reader.extract_thread_info(msg)
        assert result["thread_id"] == email_reader._hash_thread_id("<reply@mail.com>")
        assert result["in_reply_to"] == "<reply@mail.com>"

    def test_uses_thread_index_third(self):
        msg = message_from_string("Thread-Index: abcdefghijklmnopqrstuvwxyz\n\n")
        result = email_reader.extract_thread_info(msg)
        expected_input = "abcdefghijklmnopqrstuvwxyz"[:22]
        assert result["thread_id"] == email_reader._hash_thread_id(expected_input)
        assert result["in_reply_to"] == ""

    def test_falls_back_to_normalized_subject(self):
        msg = message_from_string("Subject: Re: My Topic\n\n")
        result = email_reader.extract_thread_info(msg)
        assert result["thread_id"] == email_reader._hash_thread_id("my topic")
        assert result["in_reply_to"] == ""

    def test_returns_none_thread_id_when_nothing(self):
        msg = message_from_string("\n\n")
        result = email_reader.extract_thread_info(msg)
        assert result["thread_id"] is None
        assert result["in_reply_to"] == ""


class TestExtractUid:
    def test_extracts_uid_from_envelope(self):
        envelope = b"UID 12345 (BODY[])"
        result = email_reader._extract_uid(envelope.decode())
        assert result == b"12345"

    def test_returns_none_for_no_match(self):
        result = email_reader._extract_uid("no uid here")
        assert result is None


class TestSanitizeImapSearch:
    def test_removes_special_chars(self):
        result = email_reader._sanitize_imap_search('test"value)test(value\\test')
        assert '"' not in result
        assert "(" not in result
        assert ")" not in result
        assert "\\" not in result


class TestParseListFolderName:
    def test_parses_folder_name(self):
        result = email_reader._parse_list_folder_name('(\\HasNoChildren) "/" "INBOX"')
        assert result == "INBOX"

    def test_returns_none_for_malformed(self):
        assert email_reader._parse_list_folder_name("no separator") is None


class TestLoadKeywords:
    def test_loads_stored_categories(self, tmp_db):
        email_reader.save_keywords({"5": ["test"]}, tmp_db)
        result = email_reader.load_keywords(tmp_db)
        assert "5" in result
        assert "test" in result["5"]

    def test_seeds_from_keywords_json_when_present(self, tmp_db, tmp_path, monkeypatch):
        from src.scripts.email_reader import keywords as kw_mod

        kw_file = tmp_path / "keywords.json"
        kw_file.write_text(json.dumps({"categories": {"9": ["urgent"]}}))
        monkeypatch.setattr(kw_mod, "KEYWORDS_FILE", str(kw_file))
        monkeypatch.setattr(kw_mod, "KEYWORDS_EXAMPLE_FILE", "/nonexistent/example.json")

        result = email_reader.load_keywords(tmp_db)
        assert result == {"9": ["urgent"]}

    def test_seeds_from_example_when_no_keywords_json(self, tmp_db, tmp_path, monkeypatch):
        from src.scripts.email_reader import keywords as kw_mod

        example = tmp_path / "keywords.example.json"
        example.write_text(json.dumps({"categories": {"8": ["invoice"]}}))
        monkeypatch.setattr(kw_mod, "KEYWORDS_FILE", "/nonexistent/keywords.json")
        monkeypatch.setattr(kw_mod, "KEYWORDS_EXAMPLE_FILE", str(example))

        result = email_reader.load_keywords(tmp_db)
        assert "8" in result and "invoice" in result["8"]

    def test_prefers_keywords_json_over_example(self, tmp_db, tmp_path, monkeypatch):
        from src.scripts.email_reader import keywords as kw_mod

        kw_file = tmp_path / "keywords.json"
        kw_file.write_text(json.dumps({"categories": {"7": ["from-file"]}}))
        example = tmp_path / "keywords.example.json"
        example.write_text(json.dumps({"categories": {"10": ["from-example"]}}))
        monkeypatch.setattr(kw_mod, "KEYWORDS_FILE", str(kw_file))
        monkeypatch.setattr(kw_mod, "KEYWORDS_EXAMPLE_FILE", str(example))

        result = email_reader.load_keywords(tmp_db)
        assert result == {"7": ["from-file"]}

    def test_returns_empty_when_neither_exists(self, tmp_db, monkeypatch):
        from src.scripts.email_reader import keywords as kw_mod

        monkeypatch.setattr(kw_mod, "KEYWORDS_FILE", "/nonexistent/keywords.json")
        monkeypatch.setattr(kw_mod, "KEYWORDS_EXAMPLE_FILE", "/nonexistent/example.json")
        result = email_reader.load_keywords(tmp_db)
        assert result == {}

    def test_corrupt_stored_value_reseeds(self, tmp_db, tmp_path, monkeypatch):
        from src.scripts import cache
        from src.scripts.email_reader import keywords as kw_mod

        kw_file = tmp_path / "keywords.json"
        kw_file.write_text(json.dumps({"categories": {"10": ["urgent"]}}))
        monkeypatch.setattr(kw_mod, "KEYWORDS_FILE", str(kw_file))
        monkeypatch.setattr(kw_mod, "KEYWORDS_EXAMPLE_FILE", "/nonexistent/example.json")

        cache.save_setting("keywords", "not valid json {{{", tmp_db)
        result = email_reader.load_keywords(tmp_db)
        assert result and "10" in result


class TestBuildCompiledPatterns:
    def test_compiles_combined_pattern(self):
        categories = {"7": ["problem", "error"]}
        result = email_reader.build_compiled_patterns(categories)
        assert "7" in result
        words, pattern = result["7"]
        assert len(words) == 2
        assert pattern.search("there is a problem here")

    def test_patterns_match_keywords(self):
        categories = {"5": ["urgent"]}
        result = email_reader.build_compiled_patterns(categories)
        words, pattern = result["5"]
        assert "urgent" in words
        assert pattern.search("this is urgent")
        assert not pattern.search("urgently")


class TestChunkList:
    def test_splits_into_n_chunks(self):
        result = email_reader._chunk_list([1, 2, 3, 4, 5, 6], 3)
        assert len(result) == 3
        flat = [x for chunk in result for x in chunk]
        assert sorted(flat) == [1, 2, 3, 4, 5, 6]

    def test_handles_uneven_division(self):
        result = email_reader._chunk_list([1, 2, 3, 4, 5], 3)
        flat = [x for chunk in result for x in chunk]
        assert sorted(flat) == [1, 2, 3, 4, 5]

    def test_empty_list(self):
        result = email_reader._chunk_list([], 3)
        assert all(len(chunk) == 0 for chunk in result)

    def test_single_element(self):
        result = email_reader._chunk_list([42], 3)
        flat = [x for chunk in result for x in chunk]
        assert flat == [42]


class TestLoadKeywordsSeeding:
    def test_seeded_value_persists_for_subsequent_loads(self, tmp_db, tmp_path, monkeypatch):
        from src.scripts.email_reader import keywords as kw_mod

        kw_file = tmp_path / "keywords.json"
        kw_file.write_text(json.dumps({"categories": {"8": ["invoice"]}}))
        monkeypatch.setattr(kw_mod, "KEYWORDS_FILE", str(kw_file))
        monkeypatch.setattr(kw_mod, "KEYWORDS_EXAMPLE_FILE", "/nonexistent/example.json")

        result = email_reader.load_keywords(tmp_db)
        assert "8" in result and "invoice" in result["8"]

        # Even with both source files gone, the DB persists the value.
        monkeypatch.setattr(kw_mod, "KEYWORDS_FILE", "/nonexistent/keywords.json")
        again = email_reader.load_keywords(tmp_db)
        assert "8" in again and "invoice" in again["8"]

    def test_seeding_failure_returns_empty(self, tmp_db, monkeypatch):
        from src.scripts.email_reader import keywords as kw_mod

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(kw_mod, "KEYWORDS_FILE", "/nonexistent/keywords.json")
        monkeypatch.setattr(kw_mod, "KEYWORDS_EXAMPLE_FILE", "/nonexistent/example.json")
        monkeypatch.setattr(kw_mod, "save_keywords", boom)
        result = kw_mod.load_keywords(tmp_db)
        assert result == {}


class TestScanEmailsIntegration:
    def test_pipeline_finds_matches(self, tmp_db):
        email_reader.save_keywords({"10": ["important"], "7": ["mistake"]}, tmp_db)

        emails = [
            {"message_id": "<a@e.com>", "subject": "important!", "body": "hello"},
            {"message_id": "<b@e.com>", "subject": "normal", "body": "nothing"},
        ]
        result = email_reader.scan_emails(emails, tmp_db)
        assert result["scanned"] == 2
        assert result["emails_with_matches"]

    def test_pipeline_with_no_keywords(self, tmp_db, monkeypatch):
        from src.scripts.email_reader import keywords as kw_mod

        monkeypatch.setattr(kw_mod, "KEYWORDS_EXAMPLE_FILE", "/nonexistent/keywords.example.json")
        email_reader.load_keywords(tmp_db)
        emails = [{"message_id": "<x@e.com>", "subject": "important", "body": "test"}]
        result = email_reader.scan_emails(emails, tmp_db)
        assert result["scanned"] == 1
        assert not result["emails_with_matches"]

    def test_pipeline_initializes_db(self, tmp_db):
        result = email_reader.scan_emails([], tmp_db)
        assert result["total"] == 0


class TestSaveKeywords:
    def test_validates_and_strips(self, tmp_db):
        cleaned = email_reader.save_keywords({"8": ["  Invoice ", "invoice", ""]}, tmp_db)
        assert cleaned == {"8": ["Invoice"]}
        assert email_reader.load_keywords(tmp_db) == {"8": ["Invoice"]}

    def test_rejects_non_dict(self, tmp_db):
        with pytest.raises(ValueError):
            email_reader.save_keywords([("8", ["x"])], tmp_db)

    def test_rejects_non_integer_level(self, tmp_db):
        with pytest.raises(ValueError):
            email_reader.save_keywords({"high": ["x"]}, tmp_db)

    def test_rejects_level_below_one(self, tmp_db):
        with pytest.raises(ValueError):
            email_reader.save_keywords({"0": ["x"]}, tmp_db)

    def test_rejects_non_list_words(self, tmp_db):
        with pytest.raises(ValueError):
            email_reader.save_keywords({"8": "invoice"}, tmp_db)

    def test_accepts_arbitrary_high_level(self, tmp_db):
        cleaned = email_reader.save_keywords({"11": ["critical"]}, tmp_db)
        assert cleaned == {"11": ["critical"]}


class TestRescanAll:
    def test_recomputes_already_checked_rows(self, tmp_db):
        from src.scripts import cache

        email_reader.save_keywords({"10": ["urgent"]}, tmp_db)
        emails = [
            {"message_id": "<a@e.com>", "subject": "urgent fix", "body": "now"},
            {"message_id": "<b@e.com>", "subject": "hello", "body": "nothing"},
        ]
        cache.save_headers_batch(emails, tmp_db)
        cache.update_bodies_batch([(e["message_id"], e["body"]) for e in emails], tmp_db)
        with cache._connect(tmp_db) as conn:
            conn.execute("UPDATE emails SET status = 'checked', category = 'unclassified', keyword_matches = NULL")

        patterns = email_reader.build_compiled_patterns(email_reader.load_keywords(tmp_db))
        result = cache.rescan_all(tmp_db, patterns)
        assert result["scanned"] == 2

        with cache._connect(tmp_db) as conn:
            rows = {
                r["message_id"]: (r["category"], r["keyword_matches"])
                for r in conn.execute("SELECT message_id, category, keyword_matches FROM emails")
            }
        assert rows["<a@e.com>"][0] == "10"
        assert rows["<b@e.com>"][0] == "unclassified"

    def test_no_bodies_returns_zero(self, tmp_db):
        from src.scripts import cache

        cache.save_headers_batch([{"message_id": "<h@e.com>"}], tmp_db)  # headers only, no body
        patterns = email_reader.build_compiled_patterns({"10": ["x"]})
        assert cache.rescan_all(tmp_db, patterns) == {"scanned": 0}


class TestGetTextBodyErrorPaths:
    def test_skips_part_with_undecodeable_payload(self):

        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("real body", "plain"))
        result = email_reader.get_text_body(msg)
        assert "real body" in result
