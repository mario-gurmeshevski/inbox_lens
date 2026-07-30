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
        msg = smtp_mod.build_message(
            "me@e.com", ["alice@example.com"], "Lunch?", "Sure!", "reply", original_message_id="<orig@e.com>"
        )
        assert msg["Subject"] == "Re: Lunch?"
        assert msg["To"] == "alice@example.com"
        assert msg["From"] == "me@e.com"

    def test_reply_strips_existing_prefix(self):
        msg = smtp_mod.build_message(
            "me@e.com", ["alice@example.com"], "Re: Lunch?", "Sure!", "reply", original_message_id="<orig@e.com>"
        )
        assert msg["Subject"] == "Re: Lunch?"

    def test_reply_sets_threading_headers(self):
        msg = smtp_mod.build_message(
            "me@e.com", ["alice@example.com"], "Lunch?", "Sure!", "reply", original_message_id="<orig@e.com>"
        )
        assert msg["In-Reply-To"] == "<orig@e.com>"
        assert msg["References"] == "<orig@e.com>"

    def test_forward_subject_and_no_threading(self):
        msg = smtp_mod.build_message(
            "me@e.com", ["bob@example.com"], "Lunch?", "FYI", "forward", original_message_id="<orig@e.com>"
        )
        assert msg["Subject"] == "Fwd: Lunch?"
        assert msg["To"] == "bob@example.com"
        assert msg.get("In-Reply-To") is None
        assert msg.get("References") is None

    def test_missing_message_id_omits_threading(self):
        msg = smtp_mod.build_message("me@e.com", ["alice@example.com"], "Lunch?", "Sure!", "reply")
        assert msg.get("In-Reply-To") is None
        assert msg.get("References") is None

    def test_empty_subject_gets_placeholder(self):
        msg = smtp_mod.build_message("me@e.com", ["x@y.com"], "", "Body", "reply")
        assert msg["Subject"] == "Re: (no subject)"

    def test_new_mode_does_not_prefix_subject(self):
        msg = smtp_mod.build_message("me@e.com", ["x@y.com"], "test", "Body", "new")
        assert msg["Subject"] == "test"

    def test_new_mode_passes_subject_through_unchanged(self):
        msg = smtp_mod.build_message("me@e.com", ["x@y.com"], "Re: Lunch?", "Body", "new")
        assert msg["Subject"] == "Re: Lunch?"

    def test_new_mode_empty_subject_stays_empty(self):
        msg = smtp_mod.build_message("me@e.com", ["x@y.com"], "", "Body", "new")
        assert msg["Subject"] == ""

    def test_new_mode_has_no_threading_headers(self):
        msg = smtp_mod.build_message("me@e.com", ["x@y.com"], "Hi", "Body", "new", original_message_id="<orig@e.com>")
        assert msg.get("In-Reply-To") is None
        assert msg.get("References") is None

    def test_plain_when_no_html(self):
        msg = smtp_mod.build_message("me@e.com", ["x@y.com"], "Hi", "Body", "reply")
        assert msg.get_content_type() == "text/plain"

    def test_multipart_alternative_when_html(self):
        msg = smtp_mod.build_message(
            "me@e.com", ["x@y.com"], "Hi", "**bold**", "reply", html="<p><strong>bold</strong></p>"
        )
        assert msg.get_content_type() == "multipart/alternative"
        parts = msg.get_payload()
        assert len(parts) == 2
        assert parts[0].get_content_type() == "text/plain"
        assert parts[1].get_content_type() == "text/html"
        assert parts[0].get_payload(decode=True).decode("utf-8") == "**bold**"
        assert "<strong>bold</strong>" in parts[1].get_payload(decode=True).decode("utf-8")

    def test_keeps_existing_prefix_as_typed(self):
        msg = smtp_mod.build_message(
            "me@e.com", ["alice@example.com"], "Re: Re: Lunch?", "Sure!", "reply", original_message_id="<orig@e.com>"
        )
        assert msg["Subject"] == "Re: Re: Lunch?"

    def test_keeps_mixed_prefix_as_typed_forward(self):
        msg = smtp_mod.build_message(
            "me@e.com", ["bob@example.com"], "Fwd: Re: Lunch?", "FYI", "forward", original_message_id="<orig@e.com>"
        )
        assert msg["Subject"] == "Fwd: Re: Lunch?"

    def test_rejects_newlines_in_subject(self):
        with pytest.raises(ValueError):
            smtp_mod.build_message("me@e.com", ["x@y.com"], "hi\nBcc: evil@x.com", "body", "reply")

    def test_rejects_bare_cr_in_subject(self):
        with pytest.raises(ValueError):
            smtp_mod.build_message("me@e.com", ["x@y.com"], "hi\rextra", "body", "reply")

    def test_rejects_newlines_in_to_addr(self):
        with pytest.raises(ValueError):
            smtp_mod.build_message("me@e.com", ["x@y.com\r\nBcc: evil@x.com"], "hi", "body", "reply")

    def test_rejects_newlines_in_cc_addr(self):
        with pytest.raises(ValueError):
            smtp_mod.build_message(
                "me@e.com", ["x@y.com"], "hi", "body", "reply", cc_addrs=["a@b.com\nBcc: evil@x.com"]
            )

    def test_rejects_newlines_in_bcc_addr(self):
        with pytest.raises(ValueError):
            smtp_mod.build_message("me@e.com", ["x@y.com"], "hi", "body", "reply", bcc_addrs=["a@b.com\nX: y"])

    def test_cc_header_set_from_cc_addrs(self):
        msg = smtp_mod.build_message("me@e.com", ["to@x.com"], "Hi", "Body", "reply", cc_addrs=["a@x.com", "b@x.com"])
        assert msg["Cc"] == "a@x.com, b@x.com"

    def test_cc_header_omitted_when_empty(self):
        msg = smtp_mod.build_message("me@e.com", ["to@x.com"], "Hi", "Body", "reply")
        assert msg.get("Cc") is None

    def test_bcc_header_never_set(self):
        msg = smtp_mod.build_message("me@e.com", ["to@x.com"], "Hi", "Body", "reply", bcc_addrs=["secret@x.com"])
        assert msg.get("Bcc") is None

    def test_to_header_joins_multiple(self):
        msg = smtp_mod.build_message("me@e.com", ["a@x.com", "b@x.com"], "Hi", "Body", "reply")
        assert msg["To"] == "a@x.com, b@x.com"

    def test_allows_newlines_in_body(self):
        msg = smtp_mod.build_message("me@e.com", ["x@y.com"], "Hi", "line1\nline2", "reply")
        assert "line1\nline2" in msg.get_payload(decode=True).decode("utf-8")


