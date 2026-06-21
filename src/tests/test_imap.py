import imaplib
from unittest.mock import MagicMock, patch

import pytest

from src.scripts import cache, email_reader
from src.scripts.email_reader import imap as imap_mod


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
        assert result[0]["thread_id"] is not None


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

    def test_returns_empty_on_non_ok_status(self, fake_mail):
        fake_mail.uid.return_value = ("BAD", [])
        assert email_reader._fetch_headers_bulk(fake_mail, [b"1"]) == []

    def test_skips_non_tuple_items(self, fake_mail):
        fake_mail.uid.return_value = ("OK", [b"standalone", (b"UID 1", b"Message-ID: <m@e.com>\r\n\r\n")])
        result = email_reader._fetch_headers_bulk(fake_mail, [b"1"])
        assert len(result) == 1


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
        monkeypatch.setattr(imap_mod, "_reconnect", lambda c, db_path=None: new_mail)
        monkeypatch.setattr(imap_mod, "_parse_fetched_email", lambda data: [])
        result, returned_mail = single(mail, (b"1", "<m@e.com>"))
        assert returned_mail is new_mail

    def test_single_gives_up_after_reconnect_failure(self, monkeypatch):
        _, single = email_reader._make_body_fetchers("/db")
        mail = MagicMock()
        mail.uid.side_effect = imaplib.IMAP4.abort("aborted")
        monkeypatch.setattr(imap_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(imap_mod, "_reconnect", lambda c, db_path=None: (_ for _ in ()).throw(RuntimeError("nope")))
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
        monkeypatch.setattr(imap_mod, "_reconnect", lambda c, db_path=None: new_conn)

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
            imap_mod, "_reconnect", lambda c, db_path=None: (_ for _ in ()).throw(RuntimeError("reconnect failed"))
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

        class FakeSession:
            def __enter__(self):
                return fake

            def __exit__(self, *a):
                return None

        monkeypatch.setattr(imap_mod, "imap_session", lambda db_path=None: FakeSession())
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


class TestDeleteEmail:
    def test_no_credentials_returns_error(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        result = email_reader.delete_email("<m@e.com>", db_path="/tmp/nonexistent.db")
        assert "error" in result

    def test_success_returns_deleted_true(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        cache.save_headers_batch([{"message_id": "<m@e.com>", "subject": "s", "date": "d"}], tmp_db)
        fake = MagicMock()

        class FakeSession:
            def __enter__(self):
                return fake

            def __exit__(self, *a):
                return None

        monkeypatch.setattr(imap_mod, "imap_session", lambda db_path=None: FakeSession())
        monkeypatch.setattr(imap_mod, "move_to_trash", lambda mail, mid: True)
        result = email_reader.delete_email("<m@e.com>", db_path=tmp_db)
        assert result == {"deleted": True, "message_id": "<m@e.com>"}
        assert cache.read_emails(tmp_db) == []

    def test_move_to_trash_failure_returns_deleted_false(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        fake = MagicMock()

        class FakeSession:
            def __enter__(self):
                return fake

            def __exit__(self, *a):
                return None

        monkeypatch.setattr(imap_mod, "imap_session", lambda db_path=None: FakeSession())
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
