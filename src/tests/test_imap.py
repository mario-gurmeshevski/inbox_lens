import imaplib
from unittest.mock import MagicMock, patch

import pytest

from src.scripts import cache, email_reader
from src.scripts.email_reader import imap as imap_mod
from src.tests._helpers import _raise, fake_imap_session


class TestSafeClose:
    def test_calls_close_and_logout(self, fake_mail):
        email_reader._safe_close(fake_mail)
        fake_mail.close.assert_called_once()
        fake_mail.logout.assert_called_once()

    def test_swallows_close_exception(self, fake_mail):
        fake_mail.close.side_effect = RuntimeError("boom")
        email_reader._safe_close(fake_mail)
        fake_mail.logout.assert_called_once()

    def test_swallows_logout_exception(self, fake_mail):
        fake_mail.logout.side_effect = RuntimeError("boom")
        email_reader._safe_close(fake_mail)
        fake_mail.close.assert_called_once()

    def test_swallows_both_exceptions(self, fake_mail):
        fake_mail.close.side_effect = RuntimeError("close")
        fake_mail.logout.side_effect = RuntimeError("logout")
        email_reader._safe_close(fake_mail)


class TestParseFetchedEmail:
    def _make_raw(self, subject="Hello", from_addr="a@b.com", body_text="body text"):
        raw = (
            f"From: {from_addr}\r\n"
            f"Subject: {subject}\r\n"
            f"Date: Mon, 01 Jan 2024 10:00:00 +0000\r\n"
            f"Message-ID: <mid@e.com>\r\n"
            "\r\n"
            f"{body_text}\r\n"
        ).encode()
        return raw

    def test_parses_tuple_item(self):
        msg_data = [(b"UID 1 (BODY[])", self._make_raw())]
        result = email_reader._parse_fetched_email(msg_data)
        assert len(result) == 1
        r = result[0]
        assert r["subject"] == "Hello"
        assert r["from"] == "a@b.com"
        assert r["message_id"] == "<mid@e.com>"
        assert "body text" in r["body"]

    def test_strips_body_whitespace(self):
        raw = self._make_raw(body_text="   spaced body   ")
        result = email_reader._parse_fetched_email([(b"env", raw)])
        assert result[0]["body"] == "spaced body"

    def test_skips_non_tuple_items(self):
        msg_data = [b"standalone bytes", (b"env", self._make_raw()), "string item"]
        result = email_reader._parse_fetched_email(msg_data)
        assert len(result) == 1

    def test_empty_list_returns_empty(self):
        assert email_reader._parse_fetched_email([]) == []

    def test_extracts_thread_info(self):
        raw = b"From: a@b.com\r\nSubject: Re: Topic\r\nMessage-ID: <m@e.com>\r\nReferences: <ref@e.com>\r\n\r\nbody\r\n"
        result = email_reader._parse_fetched_email([(b"env", raw)])
        assert result[0]["in_reply_to"] == ""
        assert result[0]["thread_id"] is None


class TestGetEmailIds:
    def test_returns_split_ids_on_success(self, fake_mail):
        fake_mail.uid.return_value = ("OK", [b"1 2 3 4"])
        result = email_reader._get_email_ids(fake_mail)
        assert result == [b"1", b"2", b"3", b"4"]
        fake_mail.select.assert_called_with("INBOX")

    def test_returns_empty_on_non_ok_status(self, fake_mail):
        fake_mail.uid.return_value = ("BAD", [b""])
        assert email_reader._get_email_ids(fake_mail) == []


class TestFetchHeadersBulk:
    def test_empty_input_returns_empty(self, fake_mail):
        assert email_reader._fetch_headers_bulk(fake_mail, []) == []

    def test_returns_header_dicts(self, fake_mail):
        fake_mail.uid.return_value = (
            "OK",
            [
                (
                    b"UID 1 (BODY[HEADER.FIELDS (...)])",
                    b"Subject: Hello\r\nFrom: a@b.com\r\nDate: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
                    b"Message-ID: <m@e.com>\r\n\r\n",
                ),
            ],
        )
        result = email_reader._fetch_headers_bulk(fake_mail, [b"1"])
        assert len(result) == 1
        h = result[0]
        assert h["subject"] == "Hello"
        assert h["from"] == "a@b.com"
        assert h["message_id"] == "<m@e.com>"
        assert h["_uid"] == b"1"
        assert h["body"] == ""

    def test_parses_flags_from_envelope(self, fake_mail):
        fake_mail.uid.return_value = (
            "OK",
            [
                (
                    b"UID 1 (FLAGS (\\Seen \\Flagged)) (BODY[HEADER.FIELDS (...)])",
                    b"Subject: Starred\r\nFrom: a@b.com\r\nDate: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
                    b"Message-ID: <star@e.com>\r\n\r\n",
                ),
                (
                    b"UID 2 (FLAGS (\\Seen)) (BODY[HEADER.FIELDS (...)])",
                    b"Subject: Plain\r\nFrom: c@d.com\r\nDate: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
                    b"Message-ID: <plain@e.com>\r\n\r\n",
                ),
            ],
        )
        result = email_reader._fetch_headers_bulk(fake_mail, [b"1", b"2"])
        assert len(result) == 2
        assert result[0]["is_starred"] is True
        assert result[0]["is_read"] is True
        assert result[1]["is_starred"] is False
        assert result[1]["is_read"] is True

    def test_returns_empty_on_non_ok_status(self, fake_mail):
        fake_mail.uid.return_value = ("BAD", [])
        assert email_reader._fetch_headers_bulk(fake_mail, [b"1"]) == []

    def test_skips_non_tuple_items(self, fake_mail):
        fake_mail.uid.return_value = ("OK", [b"standalone", (b"UID 1", b"Message-ID: <m@e.com>\r\n\r\n")])
        result = email_reader._fetch_headers_bulk(fake_mail, [b"1"])
        assert len(result) == 1

    def test_retries_failed_batch_then_succeeds(self, fake_mail):
        fake_mail.uid.side_effect = [
            ("BAD", []),
            (
                "OK",
                [
                    (b"UID 1 (BODY[HEADER.FIELDS (...)])", b"Subject: Hello\r\nMessage-ID: <m@e.com>\r\n\r\n"),
                ],
            ),
        ]
        result = email_reader._fetch_headers_bulk(fake_mail, [b"1"])
        assert len(result) == 1
        assert result[0]["message_id"] == "<m@e.com>"

    def test_gives_up_after_retry_failure(self, fake_mail):
        fake_mail.uid.side_effect = [("BAD", []), ("BAD", [])]
        result = email_reader._fetch_headers_bulk(fake_mail, [b"1"])
        assert result == []

    def test_paginates_large_uid_set(self, fake_mail):
        batch_size = email_reader.FETCH_BATCH_SIZE
        num_uids = batch_size * 3 + 2
        uids = [str(i).encode() for i in range(1, num_uids + 1)]

        def fake_uid_fetch(*args):
            id_bytes = args[1]
            uid_count = id_bytes.count(b",") + 1 if b"," in id_bytes else 1
            assert uid_count <= batch_size, f"fetch batch exceeded FETCH_BATCH_SIZE: {uid_count} > {batch_size}"
            items = []
            for uid in id_bytes.split(b","):
                items.append(
                    (
                        f"UID {uid.decode()} (BODY[HEADER.FIELDS (...)])".encode(),
                        f"Message-ID: <m{uid.decode()}@e.com>\r\n\r\n".encode(),
                    )
                )
            return ("OK", items)

        fake_mail.uid.side_effect = fake_uid_fetch
        result = email_reader._fetch_headers_bulk(fake_mail, uids)
        assert len(result) == num_uids
        assert fake_mail.uid.call_count >= 4


class TestResolveUid:
    def test_returns_uid_when_found(self, fake_mail):
        fake_mail.uid.return_value = ("OK", [b"42"])
        assert email_reader._resolve_uid(fake_mail, "<msg@e.com>") == b"42"

    def test_returns_none_when_not_found(self, fake_mail):
        fake_mail.uid.return_value = ("OK", [b""])
        assert email_reader._resolve_uid(fake_mail, "<msg@e.com>") is None

    def test_returns_none_on_non_ok_status(self, fake_mail):
        fake_mail.uid.return_value = ("BAD", [b""])
        assert email_reader._resolve_uid(fake_mail, "<msg@e.com>") is None