class TestSendMessage:
    def test_sends_reply(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("me@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message(
                ["alice@example.com"],
                [],
                [],
                "Lunch?",
                "Sure!",
                None,
                "reply",
                original_message_id="<orig@e.com>",
                db_path="/db",
            )
        fake.login.assert_called_once_with("me@e.com", "secret")
        assert fake.sendmail.call_count == 1
        args = fake.sendmail.call_args.args
        assert args[0] == "me@e.com"
        assert args[1] == ["alice@example.com"]
        assert "Re: Lunch?" in args[2]
        assert "In-Reply-To: <orig@e.com>" in args[2]
        fake.quit.assert_called_once()

    def test_envelope_includes_cc_and_bcc(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("me@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message(
                ["to@x.com"],
                ["cc@x.com"],
                ["bcc@x.com"],
                "Hi",
                "Body",
                "<p>Body</p>",
                "reply",
                db_path="/db",
            )
        args = fake.sendmail.call_args.args
        assert args[0] == "me@e.com"
        assert args[1] == ["to@x.com", "cc@x.com", "bcc@x.com"]

    def test_bcc_absent_from_message_body(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("me@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message(["to@x.com"], [], ["bcc@x.com"], "Hi", "Body", None, "reply", db_path="/db")
        raw = fake.sendmail.call_args.args[2]
        assert "bcc@x.com" not in raw.lower()

    def test_sends_forward(self, monkeypatch):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("me@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message(
                ["bob@example.com"],
                [],
                [],
                "Lunch?",
                "FYI",
                None,
                "forward",
                original_message_id="<orig@e.com>",
                db_path="/db",
            )
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
                smtp_mod.send_message(["x@y.com"], [], [], "Hi", "Body", None, "reply", db_path="/db")

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
                ["alice@example.com"],
                [],
                [],
                "Re: Lunch?",
                "Sure!",
                None,
                "reply",
                original_message_id="<orig@e.com>",
                thread_id=parent_thread,
                db_path=tmp_db,
            )
        with cache._connect(tmp_db) as conn:
            rows = conn.execute(
                "SELECT message_id, sender, subject, is_sent, thread_id, in_reply_to, body, cc "
                "FROM emails WHERE is_sent = 1"
            ).fetchall()
        assert len(rows) == 1
        sent = rows[0]
        assert sent["in_reply_to"] == "<orig@e.com>"
        assert sent["thread_id"] == parent_thread
        assert sent["sender"] == "user@e.com"
        assert sent["subject"] == "Re: Lunch?"
        assert sent["body"] == "Sure!"
        assert sent["cc"] is None or sent["cc"] == ""
        parent_hash = cache._hash_message_id("<orig@e.com>")
        conv = cache.get_conversation(tmp_db, parent_hash)
        assert any(c["message_id"] == sent["message_id"] for c in conv)

    def test_reply_with_cc_persists_cc_column(self, tmp_db, fake_credentials):
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message(
                ["to@x.com"],
                ["cc1@x.com", "cc2@x.com"],
                [],
                "Hi",
                "Body",
                None,
                "reply",
                db_path=tmp_db,
            )
        with cache._connect(tmp_db) as conn:
            row = conn.execute("SELECT cc FROM emails WHERE is_sent = 1").fetchone()
        assert row["cc"] == "cc1@x.com, cc2@x.com"

    def test_forward_persists_to_cache_without_in_reply_to(self, tmp_db, fake_credentials):
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            smtp_mod.send_message(
                ["bob@example.com"],
                [],
                [],
                "Fwd: Lunch?",
                "FYI",
                None,
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
            smtp_mod.send_message(["x@y.com"], [], [], "Hi", "Body", None, "reply", db_path=tmp_db)
        assert fake.sendmail.call_count == 1


class TestPackageReexport:
    def test_symbols_available_on_package(self):
        from src.scripts import email_reader

        for name in (
            "smtp_session",
            "build_message",
            "send_message",
            "test_smtp_connection",
            "SECURITY_SSL",
            "SECURITY_STARTTLS",
            "SECURITY_NONE",
        ):
            assert hasattr(email_reader, name), f"email_reader.{name} missing"


class TestSmtpConfig:
    def test_defaults_when_no_settings(self, tmp_db):
        assert smtp_mod._smtp_config(tmp_db) == ("smtp.gmail.com", 465, smtp_mod.SECURITY_SSL)

    def test_settings_override_env_defaults(self, tmp_db):
        cache.save_setting("smtp_server", "mail.example.com", tmp_db)
        cache.save_setting("smtp_port", "587", tmp_db)
        cache.save_setting("smtp_security", "STARTTLS", tmp_db)
        assert smtp_mod._smtp_config(tmp_db) == ("mail.example.com", 587, smtp_mod.SECURITY_STARTTLS)

    def test_invalid_security_falls_back_to_ssl(self, tmp_db):
        cache.save_setting("smtp_security", "bogus", tmp_db)
        assert smtp_mod._smtp_config(tmp_db)[2] == smtp_mod.SECURITY_SSL

    def test_invalid_port_in_settings_falls_back(self, tmp_db):
        cache.save_setting("smtp_port", "not-a-port", tmp_db)
        assert smtp_mod._smtp_config(tmp_db)[1] == 465

    def test_unreadable_db_falls_back_to_env_defaults(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db gone")

        monkeypatch.setattr(cache, "get_setting", boom)
        server, port, security = smtp_mod._smtp_config("/nope/db")
        assert server == "smtp.gmail.com"
        assert port == 465
        assert security == smtp_mod.SECURITY_SSL


class TestSmtpSessionSecurity:
    def test_uses_settings_over_env(self, monkeypatch, tmp_db):
        cache.save_setting("smtp_server", "mail.example.com", tmp_db)
        cache.save_setting("smtp_port", "587", tmp_db)
        cache.save_setting("smtp_security", "starttls", tmp_db)
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP", return_value=fake) as mock_ctor:
            with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake) as mock_ssl:
                with smtp_mod.smtp_session(tmp_db) as conn:
                    assert conn is fake
        mock_ssl.assert_not_called()
        mock_ctor.assert_called_once_with("mail.example.com", 587, timeout=smtp_mod.SMTP_TIMEOUT)
        fake.starttls.assert_called_once()
        fake.login.assert_called_once_with("u@e.com", "secret")
        fake.quit.assert_called_once()


class TestOpenSmtp:
    def test_ssl_mode_uses_smtp_ssl(self):
        fake = MagicMock(name="ssl")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake) as ctor:
            assert smtp_mod._open_smtp("h", 465, smtp_mod.SECURITY_SSL) is fake
        ctor.assert_called_once_with("h", 465, timeout=smtp_mod.SMTP_TIMEOUT)

    def test_none_mode_uses_plain_smtp_no_starttls(self):
        fake = MagicMock(name="plain")
        with patch.object(smtp_mod.smtplib, "SMTP", return_value=fake) as ctor:
            assert smtp_mod._open_smtp("h", 25, smtp_mod.SECURITY_NONE) is fake
        ctor.assert_called_once_with("h", 25, timeout=smtp_mod.SMTP_TIMEOUT)
        fake.starttls.assert_not_called()

    def test_starttls_mode_upgrades(self):
        fake = MagicMock(name="starttls")
        with patch.object(smtp_mod.smtplib, "SMTP", return_value=fake) as ctor:
            assert smtp_mod._open_smtp("h", 587, smtp_mod.SECURITY_STARTTLS) is fake
        ctor.assert_called_once_with("h", 587, timeout=smtp_mod.SMTP_TIMEOUT)
        fake.starttls.assert_called_once()


class TestTestSmtpConnection:
    def test_success_on_login(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            result = smtp_mod.test_smtp_connection("smtp.x.com", "465", "ssl", db_path=tmp_db)
        assert result == {
            "success": True,
            "server": "smtp.x.com:465",
            "security": "ssl",
        }
        fake.login.assert_called_once_with("u@e.com", "secret")
        fake.quit.assert_called_once()

    def test_auth_failure_message(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u@e.com", "secret"))
        fake = MagicMock(name="smtp")
        fake.login.side_effect = smtplib.SMTPAuthenticationError(535, b"auth failed")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            result = smtp_mod.test_smtp_connection("smtp.x.com", 465, "ssl", db_path=tmp_db)
        assert result["success"] is False
        assert "Authentication failed" in result["error"]

    def test_generic_error_returns_sanitized_message(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u@e.com", "secret"))
        fake = MagicMock(name="smtp")
        fake.login.side_effect = OSError("connection refused")
        with patch.object(smtp_mod.smtplib, "SMTP_SSL", return_value=fake):
            result = smtp_mod.test_smtp_connection("smtp.x.com", 465, "ssl", db_path=tmp_db)
        assert result["success"] is False
        assert "connection refused" not in result["error"]
        assert "Could not reach the SMTP server" in result["error"]

    def test_normalizes_security_and_port(self, monkeypatch, tmp_db):
        monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("u@e.com", "secret"))
        fake = MagicMock(name="smtp")
        with patch.object(smtp_mod.smtplib, "SMTP", return_value=fake) as ctor:
            result = smtp_mod.test_smtp_connection("smtp.x.com", "587", "STARTTLS", db_path=tmp_db)
        assert result["success"] is True
        assert result["security"] == "starttls"
        ctor.assert_called_once_with("smtp.x.com", 587, timeout=smtp_mod.SMTP_TIMEOUT)
        fake.starttls.assert_called_once()
        fake.quit.assert_called_once()
