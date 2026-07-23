import smtplib
from unittest.mock import MagicMock, patch

import pytest

from src.scripts import cache
from src.scripts.email_reader import smtp as smtp_mod


class TestSmtpConstants:
    def test_defaults(self):
        assert smtp_mod.SMTP_SERVER == "smtp.gmail.com"
        assert smtp_mod.SMTP_PORT == 465

    def test_port_guard_falls_back_on_invalid(self, caplog):
        with caplog.at_level("WARNING"):
            assert smtp_mod._parse_port("not-a-number") == 465
        assert "Invalid SMTP_PORT" in caplog.text

    def test_port_guard_accepts_numeric_string(self):
        assert smtp_mod._parse_port("587") == 587
        assert smtp_mod._parse_port(25) == 25


class TestSmtpSession:
    def test_logs_in_with_cached_credentials(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake) as mock_ctor:
            with smtp_mod.smtp_session("/db") as conn:
                assert conn is fake
        mock_ctor.assert_called_once_with("smtp.gmail.com", 465, timeout=smtp_mod.SMTP_TIMEOUT)
        fake.login.assert_called_once_with("u@e.com", "secret")
        fake.quit.assert_called_once()

    def test_quit_called_even_on_error(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u@e.com", "secret"))
        fake = MagicMock(name="smtp")
        fake.login.side_effect = Exception("boom")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            with pytest.raises(Exception):
                with smtp_mod.smtp_session("/db"):
                    pass
        fake.quit.assert_called_once()


class TestBuildMessage:
    def test_reply_subject_prefix(self):
        msg = smtp_mod.build_message("me@e.com", "alice@example.com", "Lunch?", "Sure!", "reply", "<orig@e.com>")
        assert msg["Subject"] == "Re: Lunch?"
        assert msg["To"] == "alice@example.com"
        assert msg["From"] == "me@e.com"

    def test_reply_strips_existing_prefix(self):
        msg = smtp_mod.build_message("me@e.com", "alice@example.com", "Re: Lunch?", "Sure!", "reply", "<orig@e.com>")
        assert msg["Subject"] == "Re: Lunch?"

    def test_reply_sets_threading_headers(self):
        msg = smtp_mod.build_message("me@e.com", "alice@example.com", "Lunch?", "Sure!", "reply", "<orig@e.com>")
        assert msg["In-Reply-To"] == "<orig@e.com>"
        assert msg["References"] == "<orig@e.com>"

    def test_forward_subject_and_no_threading(self):
        msg = smtp_mod.build_message("me@e.com", "bob@example.com", "Lunch?", "FYI", "forward", "<orig@e.com>")
        assert msg["Subject"] == "Fwd: Lunch?"
        assert msg["To"] == "bob@example.com"
        assert msg.get("In-Reply-To") is None
        assert msg.get("References") is None

    def test_body_passes_through_unchanged(self):
        msg = smtp_mod.build_message("me@e.com", "x@y.com", "Hi", "Line 1\nLine 2", "reply", None)
        payload = msg.get_payload(decode=True).decode("utf-8")
        assert payload == "Line 1\nLine 2"

    def test_missing_message_id_omits_threading(self):
        msg = smtp_mod.build_message("me@e.com", "alice@example.com", "Lunch?", "Sure!", "reply", None)
        assert msg.get("In-Reply-To") is None
        assert msg.get("References") is None

    def test_empty_subject_gets_placeholder(self):
        msg = smtp_mod.build_message("me@e.com", "x@y.com", "", "Body", "reply", None)
        assert msg["Subject"] == "Re: (no subject)"

    def test_content_type_is_plain_utf8(self):
        msg = smtp_mod.build_message("me@e.com", "x@y.com", "Hi", "Body", "reply", None)
        assert msg.get_content_type() == "text/plain"
        assert (msg.get_content_charset() or "utf-8").lower() == "utf-8"

    def test_keeps_existing_prefix_as_typed(self):
        msg = smtp_mod.build_message(
            "me@e.com", "alice@example.com", "Re: Re: Lunch?", "Sure!", "reply", "<orig@e.com>"
        )
        assert msg["Subject"] == "Re: Re: Lunch?"

    def test_keeps_mixed_prefix_as_typed_forward(self):
        msg = smtp_mod.build_message("me@e.com", "bob@example.com", "Fwd: Re: Lunch?", "FYI", "forward", "<orig@e.com>")
        assert msg["Subject"] == "Fwd: Re: Lunch?"

    def test_rejects_newlines_in_subject(self):
        with pytest.raises(ValueError):
            smtp_mod.build_message("me@e.com", "x@y.com", "hi\nBcc: evil@x.com", "body", "reply")

    def test_rejects_bare_cr_in_subject(self):
        with pytest.raises(ValueError):
            smtp_mod.build_message("me@e.com", "x@y.com", "hi\rextra", "body", "reply")

    def test_rejects_newlines_in_to_addr(self):
        with pytest.raises(ValueError):
            smtp_mod.build_message("me@e.com", "x@y.com\r\nBcc: evil@x.com", "hi", "body", "reply")

    def test_allows_newlines_in_body(self):
        msg = smtp_mod.build_message("me@e.com", "x@y.com", "Hi", "line1\nline2", "reply")
        assert "line1\nline2" in msg.get_payload(decode=True).decode("utf-8")


class TestSendMessage:
    def test_sends_reply(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("me@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message("alice@example.com", "Lunch?", "Sure!", "reply", "<orig@e.com>", db_path="/db")
        fake.login.assert_called_once_with("me@e.com", "secret")
        assert fake.sendmail.call_count == 1
        args = fake.sendmail.call_args.args
        assert args[0] == "me@e.com"
        assert args[1] == ["alice@example.com"]
        assert "Re: Lunch?" in args[2]
        assert "In-Reply-To: <orig@e.com>" in args[2]
        fake.quit.assert_called_once()

    def test_sends_forward(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("me@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message("bob@example.com", "Lunch?", "FYI", "forward", "<orig@e.com>", db_path="/db")
        args = fake.sendmail.call_args.args
        assert args[1] == ["bob@example.com"]
        assert "Fwd: Lunch?" in args[2]
        assert "In-Reply-To" not in args[2]

    def test_propagates_send_error(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("me@e.com", "secret"))
        fake = MagicMock(name="smtp")
        fake.sendmail.side_effect = smtplib.SMTPRecipientsRefused({"x@y.com": (550, b"no")})
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            with pytest.raises(smtplib.SMTPRecipientsRefused):
                smtp_mod.send_message("x@y.com", "Hi", "Body", "reply", None, db_path="/db")

    def test_reply_persists_to_cache_with_thread_id(self, tmp_db, fake_credentials):
        parent_thread = cache._hash_message_id("parent-gm-thrid")
        cache.save_headers_batch(
            [
                {
                    "message_id": "<orig@e.com>",
                    "from": "alice@e.com",
                    "subject": "Lunch?",
                    "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                    "thread_id": parent_thread,
                }
            ],
            tmp_db,
        )

        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message(
                "alice@example.com",
                "Re: Lunch?",
                "Sure!",
                "reply",
                original_message_id="<orig@e.com>",
                thread_id=parent_thread,
                db_path=tmp_db,
            )
        with cache._connect(tmp_db) as conn:
            rows = conn.execute(
                "SELECT message_id, sender, subject, is_sent, thread_id, in_reply_to, body "
                "FROM emails WHERE is_sent = 1"
            ).fetchall()
        assert len(rows) == 1
        sent = rows[0]
        assert sent["in_reply_to"] == "<orig@e.com>"
        assert sent["thread_id"] == parent_thread
        assert sent["sender"] == "user@e.com"
        assert sent["subject"] == "Re: Lunch?"
        assert sent["body"] == "Sure!"
        parent_hash = cache._hash_message_id("<orig@e.com>")
        conv = cache.get_conversation(tmp_db, parent_hash)
        assert any(c["message_id"] == sent["message_id"] for c in conv)

    def test_forward_persists_to_cache_without_in_reply_to(self, tmp_db, fake_credentials):
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message(
                "bob@example.com",
                "Fwd: Lunch?",
                "FYI",
                "forward",
                original_message_id="<orig@e.com>",
                db_path=tmp_db,
            )

        with cache._connect(tmp_db) as conn:
            rows = conn.execute(
                "SELECT message_id, is_sent, thread_id, in_reply_to FROM emails WHERE is_sent = 1"
            ).fetchall()
        assert len(rows) == 1
        sent = rows[0]
        assert sent["in_reply_to"] == "" or sent["in_reply_to"] is None
        assert sent["thread_id"] == cache._hash_message_id(sent["message_id"])

    def test_persistence_failure_does_not_invalidate_send(self, tmp_db, fake_credentials, monkeypatch):
        fake = MagicMock(name="smtp")
        monkeypatch.setattr(cache, "save_headers_batch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db boom")))
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message("x@y.com", "Hi", "Body", "reply", db_path=tmp_db)
        assert fake.sendmail.call_count == 1


class TestPackageReexport:
    def test_symbols_available_on_package(self):
        from src.scripts import email_reader

        for name in ("smtp_session", "build_message", "send_message"):
            assert hasattr(email_reader, name), f"email_reader.{name} missing"