class TestFindTrashFolder:
    def test_returns_default_when_no_match(self, fake_mail):
        fake_mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
        assert email_reader.find_trash_folder(fake_mail) == "[Gmail]/Trash"

    def test_returns_default_on_non_ok_status(self, fake_mail):
        fake_mail.list.return_value = ("BAD", [])
        assert email_reader.find_trash_folder(fake_mail) == "[Gmail]/Trash"

    def test_detects_trash_flag(self, fake_mail):
        fake_mail.list.return_value = ("OK", [b'(\\Trash \\HasNoChildren) "/" "Trash"'])
        assert email_reader.find_trash_folder(fake_mail) == "Trash"

    def test_prefers_first_match(self, fake_mail):
        fake_mail.list.return_value = (
            "OK",
            [b'(\\Trash) "/" "First Trash"', b'(\\HasNoChildren) "/" "Bin"'],
        )
        assert email_reader.find_trash_folder(fake_mail) == "First Trash"


class TestMoveToTrash:
    def test_returns_false_when_uid_not_found(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: None)
        monkeypatch.setattr(imap_mod, "_trash_folder_cache", None)
        assert email_reader.move_to_trash(fake_mail, "<m@e.com>") is False

    def test_returns_false_when_copy_fails(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"1")
        monkeypatch.setattr(imap_mod, "_trash_folder_cache", "Trash")
        fake_mail.uid.side_effect = [("BAD", None), ("OK", None), ("OK", None)]
        assert email_reader.move_to_trash(fake_mail, "<m@e.com>") is False

    def test_copies_deletes_and_expunges(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"42")
        monkeypatch.setattr(imap_mod, "_trash_folder_cache", "Trash")
        fake_mail.uid.side_effect = [("OK", None), ("OK", None)]
        result = email_reader.move_to_trash(fake_mail, "<m@e.com>")
        assert result is True
        assert fake_mail.uid.call_count == 2
        fake_mail.expunge.assert_called_once()

    def test_caches_trash_folder_lookup(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"1")
        monkeypatch.setattr(imap_mod, "_trash_folder_cache", None)
        fake_mail.list.return_value = ("OK", [b'(\\Trash) "/" "MyTrash"'])
        fake_mail.uid.side_effect = [("OK", None), ("OK", None), ("OK", None)]
        email_reader.move_to_trash(fake_mail, "<m@e.com>")
        assert imap_mod._trash_folder_cache == "MyTrash"


class TestFolderQuoting:
    def test_move_to_trash_quotes_spaced_folder(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"42")
        monkeypatch.setattr(imap_mod, "_trash_folder_cache", "My Sent Trash")
        fake_mail.uid.side_effect = [("OK", None), ("OK", None)]
        email_reader.move_to_trash(fake_mail, "<m@e.com>")
        copy_args = fake_mail.uid.call_args_list[0].args
        assert copy_args[0] == "copy"
        assert copy_args[2] == '"My Sent Trash"'

    def test_move_to_folder_quotes_spaced_name(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"7")
        fake_mail.uid.side_effect = [("OK", None), ("OK", None)]
        result = email_reader.move_to_folder(fake_mail, "<m@e.com>", "Project X")
        assert result is True
        copy_args = fake_mail.uid.call_args_list[0].args
        assert copy_args[0] == "copy"
        assert copy_args[2] == '"Project X"'

    def test_move_to_folder_passthrough_unquoted_simple_name(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"7")
        fake_mail.uid.side_effect = [("OK", None), ("OK", None)]
        email_reader.move_to_folder(fake_mail, "<m@e.com>", "INBOX")
        copy_args = fake_mail.uid.call_args_list[0].args
        assert copy_args[2] == "INBOX"

    def test_bulk_move_uids_quotes_spaced_folder(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"3")
        fake_mail.uid.side_effect = [("OK", None), ("OK", None)]
        email_reader._bulk_move_uids(fake_mail, ["<m@e.com>"], "Archive Box")
        copy_args = fake_mail.uid.call_args_list[0].args
        assert copy_args[0] == "copy"
        assert copy_args[2] == '"Archive Box"'


class TestImapConnect:
    def test_uses_credentials_and_selects_inbox(self, monkeypatch, fake_mail):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u", "p"))
        with patch.object(imap_mod.imaplib, "IMAP4_SSL", return_value=fake_mail) as mock_ctor:
            result = imap_mod._imap_connect("/db")
        assert result is fake_mail
        mock_ctor.assert_called_once()
        fake_mail.login.assert_called_with("u", "p")
        fake_mail.select.assert_called_with("INBOX")

    def test_defaults_db_path_to_DB_PATH(self, monkeypatch, fake_mail):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u", "p"))
        with patch.object(imap_mod.imaplib, "IMAP4_SSL", return_value=fake_mail):
            imap_mod._imap_connect()
        fake_mail.login.assert_called_with("u", "p")


class TestImapSession:
    def test_yields_connection_and_closes(self, monkeypatch, fake_mail):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u", "p"))
        with patch.object(imap_mod.imaplib, "IMAP4_SSL", return_value=fake_mail):
            with email_reader.imap_session("/db") as mail:
                assert mail is fake_mail
        fake_mail.close.assert_called_once()
        fake_mail.logout.assert_called_once()

    def test_closes_even_on_exception(self, monkeypatch, fake_mail):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u", "p"))
        with patch.object(imap_mod.imaplib, "IMAP4_SSL", return_value=fake_mail):
            with pytest.raises(RuntimeError, match="inner"):
                with email_reader.imap_session("/db"):
                    raise RuntimeError("inner")
        fake_mail.close.assert_called_once()


