import hashlib
import json
import threading
from datetime import datetime, timezone
from email import message_from_string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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
        text = 'before <style>body{color:red}</style> after'
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
        msg = message_from_string(
            "References: <ref1@mail.com> <ref2@mail.com>\nIn-Reply-To: <reply@mail.com>\n\n"
        )
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
        envelope = b'UID 12345 (BODY[])'
        result = email_reader._extract_uid(envelope.decode())
        assert result == b"12345"

    def test_returns_none_for_no_match(self):
        result = email_reader._extract_uid("no uid here")
        assert result is None


class TestParseSince:
    def test_today_returns_formatted_date(self):
        result = email_reader.parse_since("today")
        now = datetime.now(timezone.utc)
        expected = now.strftime("%d-%b-%Y")
        assert result == expected

    def test_yesterday_returns_formatted_date(self):
        result = email_reader.parse_since("yesterday")
        assert result is not None

    def test_valid_date_string(self):
        result = email_reader.parse_since("2024-01-15")
        assert result == "15-Jan-2024"

    def test_invalid_returns_none(self):
        assert email_reader.parse_since("not-a-date") is None


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
    def test_loads_valid_json(self, tmp_path):
        kw_file = tmp_path / "keywords.json"
        kw_file.write_text(json.dumps({"categories": {"5": ["test"]}}))
        result = email_reader.load_keywords(str(kw_file))
        assert "5" in result
        assert "test" in result["5"]

    def test_returns_empty_for_missing_file(self):
        result = email_reader.load_keywords("/nonexistent/path/keywords.json")
        assert result == {}

    def test_returns_empty_for_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{")
        result = email_reader.load_keywords(str(bad_file))
        assert result == {}


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


class TestSharedCounter:
    def test_adds_correctly(self):
        counter = email_reader._SharedCounter()
        assert counter.add(1) == 1
        assert counter.add(2) == 3
        assert counter.value == 3

    def test_thread_safety(self):
        counter = email_reader._SharedCounter()
        threads = []
        for _ in range(10):
            t = threading.Thread(target=lambda: counter.add(100))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert counter.value == 1000