class TestReconnect:
    def test_closes_sleeps_reconnects(self, monkeypatch, fake_mail):
        sleeps = []
        monkeypatch.setattr(imap_mod.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(imap_mod, "_safe_close", lambda c: None)
        new_mail = MagicMock(name="new")
        monkeypatch.setattr(imap_mod, "_imap_connect", lambda db_path=None: new_mail)
        result = imap_mod._reconnect(fake_mail, "/db")
        assert result is new_mail
        assert sleeps == [imap_mod.RECONNECT_DELAY]

    def test_reconnect_reselects_mailbox(self, monkeypatch, fake_mail):
        monkeypatch.setattr(imap_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(imap_mod, "_safe_close", lambda c: None)
        new_mail = MagicMock(name="new")
        monkeypatch.setattr(imap_mod, "_imap_connect", lambda db_path=None: new_mail)
        result = imap_mod._reconnect(fake_mail, "/db", mailbox="[Gmail]/Sent Mail")
        assert result is new_mail
        new_mail.select.assert_called_once_with("[Gmail]/Sent Mail")

    def test_reconnect_returns_conn_even_if_reselect_fails(self, monkeypatch, fake_mail, caplog):
        monkeypatch.setattr(imap_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(imap_mod, "_safe_close", lambda c: None)
        new_mail = MagicMock(name="new")
        new_mail.select.side_effect = Exception("boom")
        monkeypatch.setattr(imap_mod, "_imap_connect", lambda db_path=None: new_mail)
        with caplog.at_level("WARNING"):
            result = imap_mod._reconnect(fake_mail, "/db", mailbox="[Gmail]/Sent Mail")
        assert result is new_mail
        assert "Could not re-select" in caplog.text


class TestMakeBodyFetchers:
    def test_bulk_returns_parsed_results(self, monkeypatch):
        raw = b"From: a@b.com\r\nSubject: s\r\nMessage-ID: <m@e.com>\r\n\r\nbody\r\n"
        monkeypatch.setattr(
            imap_mod, "_parse_fetched_email", lambda data: [{"message_id": "<m@e.com>", "body": "body"}]
        )
        bulk, single = email_reader._make_body_fetchers("/db")
        mail = MagicMock()
        mail.uid.return_value = ("OK", [(b"env", raw)])
        result = bulk(mail, [(b"1", "<m@e.com>")])
        assert result is not None
        assert result[0]["_message_id"] == "<m@e.com>"

    def test_bulk_returns_none_on_exception(self):
        bulk, _ = email_reader._make_body_fetchers("/db")
        mail = MagicMock()
        mail.uid.side_effect = RuntimeError("boom")
        assert bulk(mail, [(b"1", "<m@e.com>")]) is None

    def test_bulk_returns_none_on_non_ok_status(self):
        bulk, _ = email_reader._make_body_fetchers("/db")
        mail = MagicMock()
        mail.uid.return_value = ("BAD", None)
        assert bulk(mail, [(b"1", "<m@e.com>")]) is None

    def test_bulk_returns_none_on_partial_result(self, monkeypatch):
        bulk, _ = email_reader._make_body_fetchers("/db")
        monkeypatch.setattr(imap_mod, "_parse_fetched_email", lambda data: [{"message_id": "<one@e.com>"}])
        mail = MagicMock()
        mail.uid.return_value = ("OK", [(b"env", b"...")])
        assert bulk(mail, [(b"1", "<one@e.com>"), (b"2", "<two@e.com>")]) is None

    def test_single_returns_parsed_with_message_id(self, monkeypatch):
        _, single = email_reader._make_body_fetchers("/db")
        monkeypatch.setattr(imap_mod, "_parse_fetched_email", lambda data: [{"message_id": "<m@e.com>"}])
        mail = MagicMock()
        mail.uid.return_value = ("OK", [(b"env", b"...")])
        result, returned_mail = single(mail, (b"1", "<m@e.com>"))
        assert result == [{"message_id": "<m@e.com>", "_message_id": "<m@e.com>"}]
        assert returned_mail is mail

    def test_single_returns_empty_on_non_ok_status(self):
        _, single = email_reader._make_body_fetchers("/db")
        mail = MagicMock()
        mail.uid.return_value = ("BAD", None)
        result, returned_mail = single(mail, (b"1", "<m@e.com>"))
        assert result == []
        assert returned_mail is mail

    def test_single_reconnects_on_abort(self, monkeypatch):
        _, single = email_reader._make_body_fetchers("/db")
        mail = MagicMock()
        mail.uid.side_effect = imaplib.IMAP4.abort("aborted")
        new_mail = MagicMock()
        new_mail.uid.return_value = ("OK", [])
        monkeypatch.setattr(imap_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(imap_mod, "_reconnect", lambda c, db_path=None, **kw: new_mail)
        monkeypatch.setattr(imap_mod, "_parse_fetched_email", lambda data: [])
        result, returned_mail = single(mail, (b"1", "<m@e.com>"))
        assert returned_mail is new_mail

    def test_single_gives_up_after_reconnect_failure(self, monkeypatch):
        _, single = email_reader._make_body_fetchers("/db")
        mail = MagicMock()
        mail.uid.side_effect = imaplib.IMAP4.abort("aborted")
        monkeypatch.setattr(imap_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(imap_mod, "_reconnect", lambda c, db_path=None, **kw: _raise(RuntimeError("nope")))
        result, returned_mail = single(mail, (b"1", "<m@e.com>"))
        assert result == []
        assert returned_mail is mail


class TestBatchFetchLoop:
    def test_bulk_success_path(self):
        items = [(b"1", "<a@e.com>"), (b"2", "<b@e.com>")]
        conn = MagicMock()

        def bulk_fn(conn, batch):
            return [{"message_id": mid, "body": "x"} for _, mid in batch]

        single_fn = MagicMock()
        results, returned = email_reader._batch_fetch_loop(conn, items, 25, bulk_fn, single_fn, db_path="/db")
        assert len(results) == 2
        single_fn.assert_not_called()

    def test_falls_back_to_single_on_bulk_failure(self, monkeypatch):
        items = [(b"1", "<a@e.com>")]
        conn = MagicMock()
        new_conn = MagicMock()

        def single_fn(conn, item):
            return [{"body": "single"}], new_conn

        monkeypatch.setattr(imap_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(imap_mod, "_reconnect", lambda c, db_path=None, **kw: new_conn)

        def bulk_raises(conn, batch):
            raise imaplib.IMAP4.abort("aborted")

        results, returned = email_reader._batch_fetch_loop(conn, items, 25, bulk_raises, single_fn, db_path="/db")
        assert results == [{"body": "single"}]
        assert returned is new_conn

    def test_reconnect_failure_goes_to_single_fallback(self, monkeypatch):
        items = [(b"1", "<a@e.com>")]
        conn = MagicMock()
        new_conn = MagicMock()

        def single_fn(conn, item):
            return [{"body": "single"}], new_conn

        def bulk_raises(conn, batch):
            raise imaplib.IMAP4.abort("aborted")

        monkeypatch.setattr(imap_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(
            imap_mod, "_reconnect", lambda c, db_path=None, **kw: _raise(RuntimeError("reconnect failed"))
        )
        results, returned = email_reader._batch_fetch_loop(conn, items, 25, bulk_raises, single_fn, db_path="/db")
        assert results == [{"body": "single"}]


class TestTestConnection:
    def test_success_returns_inbox_count(self, monkeypatch):
        fake = MagicMock()
        fake.uid.return_value = ("OK", [b"1 2 3"])
        monkeypatch.setattr(imap_mod.imaplib, "IMAP4_SSL", MagicMock(return_value=fake))
        result = email_reader.test_connection("server", "u", "p")
        assert result["success"] is True
        assert result["inbox_count"] == 3

    def test_invalid_credentials_returns_error(self, monkeypatch):
        fake = MagicMock()
        fake.login.side_effect = imaplib.IMAP4.error("bad creds")
        monkeypatch.setattr(imap_mod.imaplib, "IMAP4_SSL", MagicMock(return_value=fake))
        result = email_reader.test_connection("server", "u", "p")
        assert result["success"] is False
        assert "Invalid" in result["error"]

    def test_generic_exception_returns_error(self, monkeypatch):
        monkeypatch.setattr(imap_mod.imaplib, "IMAP4_SSL", MagicMock(side_effect=OSError("network down")))
        result = email_reader.test_connection("server", "u", "p")
        assert result["success"] is False
        assert "network down" in result["error"]

    def test_empty_search_returns_zero_count(self, monkeypatch):
        fake = MagicMock()
        fake.uid.return_value = ("OK", [b""])
        monkeypatch.setattr(imap_mod.imaplib, "IMAP4_SSL", MagicMock(return_value=fake))
        result = email_reader.test_connection("server", "u", "p")
        assert result["success"] is True
        assert result["inbox_count"] == 0


class TestFetchHeadersAndCache:
    def test_no_credentials_returns_error(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        result = email_reader.fetch_headers_and_cache(db_path="/tmp/nonexistent.db")
        assert "error" in result

    def test_empty_inbox_returns_zero_counts(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()
        fake.uid.return_value = ("OK", [b""])

        fake_imap_session(monkeypatch, imap_mod, fake)
        result = email_reader.fetch_headers_and_cache(db_path=tmp_db)
        assert result["new_count"] == 0
        assert result["emails"] == []

    def test_top_level_exception_returns_error_dict(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)

        def boom(*a, **kw):
            raise OSError("kaboom")

        monkeypatch.setattr(imap_mod, "imap_session", boom)
        result = email_reader.fetch_headers_and_cache(db_path=tmp_db)
        assert "kaboom" in result["error"]

    def test_existing_hash_flags_refreshed_new_email_inserted(self, monkeypatch, tmp_db):
        cache.save_headers_batch(
            [{"message_id": "<existing@e.com>", "subject": "Old", "date": "Mon, 01 Jan 2024 00:00:00 +0000"}],
            tmp_db,
        )

        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()
        fake.uid.side_effect = [
            ("OK", [b"1 2"]),
            (
                "OK",
                [
                    (
                        b"UID 1 (FLAGS (\\Seen \\Flagged)) (BODY[HEADER.FIELDS (...)])",
                        b"Subject: Existing\r\nFrom: a@b.com\r\nDate: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
                        b"Message-ID: <existing@e.com>\r\n\r\n",
                    ),
                    (
                        b"UID 2 (FLAGS ()) (BODY[HEADER.FIELDS (...)])",
                        b"Subject: Fresh\r\nFrom: c@d.com\r\nDate: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
                        b"Message-ID: <fresh@e.com>\r\n\r\n",
                    ),
                ],
            ),
        ]
        fake.select.return_value = ("OK", [b""])

        fake_imap_session(monkeypatch, imap_mod, fake)
        result = email_reader.fetch_headers_and_cache(db_path=tmp_db)

        assert result["new_count"] == 1
        assert result["existing_count"] == 1

        existing_hash = cache._hash_message_id("<existing@e.com>")
        fresh_hash = cache._hash_message_id("<fresh@e.com>")
        with cache._connect(tmp_db) as conn:
            ex = conn.execute(
                "SELECT status, is_read, is_starred FROM emails WHERE message_id_hash = ?",
                (existing_hash,),
            ).fetchone()
            fr = conn.execute(
                "SELECT is_read, is_starred FROM emails WHERE message_id_hash = ?",
                (fresh_hash,),
            ).fetchone()
        assert ex["status"] == "headers_only"  # untouched by flag refresh
        assert int(ex["is_read"]) == 1
        assert int(ex["is_starred"]) == 1
        assert int(fr["is_read"]) == 0
        assert int(fr["is_starred"]) == 0

    def test_reconcile_skipped_on_partial_search(self, monkeypatch, tmp_db):
        cache.save_headers_batch(
            [
                {"message_id": f"<real{i}@e.com>", "subject": f"S{i}", "date": "Mon, 01 Jan 2024 00:00:00 +0000"}
                for i in range(10)
            ],
            tmp_db,
        )
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()
        fake.uid.side_effect = [
            ("OK", [b" ".join(str(i).encode() for i in range(1, 11))]),  # search: 10 UIDs
            (
                "OK",
                [
                    (
                        b"UID 1 (FLAGS ()) (BODY[HEADER.FIELDS (...)])",
                        b"Subject: S1\r\nMessage-ID: <real1@e.com>\r\n\r\n",
                    ),
                    (
                        b"UID 2 (FLAGS ()) (BODY[HEADER.FIELDS (...)])",
                        b"Subject: S2\r\nMessage-ID: <real2@e.com>\r\n\r\n",
                    ),
                ],
            ),
        ]
        fake.select.return_value = ("OK", [b""])
        fake_imap_session(monkeypatch, imap_mod, fake)

        email_reader.fetch_headers_and_cache(db_path=tmp_db, protected_hashes=set())
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<real1@e.com>")) is not None
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<real3@e.com>")) is not None

    def test_reconcile_not_called_on_fetch_failure(self, monkeypatch, tmp_db):
        cache.save_headers_batch(
            [{"message_id": "<keep@e.com>", "subject": "Keep", "date": "Mon, 01 Jan 2024 00:00:00 +0000"}],
            tmp_db,
        )
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()
        fake.uid.side_effect = [
            ("OK", [b"1 2 3"]),
            ("OK", []),
        ]
        fake.select.return_value = ("OK", [b""])
        fake_imap_session(monkeypatch, imap_mod, fake)

        calls = []
        monkeypatch.setattr(cache, "reconcile_inbox", lambda *a, **kw: calls.append(a) or 0)

        email_reader.fetch_headers_and_cache(db_path=tmp_db)
        assert calls == [], "reconcile must NOT run on a failed fetch"
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<keep@e.com>")) is not None

    def test_reconcile_runs_on_complete_fetch(self, monkeypatch, tmp_db):
        cache.save_headers_batch(
            [{"message_id": "<ghost@e.com>", "subject": "Ghost", "date": "Mon, 01 Jan 2024 00:00:00 +0000"}],
            tmp_db,
        )
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()
        fake.uid.side_effect = [
            ("OK", [b"1"]),
            (
                "OK",
                [
                    (
                        b"UID 1 (FLAGS ()) (BODY[HEADER.FIELDS (...)])",
                        b"Subject: Real\r\nMessage-ID: <real@e.com>\r\n\r\n",
                    ),
                ],
            ),
        ]
        fake.select.return_value = ("OK", [b""])
        fake_imap_session(monkeypatch, imap_mod, fake)
        email_reader.fetch_headers_and_cache(db_path=tmp_db, protected_hashes=set())
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<ghost@e.com>")) is None
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<real@e.com>")) is not None

    def test_server_hashes_includes_no_uid_entries(self, monkeypatch, tmp_db):
        cache.save_headers_batch(
            [{"message_id": "<nouid@e.com>", "subject": "NoUid", "date": "Mon, 01 Jan 2024 00:00:00 +0000"}],
            tmp_db,
        )
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()
        fake.uid.side_effect = [
            ("OK", [b"1"]),
            (
                "OK",
                [
                    (b"(FLAGS ()) (BODY[HEADER.FIELDS (...)])", b"Subject: NoUid\r\nMessage-ID: <nouid@e.com>\r\n\r\n"),
                ],
            ),
        ]
        fake.select.return_value = ("OK", [b""])
        fake_imap_session(monkeypatch, imap_mod, fake)

        email_reader.fetch_headers_and_cache(db_path=tmp_db)
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<nouid@e.com>")) is not None

    def test_reconcile_runs_after_complete_batched_fetch(self, monkeypatch, tmp_db):
        batch_size = email_reader.FETCH_BATCH_SIZE
        num_uids = batch_size + 5  # spans 2 batches
        cache.save_headers_batch(
            [{"message_id": "<ghost@e.com>", "subject": "Ghost", "date": "Mon, 01 Jan 2024 00:00:00 +0000"}],
            tmp_db,
        )
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)

        def fake_uid(*args):
            cmd = args[0]
            if cmd == "search":
                uids_list = b" ".join(str(i).encode() for i in range(1, num_uids + 1))
                return ("OK", [uids_list])
            id_bytes = args[1]
            items = []
            for uid in id_bytes.split(b","):
                items.append(
                    (
                        f"UID {uid.decode()} (BODY[HEADER.FIELDS (...)])".encode(),
                        f"Message-ID: <real{uid.decode()}@e.com>\r\n\r\n".encode(),
                    )
                )
            return ("OK", items)

        fake = MagicMock()
        fake.uid.side_effect = fake_uid
        fake.select.return_value = ("OK", [b""])
        fake_imap_session(monkeypatch, imap_mod, fake)

        email_reader.fetch_headers_and_cache(db_path=tmp_db, protected_hashes=set())
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<ghost@e.com>")) is None
        assert cache.get_email_by_hash(tmp_db, cache._hash_message_id("<real1@e.com>")) is not None

    def test_sync_sent_replies_batches_large_sent_folder(self, monkeypatch, tmp_db):
        from src.tests._helpers import fake_imap_session

        cache.save_email_credentials("me@e.com", "secret", tmp_db)
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        batch_size = email_reader.FETCH_BATCH_SIZE
        num_uids = batch_size * 2 + 3  # spans 3 batches

        def fake_uid(*args):
            cmd = args[0]
            if cmd == "search":
                uids_list = b" ".join(str(i).encode() for i in range(1, num_uids + 1))
                return ("OK", [uids_list])
            if cmd == "fetch":
                id_bytes = args[1]
                items = []
                for uid in id_bytes.split(b","):
                    items.append(
                        (
                            f"UID {uid.decode()} X-GM-THRID 1809095669921875987".encode(),
                            f"Message-ID: <sent{uid.decode()}@e.com>\r\n\r\n".encode(),
                        )
                    )
                return ("OK", items)
            return ("OK", [b""])

        fake = MagicMock()
        fake.uid.side_effect = fake_uid
        fake.select.return_value = ("OK", [b""])
        fake.list.return_value = ("OK", [b'(\\Sent) "/" "[Gmail]/Sent Mail"'])
        fake_imap_session(monkeypatch, imap_mod, fake)
        imap_mod.reset_folder_caches()
        email_reader.sync_sent_replies(db_path=tmp_db)

        header_fetch_calls = [
            c
            for c in fake.uid.call_args_list
            if c.args and c.args[0] == "fetch" and "HEADER.FIELDS" in (c.args[2] if len(c.args) > 2 else "")
        ]
        assert len(header_fetch_calls) >= 3  # batched into >=3 calls
        for call in header_fetch_calls:
            id_bytes = call.args[1]
            uid_count = len(id_bytes.split(b","))
            assert uid_count <= batch_size


class TestFetchBodiesInFolder:
    def test_empty_message_ids_returns_empty(self, monkeypatch, tmp_db):
        cache.save_email_credentials("me@e.com", "secret", tmp_db)
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()
        fake.select.return_value = ("OK", [b""])
        from src.tests._helpers import fake_imap_session

        fake_imap_session(monkeypatch, imap_mod, fake)
        result = imap_mod._fetch_bodies_in_folder(tmp_db, "Sent", [])
        assert result == []
        fake.uid.assert_not_called()

    def test_fetches_and_matches_bodies(self, monkeypatch, tmp_db):
        from src.tests._helpers import fake_imap_session

        cache.save_email_credentials("me@e.com", "secret", tmp_db)
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        raw = b"From: me@e.com\r\nMessage-ID: <m1@e.com>\r\n\r\nHello body\r\n"
        fake = MagicMock()
        fake.select.return_value = ("OK", [b""])
        fake.uid.side_effect = [
            ("OK", [b"1"]),  # search for UID by message-id
            ("OK", [(b"UID 1", raw)]),  # body fetch
            ("OK", [b""]),  # INBOX restore select (via _resolve_uid path)
        ]
        fake_imap_session(monkeypatch, imap_mod, fake)
        result = imap_mod._fetch_bodies_in_folder(tmp_db, "Sent", ["<m1@e.com>"])
        assert result == [("<m1@e.com>", "Hello body")]

    def test_partial_failure_skips_missing_uid(self, monkeypatch, tmp_db):
        from src.tests._helpers import fake_imap_session

        cache.save_email_credentials("me@e.com", "secret", tmp_db)
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        raw2 = b"From: me@e.com\r\nMessage-ID: <m2@e.com>\r\n\r\nSecond body\r\n"
        fake = MagicMock()
        fake.select.return_value = ("OK", [b""])
        fake.uid.side_effect = [
            ("OK", [b""]),  # search for missing → no UID
            ("OK", [b"2"]),  # search for m2 → UID 2
            ("OK", [(b"UID 2", raw2)]),  # body fetch for m2
        ]
        fake_imap_session(monkeypatch, imap_mod, fake)
        result = imap_mod._fetch_bodies_in_folder(tmp_db, "Sent", ["<missing@e.com>", "<m2@e.com>"])
        assert result == [("<m2@e.com>", "Second body")]


class TestFetchBodiesByMessageIds:
    def test_empty_input_returns_zeros(self):
        result = email_reader.fetch_bodies_by_message_ids([], db_path="/db")
        assert result == {"fetched": 0, "failed": 0}

    def test_no_credentials_returns_zeros(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        result = email_reader.fetch_bodies_by_message_ids(["<m@e.com>"], db_path="/db")
        assert result == {"fetched": 0, "failed": 0}

    def test_counts_uid_not_found_as_failed(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        monkeypatch.setattr(email_reader, "_resolve_uid", lambda mail, mid: None)
        fake = MagicMock()

        monkeypatch.setattr(email_reader, "_imap_connect", lambda db_path=None: fake)
        result = email_reader.fetch_bodies_by_message_ids(["<m@e.com>"], db_path=tmp_db)
        assert result["failed"] == 1
        assert result["fetched"] == 0

    def test_generic_exception_counts_all_as_failed(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)

        def boom(*a, **kw):
            raise OSError("nope")

        monkeypatch.setattr(email_reader, "_imap_connect", boom)
        result = email_reader.fetch_bodies_by_message_ids(["<a@e.com>", "<b@e.com>"], db_path=tmp_db)
        assert result["failed"] == 2

    def test_connection_failure_after_fetches_sums_to_total(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)

        class _Conn:
            def uid(self, *a, **kw):
                return ("OK", [b"1"])

            def close(self):
                pass

            def logout(self):
                pass

        monkeypatch.setattr(email_reader, "_imap_connect", lambda db_path=None: _Conn())
        monkeypatch.setattr(email_reader, "_resolve_uid", lambda mail, mid: b"1")
        monkeypatch.setattr(
            email_reader,
            "_batch_fetch_loop",
            lambda *a, **kw: _raise(OSError("connection lost mid-batch")),
        )
        result = email_reader.fetch_bodies_by_message_ids(["<a@e.com>", "<b@e.com>", "<c@e.com>"], db_path=tmp_db)
        assert result["fetched"] + result["failed"] == 3
        assert result["fetched"] == 0
        assert result["failed"] == 3


class TestDeleteEmail:
    def test_no_credentials_returns_error(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        result = email_reader.delete_email("<m@e.com>", db_path="/tmp/nonexistent.db")
        assert "error" in result

    def test_success_returns_deleted_true(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        cache.save_headers_batch([{"message_id": "<m@e.com>", "subject": "s", "date": "d"}], tmp_db)
        fake = MagicMock()

        fake_imap_session(monkeypatch, imap_mod, fake)
        monkeypatch.setattr(imap_mod, "move_to_trash", lambda mail, mid: True)
        result = email_reader.delete_email("<m@e.com>", db_path=tmp_db)
        assert result == {"deleted": True, "message_id": "<m@e.com>"}
        assert cache.read_emails(tmp_db) == []

    def test_move_to_trash_failure_returns_deleted_false(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()

        fake_imap_session(monkeypatch, imap_mod, fake)
        monkeypatch.setattr(imap_mod, "move_to_trash", lambda mail, mid: False)
        result = email_reader.delete_email("<m@e.com>", db_path=tmp_db)
        assert result["deleted"] is False
        assert "not found" in result["error"].lower() or "Message not found" in result["error"]

    def test_generic_exception_returns_error(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)

        def boom(*a, **kw):
            raise OSError("kaboom")

        monkeypatch.setattr(imap_mod, "imap_session", boom)
        result = email_reader.delete_email("<m@e.com>", db_path=tmp_db)
        assert "kaboom" in result["error"]


class TestFetchBodiesForIds:
    def test_empty_input_returns_empty(self):
        assert email_reader.fetch_bodies_for_ids([], db_path="/db") == []


class TestExtractFlags:
    def test_seen_and_flagged(self):
        assert imap_mod._extract_flags("FLAGS (\\Seen \\Flagged \\Recent)") == (True, True)

    def test_neither(self):
        assert imap_mod._extract_flags("FLAGS (\\Recent)") == (False, False)

    def test_seen_only(self):
        assert imap_mod._extract_flags("FLAGS (\\Seen)") == (True, False)

    def test_flags_absent_returns_false_false(self):
        assert imap_mod._extract_flags("UID 1 (BODY[HEADER.FIELDS (...)])") == (False, False)


class TestExtractGmThrid:
    def test_extracts_thrid_hash(self):
        env = b"1 (X-GM-THRID 1809095669921875987 UID 16 FLAGS (\\Seen))".decode()
        result = imap_mod._extract_gm_thrid(env)
        assert result == cache._hash_message_id("1809095669921875987")

    def test_returns_none_when_absent(self):
        env = b"1 (UID 16 FLAGS (\\Seen))".decode()
        assert imap_mod._extract_gm_thrid(env) is None


class TestParseHeaderItemsGmThrid:
    def _msg_data(self, envelope_extra=b""):
        return [
            (b"UID 16 FLAGS (\\Seen)" + envelope_extra, b"Subject: Hi\r\nMessage-ID: <m@e.com>\r\n\r\n"),
        ]

    def test_dict_carries_gm_thrid_when_present(self):
        msg_data = self._msg_data(b" X-GM-THRID 1809095669921875987")
        result = imap_mod._parse_header_items(msg_data)
        assert len(result) == 1
        gm_hash = cache._hash_message_id("1809095669921875987")
        assert result[0]["gm_thrid"] == gm_hash
        assert result[0]["thread_id"] == gm_hash

    def test_dict_gm_thrid_none_when_absent(self):
        result = imap_mod._parse_header_items(self._msg_data())
        assert len(result) == 1
        assert result[0]["gm_thrid"] is None


class TestFindArchiveFolder:
    def test_returns_default_when_list_fails(self, fake_mail):
        fake_mail.list.return_value = ("BAD", [])
        assert email_reader.find_archive_folder(fake_mail) == "[Gmail]/All Mail"

    def test_detects_all_attribute(self, fake_mail):
        fake_mail.list.return_value = ("OK", [b'(\\All \\HasNoChildren) "/" "All Mail"'])
        assert email_reader.find_archive_folder(fake_mail) == "All Mail"

    def test_falls_back_to_default(self, fake_mail):
        fake_mail.list.return_value = ("OK", [b'(\\Inbox) "/" "INBOX"'])
        assert email_reader.find_archive_folder(fake_mail) == "[Gmail]/All Mail"


class TestFindFolderByAttr:
    def test_matches_attribute(self, fake_mail):
        fake_mail.list.return_value = ("OK", [b'(\\Trash) "/" "Trash"'])
        assert email_reader.find_folder_by_attr(fake_mail, "\\Trash", "Fallback") == "Trash"

    def test_returns_fallback_when_no_match(self, fake_mail):
        fake_mail.list.return_value = ("OK", [b'(\\Inbox) "/" "INBOX"'])
        assert email_reader.find_folder_by_attr(fake_mail, "\\Xyz", "Fallback") == "Fallback"


class TestValidateFolderName:
    def test_rejects_empty(self):
        assert email_reader._validate_folder_name("") is False

    def test_accepts_normal_name(self):
        assert email_reader._validate_folder_name("Work") is True
        assert email_reader._validate_folder_name("[Gmail]/All Mail") is True

    @pytest.mark.parametrize("bad", ['a"b', "a\\b", "a\nb", "a\rb", "a\x00b", "a{b"])
    def test_rejects_unsafe_chars(self, bad):
        assert email_reader._validate_folder_name(bad) is False


class TestQuoteMailbox:
    def test_leaves_simple_name_unchanged(self):
        assert email_reader._quote_mailbox("INBOX") == "INBOX"
        assert email_reader._quote_mailbox("Work") == "Work"

    def test_quotes_name_with_space(self):
        assert email_reader._quote_mailbox("[Gmail]/Sent Mail") == '"[Gmail]/Sent Mail"'
        assert email_reader._quote_mailbox("[Gmail]/All Mail") == '"[Gmail]/All Mail"'

    def test_quotes_name_with_parens(self):
        assert email_reader._quote_mailbox("a(b)") == '"a(b)"'

    def test_quotes_name_with_percent_or_star(self):
        assert email_reader._quote_mailbox("a%b") == '"a%b"'
        assert email_reader._quote_mailbox("a*b") == '"a*b"'

    def test_escapes_embedded_quotes_and_backslashes(self):
        assert email_reader._quote_mailbox('a"b') == '"a\\"b"'
        assert email_reader._quote_mailbox("a\\b") == '"a\\\\b"'

    def test_empty_name_passes_through(self):
        assert email_reader._quote_mailbox("") == ""


class TestMoveToFolder:
    def test_returns_false_when_no_folder(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"1")
        assert email_reader.move_to_folder(fake_mail, "<m@e.com>", "") is False

    def test_returns_false_when_uid_not_found(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: None)
        assert email_reader.move_to_folder(fake_mail, "<m@e.com>", "Work") is False

    def test_copies_and_deletes(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"7")
        fake_mail.uid.side_effect = [("OK", None), ("OK", None)]
        assert email_reader.move_to_folder(fake_mail, "<m@e.com>", "Work") is True
        fake_mail.expunge.assert_called_once()

    def test_returns_false_when_copy_fails(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"7")
        fake_mail.uid.return_value = ("BAD", None)
        assert email_reader.move_to_folder(fake_mail, "<m@e.com>", "Work") is False

    def test_rejects_injection_style_folder(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"7")
        assert email_reader.move_to_folder(fake_mail, "<m@e.com>", 'INBOX" "') is False
        fake_mail.uid.assert_not_called()


class TestSetFlagsBulk:
    def test_empty_returns_zero(self, fake_mail):
        assert email_reader.set_flags_bulk(fake_mail, [], "\\Seen", True) == 0

    def test_counts_updated(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: mid.encode())
        fake_mail.uid.return_value = ("OK", None)
        assert email_reader.set_flags_bulk(fake_mail, ["<a@e.com>", "<b@e.com>"], "\\Seen", True) == 2

    def test_skips_missing_uids(self, fake_mail, monkeypatch):
        monkeypatch.setattr(
            imap_mod,
            "_resolve_uid",
            lambda mail, mid: mid.encode() if mid == "<a@e.com>" else None,
        )
        fake_mail.uid.return_value = ("OK", None)
        assert email_reader.set_flags_bulk(fake_mail, ["<a@e.com>", "<b@e.com>"], "\\Seen", True) == 1


class TestArchiveRemote:
    def test_empty_returns_false(self, tmp_db, fake_credentials):
        assert email_reader.archive_remote("", db_path=tmp_db) is False

    def test_no_credentials_returns_false(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        assert email_reader.archive_remote("<a@e.com>", db_path=tmp_db) is False

    def test_resolves_folder_and_moves(self, monkeypatch, tmp_db, fake_credentials):
        fake_imap_session(monkeypatch, imap_mod, MagicMock())
        monkeypatch.setattr(imap_mod, "find_archive_folder", lambda mail: "Archive")
        monkeypatch.setattr(imap_mod, "move_to_folder", lambda mail, mid, folder: folder == "Archive")
        assert email_reader.archive_remote("<a@e.com>", db_path=tmp_db) is True

    def test_reuses_cached_folder(self, monkeypatch, tmp_db, fake_credentials):
        fake_imap_session(monkeypatch, imap_mod, MagicMock())
        monkeypatch.setattr(imap_mod, "_archive_folder_cache", "[Gmail]/All Mail")
        find = MagicMock()
        monkeypatch.setattr(imap_mod, "find_archive_folder", find)
        monkeypatch.setattr(imap_mod, "move_to_folder", lambda mail, mid, folder: True)
        assert email_reader.archive_remote("<a@e.com>", db_path=tmp_db) is True
        find.assert_not_called()

    def test_swallows_imap_failure(self, monkeypatch, tmp_db, fake_credentials):
        def boom(*a, **kw):
            raise OSError("imap down")

        monkeypatch.setattr(imap_mod, "imap_session", boom)
        assert email_reader.archive_remote("<a@e.com>", db_path=tmp_db) is False


class TestResolveArchiveFolder:
    def test_uses_attribute_matcher(self, monkeypatch, tmp_db, fake_credentials):
        fake_imap_session(monkeypatch, imap_mod, MagicMock())
        monkeypatch.setattr(imap_mod, "find_archive_folder", lambda mail: "All Mail")
        monkeypatch.setattr(imap_mod, "_archive_folder_cache", None)
        assert email_reader.resolve_archive_folder(db_path=tmp_db) == "All Mail"

    def test_caches_result(self, monkeypatch, tmp_db, fake_credentials):
        fake_imap_session(monkeypatch, imap_mod, MagicMock())
        find = MagicMock(side_effect=lambda mail: "Archive")
        monkeypatch.setattr(imap_mod, "find_archive_folder", find)
        monkeypatch.setattr(imap_mod, "_archive_folder_cache", None)
        email_reader.resolve_archive_folder(db_path=tmp_db)
        email_reader.resolve_archive_folder(db_path=tmp_db)
        find.assert_called_once()

    def test_falls_back_on_failure(self, monkeypatch, tmp_db, fake_credentials):
        def boom(*a, **kw):
            raise OSError("imap down")

        monkeypatch.setattr(imap_mod, "imap_session", boom)
        monkeypatch.setattr(imap_mod, "_archive_folder_cache", None)
        assert email_reader.resolve_archive_folder(db_path=tmp_db) == imap_mod.GMAIL_DEFAULT_ARCHIVE

    def test_single_and_bulk_resolve_to_same_folder(self, monkeypatch, tmp_db, fake_credentials):
        fake_imap_session(monkeypatch, imap_mod, MagicMock())
        monkeypatch.setattr(imap_mod, "find_archive_folder", lambda mail: "All Mail")
        monkeypatch.setattr(imap_mod, "_archive_folder_cache", None)
        single = imap_mod.resolve_archive_folder(db_path=tmp_db)
        monkeypatch.setattr(imap_mod, "_archive_folder_cache", None)
        bulk = imap_mod.resolve_archive_folder(db_path=tmp_db)
        assert single == bulk == "All Mail"


class TestMoveRemote:
    def test_empty_returns_false(self, tmp_db, fake_credentials):
        assert email_reader.move_remote("", "Work", db_path=tmp_db) is False

    def test_no_folder_returns_false(self, tmp_db, fake_credentials):
        assert email_reader.move_remote("<a@e.com>", "", db_path=tmp_db) is False

    def test_moves_on_server(self, monkeypatch, tmp_db, fake_credentials):
        fake_imap_session(monkeypatch, imap_mod, MagicMock())
        monkeypatch.setattr(imap_mod, "move_to_folder", lambda mail, mid, folder: folder == "Work")
        assert email_reader.move_remote("<a@e.com>", "Work", db_path=tmp_db) is True

    def test_swallows_imap_failure(self, monkeypatch, tmp_db, fake_credentials):
        def boom(*a, **kw):
            raise OSError("imap down")

        monkeypatch.setattr(imap_mod, "imap_session", boom)
        assert email_reader.move_remote("<a@e.com>", "Work", db_path=tmp_db) is False


class TestBulkArchiveRemote:
    def test_empty_returns_zero(self, tmp_db, fake_credentials):
        assert email_reader.bulk_archive_remote([], db_path=tmp_db) == 0

    def test_no_credentials_returns_zero(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        assert email_reader.bulk_archive_remote(["<a@e.com>"], db_path=tmp_db) == 0

    def test_delegates_to_resolve_and_bulk_move(self, monkeypatch, tmp_db, fake_credentials):
        monkeypatch.setattr(imap_mod, "resolve_archive_folder", lambda db_path=None: "All Mail")
        called = {}

        def fake_bulk_move_remote(mids, folder, db_path=None):
            called["mids"] = list(mids)
            called["folder"] = folder
            return len(mids)

        monkeypatch.setattr(imap_mod, "bulk_move_remote", fake_bulk_move_remote)
        assert email_reader.bulk_archive_remote(["<a@e.com>", "<b@e.com>"], db_path=tmp_db) == 2
        assert called["folder"] == "All Mail"
        assert called["mids"] == ["<a@e.com>", "<b@e.com>"]


class TestBulkSetFlagRemote:
    def test_empty_returns_zero(self, tmp_db, fake_credentials):
        assert email_reader.bulk_set_flag_remote([], "\\Seen", True, db_path=tmp_db) == 0

    def test_no_credentials_returns_zero(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        assert email_reader.bulk_set_flag_remote(["<a@e.com>"], "\\Seen", True, db_path=tmp_db) == 0

    def test_runs_imap_store(self, monkeypatch, tmp_db, fake_credentials):
        fake = MagicMock()

        fake_imap_session(monkeypatch, imap_mod, fake)
        monkeypatch.setattr(imap_mod, "set_flags_bulk", lambda mail, mids, flag, on: len(mids))
        assert email_reader.bulk_set_flag_remote(["<a@e.com>", "<b@e.com>"], "\\Seen", True, db_path=tmp_db) == 2

    def test_swallows_imap_failure(self, monkeypatch, tmp_db, fake_credentials):
        def boom(*a, **kw):
            raise OSError("imap down")

        monkeypatch.setattr(imap_mod, "imap_session", boom)
        assert email_reader.bulk_set_flag_remote(["<a@e.com>"], "\\Seen", True, db_path=tmp_db) == 0


class TestBulkMoveUidS:
    def test_empty_returns_zero(self, fake_mail):
        assert email_reader._bulk_move_uids(fake_mail, [], "Work") == 0
        fake_mail.expunge.assert_not_called()

    def test_rejects_unsafe_folder(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: b"1")
        assert email_reader._bulk_move_uids(fake_mail, ["<a@e.com>"], 'a"b') == 0
        fake_mail.uid.assert_not_called()
        fake_mail.expunge.assert_not_called()

    def test_single_expunge_for_many_messages(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: mid.encode())
        fake_mail.uid.return_value = ("OK", None)
        moved = email_reader._bulk_move_uids(fake_mail, ["<a@e.com>", "<b@e.com>", "<c@e.com>"], "Work")
        assert moved == 3
        # uid calls: three COPYs + one STORE = four total.
        assert fake_mail.uid.call_count == 4
        store_args = fake_mail.uid.call_args_list[3].args
        assert store_args[0] == "store"
        assert store_args[1] == b"<a@e.com>,<b@e.com>,<c@e.com>"
        assert store_args[2] == "+FLAGS"
        fake_mail.expunge.assert_called_once()

    def test_partial_failure_still_single_expunge(self, fake_mail, monkeypatch):
        monkeypatch.setattr(
            imap_mod,
            "_resolve_uid",
            lambda mail, mid: None if mid == "<b@e.com>" else mid.encode(),
        )
        fake_mail.uid.return_value = ("OK", None)
        moved = email_reader._bulk_move_uids(fake_mail, ["<a@e.com>", "<b@e.com>", "<c@e.com>"], "Work")
        assert moved == 2
        # two COPYs (b was skipped) + one STORE = three total.
        assert fake_mail.uid.call_count == 3
        fake_mail.expunge.assert_called_once()

    def test_no_successful_copies_skips_expunge(self, fake_mail, monkeypatch):
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: mid.encode())
        fake_mail.uid.return_value = ("BAD", None)
        assert email_reader._bulk_move_uids(fake_mail, ["<a@e.com>"], "Work") == 0
        fake_mail.expunge.assert_not_called()


class TestBulkMoveRemote:
    def test_empty_returns_zero(self, tmp_db, fake_credentials):
        assert email_reader.bulk_move_remote([], "Work", db_path=tmp_db) == 0

    def test_no_folder_returns_zero(self, tmp_db, fake_credentials):
        assert email_reader.bulk_move_remote(["<a@e.com>"], "", db_path=tmp_db) == 0

    def test_moves_on_server(self, monkeypatch, tmp_db, fake_credentials):
        fake = MagicMock()

        fake_imap_session(monkeypatch, imap_mod, fake)
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: mid.encode())
        fake.uid.return_value = ("OK", None)
        assert email_reader.bulk_move_remote(["<a@e.com>", "<b@e.com>"], "Work", db_path=tmp_db) == 2
        fake.expunge.assert_called_once()

    def test_swallows_imap_failure(self, monkeypatch, tmp_db, fake_credentials):
        def boom(*a, **kw):
            raise OSError("imap down")

        monkeypatch.setattr(imap_mod, "imap_session", boom)
        assert email_reader.bulk_move_remote(["<a@e.com>"], "Work", db_path=tmp_db) == 0


class TestBulkDeleteRemote:
    def test_empty_returns_zero(self, tmp_db, fake_credentials):
        assert email_reader.bulk_delete_remote([], db_path=tmp_db) == 0

    def test_trashes_on_server(self, monkeypatch, tmp_db, fake_credentials):
        fake = MagicMock()

        fake_imap_session(monkeypatch, imap_mod, fake)
        monkeypatch.setattr(imap_mod, "find_trash_folder", lambda mail: "Trash")
        monkeypatch.setattr(imap_mod, "_resolve_uid", lambda mail, mid: mid.encode())
        fake.uid.return_value = ("OK", None)
        assert email_reader.bulk_delete_remote(["<a@e.com>", "<b@e.com>"], db_path=tmp_db) == 2
        fake.expunge.assert_called_once()

    def test_swallows_imap_failure(self, monkeypatch, tmp_db, fake_credentials):
        def boom(*a, **kw):
            raise OSError("imap down")

        monkeypatch.setattr(imap_mod, "imap_session", boom)
        assert email_reader.bulk_delete_remote(["<a@e.com>"], db_path=tmp_db) == 0


class TestListFolders:
    def test_no_credentials_returns_empty(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        assert email_reader.list_folders(db_path=tmp_db) == []

    def test_parses_folders(self, monkeypatch, tmp_db, fake_credentials):
        fake = MagicMock()
        fake.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\Noselect) "/" "root"',
                b'(\\HasChildren) "/" "Work"',
            ],
        )

        fake_imap_session(monkeypatch, imap_mod, fake)
        folders = email_reader.list_folders(db_path=tmp_db)
        assert "INBOX" in folders
        assert "Work" in folders
        assert "root" not in folders  # \\Noselect excluded


class TestFindSentFolder:
    def test_finds_sent_attribute(self, fake_mail):
        fake_mail.list.return_value = (
            "OK",
            [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "Sent"'],
        )
        assert email_reader.find_sent_folder(fake_mail) == "Sent"

    def test_falls_back_to_gmail_default(self, fake_mail):
        fake_mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
        assert email_reader.find_sent_folder(fake_mail) == "[Gmail]/Sent Mail"


class TestSyncSentReplies:
    def _make_sent_raw(self, mid, in_reply_to=None):
        headers = (
            f"From: me@e.com\r\nSubject: Re: Hello\r\nDate: Tue, 02 Jan 2024 10:00:00 +0000\r\nMessage-ID: {mid}\r\n"
        )
        if in_reply_to:
            headers += f"In-Reply-To: {in_reply_to}\r\nReferences: {in_reply_to}\r\n"
        return (headers + "\r\n" + "reply body\r\n").encode()

    def test_persists_new_sent_reply_linked_to_inbox_email(self, monkeypatch, tmp_db, fake_credentials):
        from src.tests._helpers import save_fetched

        gm_hash = cache._hash_message_id("1809095669921875987")
        save_fetched(
            {
                "message_id": "<orig@e.com>",
                "from": "a@b.com",
                "subject": "Hello",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "hi",
                "in_reply_to": None,
                "thread_id": gm_hash,
                "gm_thrid": gm_hash,
            },
            tmp_db,
        )

        fake = MagicMock()
        fake.list.return_value = ("OK", [b'(\\Sent) "/" "Sent"'])
        fake.select.return_value = ("OK", [b""])
        fake.uid.side_effect = [
            ("OK", [b"1"]),
            (
                "OK",
                [(b"UID 1 X-GM-THRID 1809095669921875987", self._make_sent_raw("<sent1@e.com>", "<orig@e.com>"))],
            ),
            ("OK", [b"1"]),
            ("OK", [(b"UID 1", self._make_sent_raw("<sent1@e.com>", "<orig@e.com>"))]),
        ]
        fake_imap_session(monkeypatch, imap_mod, fake)

        result = email_reader.sync_sent_replies(db_path=tmp_db)
        assert result["synced"] == 1

        conv = cache.get_conversation(tmp_db, cache._hash_message_id("<orig@e.com>"))
        assert "<sent1@e.com>" in [c["message_id"] for c in conv]

        sent = cache.get_email_by_hash(tmp_db, cache._hash_message_id("<sent1@e.com>"))
        assert sent["is_sent"] == 1

        assert sent["thread_id"] == gm_hash
        assert sent["gm_thrid"] == gm_hash

    def test_skips_already_cached_sent_message(self, monkeypatch, tmp_db, fake_credentials):
        from src.tests._helpers import save_fetched

        save_fetched(
            {
                "message_id": "<sent1@e.com>",
                "from": "me@e.com",
                "subject": "Re: Hello",
                "date": "Tue, 02 Jan 2024 10:00:00 +0000",
                "body": "reply",
                "in_reply_to": "<orig@e.com>",
            },
            tmp_db,
        )

        fake = MagicMock()
        fake.list.return_value = ("OK", [b'(\\Sent) "/" "Sent"'])
        fake.select.return_value = ("OK", [b""])
        fake.uid.side_effect = [
            ("OK", [b"1"]),  # uid search ALL
            ("OK", [(b"UID 1", self._make_sent_raw("<sent1@e.com>", "<orig@e.com>"))]),
        ]
        fake_imap_session(monkeypatch, imap_mod, fake)

        result = email_reader.sync_sent_replies(db_path=tmp_db)
        assert result["synced"] == 0
        assert result["skipped"] == 1

        existing = cache.get_email_by_hash(tmp_db, cache._hash_message_id("<sent1@e.com>"))
        assert existing["is_sent"] == 1

    def test_no_credentials_returns_zeros(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        assert email_reader.sync_sent_replies(db_path=tmp_db) == {"synced": 0, "skipped": 0}

    def test_empty_sent_folder_returns_zeros(self, monkeypatch, tmp_db, fake_credentials):
        fake = MagicMock()
        fake.list.return_value = ("OK", [b'(\\Sent) "/" "Sent"'])
        fake.select.return_value = ("OK", [b""])
        fake.uid.return_value = ("OK", [b""])
        fake_imap_session(monkeypatch, imap_mod, fake)
        result = email_reader.sync_sent_replies(db_path=tmp_db)
        assert result == {"synced": 0, "skipped": 0}

    def test_quotes_folder_name_with_spaces_for_select(self, monkeypatch, tmp_db, fake_credentials):
        fake = MagicMock()
        fake.list.return_value = ("OK", [b'(\\Sent) "/" "[Gmail]/Sent Mail"'])
        fake.select.return_value = ("OK", [b""])
        fake.uid.return_value = ("OK", [b""])
        fake_imap_session(monkeypatch, imap_mod, fake)

        email_reader.sync_sent_replies(db_path=tmp_db)

        sent_selects = [
            call.args[0] for call in fake.select.call_args_list if call.args and "Sent Mail" in call.args[0]
        ]
        assert sent_selects, "expected at least one SELECT of the Sent folder"
        for mailbox in sent_selects:
            assert mailbox.startswith('"') and mailbox.endswith('"'), (
                f"mailbox name with spaces must be quoted, got: {mailbox!r}"
            )


class TestSyncAllMail:
    def _make_raw(self, mid, subject="Archived thread", in_reply_to=None, sender="them@e.com"):
        headers = (
            f"From: {sender}\r\nSubject: {subject}\r\nDate: Tue, 02 Jan 2024 10:00:00 +0000\r\nMessage-ID: {mid}\r\n"
        )
        if in_reply_to:
            headers += f"In-Reply-To: {in_reply_to}\r\nReferences: {in_reply_to}\r\n"
        return (headers + "\r\n" + "archived body\r\n").encode()

    def test_persists_new_archived_message_and_returns_hashes(self, monkeypatch, tmp_db, fake_credentials):
        fake = MagicMock()
        fake.list.return_value = ("OK", [b'(\\All) "/" "[Gmail]/All Mail"'])
        fake.select.return_value = ("OK", [b""])
        fake.uid.side_effect = [
            ("OK", [b"1"]),
            ("OK", [(b"UID 1", self._make_raw("<arch1@e.com>"))]),
            ("OK", [b"1"]),
            ("OK", [(b"UID 1", self._make_raw("<arch1@e.com>"))]),
        ]
        fake_imap_session(monkeypatch, imap_mod, fake)

        result = email_reader.sync_all_mail(db_path=tmp_db)
        assert result["synced"] == 1
        assert cache._hash_message_id("<arch1@e.com>") in result["hashes"]

        emails, total, _ = cache.search_emails(tmp_db)
        assert total == 1
        assert cache._hash_message_id("<arch1@e.com>") in [e["message_id_hash"] for e in emails]

    def test_skips_already_cached_message(self, monkeypatch, tmp_db, fake_credentials):
        from src.tests._helpers import save_fetched

        save_fetched(
            {
                "message_id": "<dup@e.com>",
                "from": "them@e.com",
                "subject": "Already here",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "x",
            },
            tmp_db,
        )
        before = cache.get_total_count(tmp_db)

        fake = MagicMock()
        fake.list.return_value = ("OK", [b'(\\All) "/" "[Gmail]/All Mail"'])
        fake.select.return_value = ("OK", [b""])
        fake.uid.side_effect = [
            ("OK", [b"1"]),  # search
            ("OK", [(b"UID 1", self._make_raw("<dup@e.com>", subject="Already here"))]),  # header
        ]
        fake_imap_session(monkeypatch, imap_mod, fake)

        result = email_reader.sync_all_mail(db_path=tmp_db)
        assert result["synced"] == 0
        assert result["skipped"] == 1
        assert cache.get_total_count(tmp_db) == before  # no duplicate row

    def test_archived_message_protected_from_inbox_reconcile(self, tmp_db):
        from src.tests._helpers import save_fetched

        save_fetched(
            {
                "message_id": "<arch@e.com>",
                "from": "them@e.com",
                "subject": "Archived",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "x",
            },
            tmp_db,
        )
        arch_hash = cache._hash_message_id("<arch@e.com>")
        inbox_hashes = {cache._hash_message_id("<inbox@e.com>")}
        deleted = cache.reconcile_inbox(tmp_db, inbox_hashes, protected_hashes={arch_hash})
        assert deleted == 0
        assert cache.get_email_by_hash(tmp_db, arch_hash) is not None

    def test_no_credentials_returns_zeros(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        result = email_reader.sync_all_mail(db_path=tmp_db)
        assert result["synced"] == 0
        assert result["hashes"] == set()

    def test_empty_folder_returns_zeros(self, monkeypatch, tmp_db, fake_credentials):
        fake = MagicMock()
        fake.list.return_value = ("OK", [b'(\\All) "/" "[Gmail]/All Mail"'])
        fake.select.return_value = ("OK", [b""])
        fake.uid.return_value = ("OK", [b""])  # search returns no uids
        fake_imap_session(monkeypatch, imap_mod, fake)
        result = email_reader.sync_all_mail(db_path=tmp_db)
        assert result["synced"] == 0
        assert result["hashes"] == set()
