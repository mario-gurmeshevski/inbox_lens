import json
from unittest.mock import patch

import pytest

from src.scripts import cache, email_reader, web
from src.tests._helpers import _raise, save_fetched as _save_fetched, save_fetched_batch as _save_fetched_batch

_GM_THRID = "1809095669921875987"
_TID = cache._hash_message_id(_GM_THRID)


class TestFormatDate:
    def test_valid_date(self):
        result = web._format_date("Mon, 01 Jan 2024 10:00:00 +0000")
        assert "2024" in result
        assert "Jan" in result

    def test_empty_string(self):
        assert web._format_date("") == ""

    def test_none_input(self):
        assert web._format_date(None) == ""

    def test_invalid_date_returns_raw(self):
        result = web._format_date("not-a-date")
        assert result == "not-a-date"

    def test_converts_to_selected_timezone(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "tz_test.db")
        cache.init_db(db_path)
        cache.save_setting("timezone", "Asia/Tokyo", db_path)
        monkeypatch.setattr(web, "DB_PATH", db_path)
        result = web._format_date("Fri, 26 Jun 2026 05:02:00 +0000")
        assert "14:02" in result

    def test_defaults_to_device_timezone(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "tz_test_default.db")
        cache.init_db(db_path)
        monkeypatch.setattr(web, "DB_PATH", db_path)
        monkeypatch.setattr(web, "_LOCAL_TIMEZONE", "Asia/Tokyo")
        result = web._format_date("Fri, 26 Jun 2026 05:02:00 +0000")
        assert "14:02" in result


class TestMarkdownFilter:
    def test_bold_renders_strong(self):
        assert "<strong>bold</strong>" in str(web._render_markdown("**bold**"))

    def test_italics_renders_em(self):
        assert "<em>it</em>" in str(web._render_markdown("*it*"))

    def test_unordered_list(self):
        out = str(web._render_markdown("- a\n- b"))
        assert "<ul>" in out and "<li>a</li>" in out and "<li>b</li>" in out

    def test_ordered_list(self):
        out = str(web._render_markdown("1. a\n2. b"))
        assert "<ol>" in out and "<li>a</li>" in out

    def test_link_renders_anchor(self):
        out = str(web._render_markdown("[site](https://example.com)"))
        assert '<a href="https://example.com"' in out
        assert "site</a>" in out

    def test_link_opens_in_new_tab_safely(self):
        out = str(web._render_markdown("[site](https://example.com)"))
        assert 'target="_blank"' in out
        assert "noopener" in out and "noreferrer" in out

    def test_plain_text_wraps_in_paragraph(self):
        out = str(web._render_markdown("just plain text"))
        assert "<p>just plain text</p>" in out

    def test_plain_text_preserves_content(self):
        out = str(web._render_markdown("just plain text"))
        assert "just plain text" in out

    def test_inline_code(self):
        out = str(web._render_markdown("use `npm` here"))
        assert "<code>npm</code>" in out

    def test_fenced_code_block(self):
        out = str(web._render_markdown("```\nprint(1)\n```"))
        assert "<pre>" in out and "<code>" in out and "print(1)" in out

    def test_blockquote(self):
        out = str(web._render_markdown("> quoted text"))
        assert "<blockquote>" in out and "quoted text" in out

    def test_strips_script_tag(self):
        out = str(web._render_markdown("<script>alert(1)</script>safe"))
        assert "<script" not in out
        assert "safe" in out

    def test_strips_javascript_url_scheme(self):
        out = str(web._render_markdown("[xss](javascript:alert(1))"))
        assert "javascript:" not in out
        assert "<script" not in out

    def test_strips_event_handler_attribute(self):
        out = str(web._render_markdown('<img src="x" onerror="alert(1)">'))
        assert "onerror" not in out
        assert "<script" not in out

    def test_strips_iframe(self):
        out = str(web._render_markdown('<iframe src="https://evil"></iframe>'))
        assert "<iframe" not in out

    def test_returns_markup_safe(self):
        from markupsafe import Markup

        assert isinstance(web._render_markdown("**x**"), Markup)

    def test_empty_string_returns_empty(self):
        assert str(web._render_markdown("")) == ""

    def test_none_returns_empty(self):
        assert str(web._render_markdown(None)) == ""


class TestWebEndpoints:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "test_web.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("test@test.com", "testpass", self.db_path)

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def _seed_emails(self, n=5):
        emails = [
            {
                "message_id": f"<web{i}@e.com>",
                "from": f"sender{i}@e.com",
                "subject": f"Web subject {i}",
                "date": f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
                "body": f"Body {i}",
                "_category": "7" if i % 2 == 0 else "3",
            }
            for i in range(n)
        ]
        _save_fetched_batch(emails, self.db_path)
        return emails

    def test_dashboard_returns_200(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/")
        assert resp.status_code == 200

    def test_dashboard_includes_counts(self):
        self._seed_emails(3)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/")
        assert resp.status_code == 200

    def test_dashboard_total_matches_emails_tab(self):
        self._seed_emails(3)
        _save_fetched(
            {
                "message_id": "<sent@e.com>",
                "from": "me@e.com",
                "subject": "Re: sent",
                "date": "Tue, 09 Jan 2024 10:00:00 +0000",
                "body": "x",
                "in_reply_to": "<web0@e.com>",
            },
            self.db_path,
        )
        cache.mark_sent([cache._hash_message_id("<sent@e.com>")], self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            dash = client.get("/")
            elist = client.get("/emails")
        assert dash.status_code == 200
        assert elist.status_code == 200
        _, list_total, _ = cache.search_emails(self.db_path)
        dash_total = cache.get_list_count(self.db_path)
        assert dash_total == list_total == 4

    def test_emails_list_returns_200(self):
        self._seed_emails(3)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails")
        assert resp.status_code == 200

    def test_emails_list_with_status_filter(self):
        self._seed_emails(3)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails?status=fetched")
        assert resp.status_code == 200

    def test_emails_list_with_search(self):
        self._seed_emails(3)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails?search=Web+subject+0")
        assert resp.status_code == 200

    def test_emails_list_with_pagination(self):
        self._seed_emails(10)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails?page=2")
        assert resp.status_code == 200

    def test_emails_list_includes_sent_replies_in_conversation(self):
        emails = self._seed_emails(2)
        sent_reply = {
            "message_id": "<sent-reply@e.com>",
            "from": "me@e.com",
            "subject": "Re: Web subject 0",
            "date": "Tue, 09 Jan 2024 10:00:00 +0000",
            "body": "my reply",
            "in_reply_to": emails[0]["message_id"],
        }
        _save_fetched(sent_reply, self.db_path)
        cache.mark_sent([cache._hash_message_id(sent_reply["message_id"])], self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails")
        assert resp.status_code == 200
        assert "Web subject 0" in resp.text
        assert "Web subject 1" in resp.text

    def test_sent_view_shows_only_sent(self):
        self._seed_emails(2)  # inbox mails
        sent = {
            "message_id": "<sent-only@e.com>",
            "from": "me@e.com",
            "subject": "My sent mail",
            "date": "Tue, 09 Jan 2024 10:00:00 +0000",
            "body": "outgoing",
        }
        _save_fetched(sent, self.db_path)
        cache.mark_sent([cache._hash_message_id(sent["message_id"])], self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails/sent")
        assert resp.status_code == 200
        assert "Sent" in resp.text  # heading
        assert "My sent mail" in resp.text
        assert "Web subject 0" not in resp.text  # inbox mail excluded
        assert "Web subject 1" not in resp.text

    def test_sent_view_empty_when_no_sent(self):
        self._seed_emails(2)  # inbox only
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails/sent")
        assert resp.status_code == 200
        assert "Web subject 0" not in resp.text

    def test_sent_nav_link_present(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails")
        assert 'href="/emails/sent"' in resp.text
        assert "folder-dropdown" in resp.text
        assert "Sent" in resp.text

    def test_inbox_hides_sent_by_default(self):
        self._seed_emails(1)
        sent = {
            "message_id": "<sent-hide@e.com>",
            "from": "me@e.com",
            "subject": "Outgoing only",
            "date": "Tue, 09 Jan 2024 10:00:00 +0000",
            "body": "x",
            "thread_id": cache._hash_message_id("<web0@e.com>"),
        }
        _save_fetched(sent, self.db_path)
        cache.mark_sent([cache._hash_message_id(sent["message_id"])], self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails")
        assert resp.status_code == 200
        assert "Outgoing only" not in resp.text

    def test_inbox_shows_sent_when_setting_enabled(self):
        self._seed_emails(1)
        sent = {
            "message_id": "<sent-show@e.com>",
            "from": "me@e.com",
            "subject": "Outgoing visible",
            "date": "Tue, 09 Jan 2024 10:00:00 +0000",
            "body": "x",
            "thread_id": cache._hash_message_id("<web0@e.com>"),
        }
        _save_fetched(sent, self.db_path)
        cache.mark_sent([cache._hash_message_id(sent["message_id"])], self.db_path)
        cache.save_setting("show_sent_in_inbox", "1", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails")
        assert resp.status_code == 200
        assert "Outgoing visible" in resp.text

    def test_sent_view_shows_all_sent_even_same_thread(self):
        parent = self._seed_emails(1)[0]
        for i, mid in enumerate(("<r1@e.com>", "<r2@e.com>"), start=1):
            reply = {
                "message_id": mid,
                "from": "me@e.com",
                "subject": f"Re: reply {i}",
                "date": f"Tue, {9 + i} Jan 2024 10:00:00 +0000",
                "body": "x",
                "in_reply_to": parent["message_id"],
                "thread_id": cache._hash_message_id(parent["message_id"]),
            }
            _save_fetched(reply, self.db_path)
            cache.mark_sent([cache._hash_message_id(mid)], self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails/sent")
        assert resp.status_code == 200
        assert "Re: reply 1" in resp.text
        assert "Re: reply 2" in resp.text

    def test_save_show_sent_in_inbox_setting(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/settings/show-sent-in-inbox", data={"show_sent_in_inbox": "true"})
        assert resp.status_code == 200
        assert cache.get_setting("show_sent_in_inbox", self.db_path) == "1"
        # Toggle off
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/settings/show-sent-in-inbox", data={"show_sent_in_inbox": "false"})
        assert cache.get_setting("show_sent_in_inbox", self.db_path) == ""

    def test_save_smtp_settings(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/settings/smtp",
                data={"smtp_server": "mail.example.com", "smtp_port": "587", "smtp_security": "starttls"},
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "success"
        assert cache.get_setting("smtp_server", self.db_path) == "mail.example.com"
        assert cache.get_setting("smtp_port", self.db_path) == "587"
        assert cache.get_setting("smtp_security", self.db_path) == "starttls"

    def test_save_smtp_settings_rejects_invalid_security(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/settings/smtp",
                data={"smtp_server": "h", "smtp_port": "465", "smtp_security": "bogus"},
            )
        assert resp.status_code == 204
        assert cache.get_setting("smtp_security", self.db_path) == "ssl"

    def test_settings_page_exposes_smtp_values(self):
        cache.save_setting("smtp_server", "mail.example.com", self.db_path)
        cache.save_setting("smtp_port", "587", self.db_path)
        cache.save_setting("smtp_security", "starttls", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert "mail.example.com" in resp.text
        assert 'value="starttls"' in resp.text

    def test_smtp_test_success_toast(self, monkeypatch):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            monkeypatch.setattr(
                email_reader,
                "test_smtp_connection",
                lambda server, port, security, db_path=None: {
                    "success": True,
                    "server": f"{server}:{port}",
                    "security": security,
                },
            )
            resp = client.post(
                "/settings/smtp/test",
                data={"smtp_server": "mail.example.com", "smtp_port": "465", "smtp_security": "ssl"},
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "success"
        assert "Connected to SMTP" in resp.headers.get("X-Toast", "")

    def test_smtp_test_failure_toast(self, monkeypatch):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            monkeypatch.setattr(
                email_reader,
                "test_smtp_connection",
                lambda server, port, security, db_path=None: {"success": False, "error": "boom"},
            )
            resp = client.post(
                "/settings/smtp/test",
                data={"smtp_server": "mail.example.com", "smtp_port": "465", "smtp_security": "ssl"},
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "error"
        assert resp.headers.get("X-Toast") == "boom"

    def test_smtp_test_requires_host(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/settings/smtp/test",
                data={"smtp_server": "", "smtp_port": "465", "smtp_security": "ssl"},
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "error"

    def test_smtp_test_cooldown(self, monkeypatch):
        monkeypatch.setattr(
            email_reader,
            "test_smtp_connection",
            lambda *a, **k: {"success": True, "server": "x", "security": "ssl"},
        )
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            first = client.post(
                "/settings/smtp/test",
                data={"smtp_server": "h", "smtp_port": "465", "smtp_security": "ssl"},
            )
            second = client.post(
                "/settings/smtp/test",
                data={"smtp_server": "h", "smtp_port": "465", "smtp_security": "ssl"},
            )
        assert first.headers.get("X-Toast-Tone") == "success"
        assert second.headers.get("X-Toast-Tone") == "warning"
        assert "wait" in second.headers.get("X-Toast", "").lower()

    def test_save_attachment_settings_persists_single_bytes_key(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/settings/attachments", data={"attachment_max_mb": "25"})
        assert resp.status_code == 204
        assert cache.get_setting("max_attachment_size", self.db_path) == str(25 * 1024 * 1024)
        assert cache.get_setting("max_attachment_size_mb", self.db_path) is None

    def test_save_attachment_settings_rejects_invalid_mb(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/settings/attachments", data={"attachment_max_mb": "999"})
        assert resp.status_code == 204
        assert cache.get_setting("max_attachment_size", self.db_path) == str(10 * 1024 * 1024)

    def test_setting_single_key_keeps_hint_and_enforcement_in_sync(self):
        cache.save_setting("max_attachment_size", str(15 * 1024 * 1024), self.db_path)
        hint = web._attachment_hint(self.db_path)
        assert "15 MB per file" in hint
        with pytest.raises(cache.AttachmentError, match="per-file limit"):
            cache.store_attachment_bytes(
                self.db_path,
                "sync-1",
                cache.OWNER_DRAFT,
                [
                    {
                        "filename": "big.bin",
                        "mime_type": "application/octet-stream",
                        "payload": b"x" * (16 * 1024 * 1024),
                    }
                ],
            )

    def test_settings_page_exposes_attachment_dropdown(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/settings")
        assert "attachment_max_mb" in resp.text
        assert "Max file size" in resp.text

    def test_email_detail_found(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get(f"/emails/{email_hash}")
        assert resp.status_code == 200

    def test_email_detail_not_found(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/emails/nonexistent")
        assert resp.status_code == 404

    def test_delete_email_returns_204_with_toast(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with patch.object(web, "DB_PATH", self.db_path), patch("src.scripts.email_reader.delete_email") as mock_del:
            mock_del.return_value = {"deleted": True, "message_id": "<web0@e.com>"}
            client = self._make_client()
            resp = client.post(f"/emails/{email_hash}/delete", follow_redirects=False)
        assert resp.status_code == 204
        assert "X-Toast" in resp.headers

    def test_delete_nonexistent_returns_error_toast(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/emails/nonexistent/delete", follow_redirects=False)
        assert resp.status_code == 204
        assert "X-Toast" in resp.headers
        assert "not found" in resp.headers["X-Toast"].lower()

    def test_delete_email_imap_failure_returns_error_toast(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with patch.object(web, "DB_PATH", self.db_path), patch("src.scripts.email_reader.delete_email") as mock_del:
            mock_del.return_value = {"deleted": False, "error": "IMAP: message not found in any folder"}
            client = self._make_client()
            resp = client.post(f"/emails/{email_hash}/delete", follow_redirects=False)
        assert resp.status_code == 204
        assert "X-Toast" in resp.headers
        assert "IMAP" in resp.headers["X-Toast"]
        assert "not found" in resp.headers["X-Toast"].lower()
        assert cache.get_email_by_hash(self.db_path, email_hash) is not None

    def test_toggle_read_returns_204_and_flips_state(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_defer") as mock_defer,
        ):
            client = self._make_client()
            resp = client.post(f"/emails/{email_hash}/toggle-read")
        assert resp.status_code == 204
        assert "X-Toast" in resp.headers
        assert cache.get_email_by_hash(self.db_path, email_hash)["is_read"] == 1
        mock_defer.assert_called_once()
        assert mock_defer.call_args.args[0] == email_reader.bulk_set_flag_remote
        assert mock_defer.call_args.args[2] == "\\Seen"
        assert mock_defer.call_args.args[3] is True

    def test_toggle_read_nonexistent_returns_error_toast(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/emails/nonexistent/toggle-read")
        assert resp.status_code == 204
        assert "not found" in resp.headers["X-Toast"].lower()

    def test_toggle_star_returns_204_and_flips_state(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_defer") as mock_defer,
        ):
            client = self._make_client()
            resp = client.post(f"/emails/{email_hash}/toggle-star")
        assert resp.status_code == 204
        assert cache.get_email_by_hash(self.db_path, email_hash)["is_starred"] == 1
        mock_defer.assert_called_once()
        assert mock_defer.call_args.args[0] == email_reader.bulk_set_flag_remote
        assert mock_defer.call_args.args[2] == "\\Flagged"
        assert mock_defer.call_args.args[3] is True

    def test_archive_redirects_to_emails(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_defer") as mock_defer,
        ):
            client = self._make_client()
            resp = client.post(f"/emails/{email_hash}/archive", follow_redirects=False)
        assert resp.status_code == 303
        # Non-blocking: the row is removed locally now, the IMAP move is deferred.
        assert cache.get_total_count(self.db_path) == 0
        mock_defer.assert_called_once()
        assert mock_defer.call_args.args[0] == email_reader.archive_remote

    def test_archive_nonexistent_hash_redirects(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_defer") as mock_defer,
        ):
            client = self._make_client()
            resp = client.post("/emails/nonexistent/archive", follow_redirects=False)
        assert resp.status_code == 303
        mock_defer.assert_not_called()

    def test_move_redirects_to_emails(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_defer") as mock_defer,
        ):
            client = self._make_client()
            resp = client.post(
                f"/emails/{email_hash}/move",
                data={"folder": "Work"},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        # Non-blocking: the row is removed locally now, the IMAP move is deferred.
        assert cache.get_total_count(self.db_path) == 0
        mock_defer.assert_called_once()
        assert mock_defer.call_args.args[0] == email_reader.move_remote
        assert mock_defer.call_args.args[2] == "Work"

    def test_move_with_unsafe_folder_keeps_row(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_defer") as mock_defer,
        ):
            client = self._make_client()
            resp = client.post(
                f"/emails/{email_hash}/move",
                data={"folder": 'a"b'},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert cache.get_total_count(self.db_path) == 1
        mock_defer.assert_not_called()

    def test_folders_endpoint_returns_list(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.list_folders", return_value=["INBOX", "Work"]),
        ):
            client = self._make_client()
            resp = client.get("/folders")
        assert resp.status_code == 200
        assert resp.json() == {"folders": ["INBOX", "Work"]}

    def test_bulk_action_with_no_selection_warns(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "read"})
        assert resp.status_code == 204
        assert "no emails" in resp.headers["X-Toast"].lower()

    def test_bulk_read_applies_to_selected(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_defer") as mock_defer,
        ):
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "read", "hashes": [h0, h1]})
        assert resp.status_code == 204
        assert cache.get_email_by_hash(self.db_path, h0)["is_read"] == 1
        assert cache.get_email_by_hash(self.db_path, h1)["is_read"] == 1
        mock_defer.assert_called_once()
        assert mock_defer.call_args.args[0] == email_reader.bulk_set_flag_remote
        assert mock_defer.call_args.args[2] == "\\Seen"
        assert mock_defer.call_args.args[3] is True

    def test_bulk_star_applies_to_selected(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_defer") as mock_defer,
        ):
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "star", "hashes": [h0, h1]})
        assert resp.status_code == 204
        assert cache.get_email_by_hash(self.db_path, h0)["is_starred"] == 1
        assert cache.get_email_by_hash(self.db_path, h1)["is_starred"] == 1
        mock_defer.assert_called_once()
        assert mock_defer.call_args.args[0] == email_reader.bulk_set_flag_remote
        assert mock_defer.call_args.args[2] == "\\Flagged"
        assert mock_defer.call_args.args[3] is True

    def test_bulk_archive_removes_rows_only_on_imap_success(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.web._defer") as mock_defer,
            patch("src.scripts.email_reader.bulk_archive_remote") as mock_archive,
        ):
            mock_archive.return_value = ["<web0@e.com>", "<web1@e.com>"]
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "archive", "hashes": [h0, h1]})
        assert resp.status_code == 204
        assert "Removed 2" in resp.headers["X-Toast"]
        assert cache.get_total_count(self.db_path) == 0
        mock_archive.assert_called_once()
        mock_defer.assert_not_called()

    def test_bulk_archive_imap_failure_keeps_emails_in_cache(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_archive_remote") as mock_archive,
        ):
            mock_archive.return_value = []
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "archive", "hashes": [h0, h1]})
        assert resp.status_code == 204
        assert "failed" in resp.headers["X-Toast"].lower()
        assert cache.get_total_count(self.db_path) == 2
        mock_archive.assert_called_once()

    def test_bulk_archive_partial_success_only_removes_archived(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_archive_remote") as mock_archive,
        ):
            mock_archive.return_value = ["<web0@e.com>"]
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "archive", "hashes": [h0, h1]})
        assert resp.status_code == 204
        assert "Removed 1" in resp.headers["X-Toast"]
        assert cache.get_total_count(self.db_path) == 1
        assert cache.get_email_by_hash(self.db_path, h0) is None  # archived
        assert cache.get_email_by_hash(self.db_path, h1) is not None  # kept
        mock_archive.assert_called_once()

    def test_bulk_move_removes_rows_only_on_imap_success(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.web._defer") as mock_defer,
            patch("src.scripts.email_reader.bulk_move_remote") as mock_move,
        ):
            mock_move.return_value = ["<web0@e.com>", "<web1@e.com>"]
            client = self._make_client()
            resp = client.post(
                "/emails/bulk",
                data={"action": "move", "folder": "Work", "hashes": [h0, h1]},
            )
        assert resp.status_code == 204
        assert "Moved 2 to Work" in resp.headers["X-Toast"]
        assert cache.get_total_count(self.db_path) == 0
        mock_move.assert_called_once()
        args = mock_move.call_args.args
        assert args[1] == "Work"
        assert args[2] == self.db_path
        mock_defer.assert_not_called()

    def test_bulk_move_imap_failure_keeps_emails_in_cache(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_move_remote") as mock_move,
        ):
            mock_move.return_value = []
            client = self._make_client()
            resp = client.post(
                "/emails/bulk",
                data={"action": "move", "folder": "Work", "hashes": [h0, h1]},
            )
        assert resp.status_code == 204
        assert "failed" in resp.headers["X-Toast"].lower()
        assert cache.get_total_count(self.db_path) == 2
        mock_move.assert_called_once()

    def test_bulk_move_partial_success_only_removes_moved(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_move_remote") as mock_move,
        ):
            mock_move.return_value = ["<web0@e.com>"]
            client = self._make_client()
            resp = client.post(
                "/emails/bulk",
                data={"action": "move", "folder": "Work", "hashes": [h0, h1]},
            )
        assert resp.status_code == 204
        assert "Moved 1 to Work" in resp.headers["X-Toast"]
        assert cache.get_total_count(self.db_path) == 1
        assert cache.get_email_by_hash(self.db_path, h0) is None  # moved
        assert cache.get_email_by_hash(self.db_path, h1) is not None  # kept

    def test_bulk_move_without_folder_warns_and_keeps_rows(self):
        self._seed_emails(1)
        h0 = cache._hash_message_id("<web0@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_move_remote") as mock_move,
        ):
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "move", "hashes": [h0]})
        assert resp.status_code == 204
        assert "no destination" in resp.headers["X-Toast"].lower()
        # No local delete, no remote op when the folder is missing.
        assert cache.get_total_count(self.db_path) == 1
        mock_move.assert_not_called()

    def test_bulk_move_with_unsafe_folder_warns_and_keeps_rows(self):
        self._seed_emails(1)
        h0 = cache._hash_message_id("<web0@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_move_remote") as mock_move,
        ):
            client = self._make_client()
            resp = client.post(
                "/emails/bulk",
                data={"action": "move", "folder": 'a"b', "hashes": [h0]},
            )
        assert resp.status_code == 204
        assert "no destination" in resp.headers["X-Toast"].lower()
        assert cache.get_total_count(self.db_path) == 1
        mock_move.assert_not_called()

    def test_bulk_delete_removes_rows_and_defers_imap(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_delete_remote") as mock_bulk_delete,
        ):
            mock_bulk_delete.return_value = ["<web0@e.com>", "<web1@e.com>"]
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "delete", "hashes": [h0, h1]})
        assert resp.status_code == 204
        assert cache.get_total_count(self.db_path) == 0
        mock_bulk_delete.assert_called_once()

    def test_bulk_delete_imap_failure_keeps_emails_in_cache(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_delete_remote") as mock_bulk_delete,
        ):
            mock_bulk_delete.return_value = []
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "delete", "hashes": [h0, h1]})
        assert resp.status_code == 204
        assert "failed" in resp.headers["X-Toast"].lower()
        assert cache.get_total_count(self.db_path) == 2
        mock_bulk_delete.assert_called_once()

    def test_bulk_delete_partial_success_only_removes_deleted(self):
        self._seed_emails(2)
        h0 = cache._hash_message_id("<web0@e.com>")
        h1 = cache._hash_message_id("<web1@e.com>")
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.email_reader.bulk_delete_remote") as mock_bulk_delete,
        ):
            mock_bulk_delete.return_value = ["<web0@e.com>"]
            client = self._make_client()
            resp = client.post("/emails/bulk", data={"action": "delete", "hashes": [h0, h1]})
        assert resp.status_code == 204
        assert "Removed 1" in resp.headers["X-Toast"]
        assert cache.get_total_count(self.db_path) == 1
        assert cache.get_email_by_hash(self.db_path, h0) is None  # deleted
        assert cache.get_email_by_hash(self.db_path, h1) is not None  # kept
        mock_bulk_delete.assert_called_once()

    def test_setup_page_no_credentials(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
        ):
            client = self._make_client()
            resp = client.get("/setup")
        assert resp.status_code == 200

    def test_account_page_redirects_to_dashboard(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/account", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    def test_partial_account_shows_email(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/partials/account")
        assert resp.status_code == 200
        assert "test@test.com" in resp.text

    def test_partial_account_disconnect_uses_confirm_dialog(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/partials/account")
        assert resp.status_code == 200
        assert "data-confirm=" in resp.text
        assert "Disconnect account?" in resp.text
        assert 'data-confirm-tone="danger"' in resp.text

    def test_keywords_remove_category_uses_confirm_dialog(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/keywords")
        assert resp.status_code == 200
        assert "hx-confirm=" in resp.text
        assert "Remove priority" in resp.text
        assert 'data-confirm-tone="danger"' in resp.text

    def test_keywords_page_returns_200_and_seeds(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/keywords")
        assert resp.status_code == 200
        assert "Priority Keywords" in resp.text
        assert "10" in cache.get_setting("keywords", self.db_path)

    def test_keywords_page_renders_words_as_static_chips(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            client.post("/keywords/word/add", data={"level": "8", "word": "zebra"})
            resp = client.get("/keywords")
        assert 'class="word"' in resp.text
        assert "zebra" in resp.text
        assert 'class="edit-input"' in resp.text

    def test_keywords_add_word(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/keywords/word/add", data={"level": "8", "word": "supercalifragilistic"})
        assert resp.status_code == 200
        cats = cache.get_setting("keywords", self.db_path)
        assert "supercalifragilistic" in cats

    def test_keywords_add_word_duplicate_shows_error(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            client.post("/keywords/word/add", data={"level": "8", "word": "dup"})
            resp = client.post("/keywords/word/add", data={"level": "8", "word": "dup"})
        assert resp.status_code == 200
        assert "already exists" in resp.text

    def test_keywords_edit_word(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            client.post("/keywords/word/add", data={"level": "8", "word": "oldword"})
            resp = client.post(
                "/keywords/word/edit",
                data={"level": "8", "old_word": "oldword", "new_word": "newword"},
            )
        assert resp.status_code == 200
        cats = cache.get_setting("keywords", self.db_path)
        assert "newword" in cats and "oldword" not in cats

    def test_keywords_remove_word(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            client.post("/keywords/word/add", data={"level": "7", "word": "deleteme"})
            resp = client.post("/keywords/word/remove", data={"level": "7", "word": "deleteme"})
        assert resp.status_code == 200
        cats = cache.get_setting("keywords", self.db_path)
        assert "deleteme" not in cats

    def test_keywords_add_category_arbitrary_level(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/keywords/category/add", data={"level": "11"})
        assert resp.status_code == 200
        cats = json.loads(cache.get_setting("keywords", self.db_path))["categories"]
        assert "11" in cats

    def test_keywords_add_category_invalid_level(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/keywords/category/add", data={"level": "notanumber"})
        assert resp.status_code == 200
        assert "must be an integer" in resp.text

    def test_keywords_remove_category(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/keywords/category/remove", data={"level": "2"})
        assert resp.status_code == 200
        cats = json.loads(cache.get_setting("keywords", self.db_path))["categories"]
        assert "2" not in cats

    def test_keywords_export_returns_json(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/keywords/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        data = json.loads(resp.content)
        assert "categories" in data

    def test_keywords_import_valid_redirects(self):
        payload = json.dumps({"categories": {"9": ["urgent"]}}).encode()
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/keywords/import",
                files={"file": ("keywords.json", payload, "application/json")},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert resp.headers["location"].endswith("?import=ok")
        cats = json.loads(cache.get_setting("keywords", self.db_path))["categories"]
        assert cats == {"9": ["urgent"]}

    def test_keywords_import_invalid_redirects(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/keywords/import",
                files={"file": ("keywords.json", b"not json {{{", "application/json")},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert resp.headers["location"].endswith("?import=invalid")

    def test_keywords_rescan_returns_partial(self):
        self._seed_emails(1)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/keywords/rescan")
        assert resp.status_code == 200
        assert "Re-scanned" in resp.text


class TestWebHelpers:
    def test_is_docker_returns_bool(self):
        assert isinstance(web._is_docker(), bool)

    def test_is_docker_true_when_dockerenv_exists(self, monkeypatch):
        class FakePath:
            def __init__(self, p):
                self.p = p

            def exists(self):
                return self.p == "/.dockerenv"

        monkeypatch.setattr(web, "Path", FakePath)
        assert web._is_docker() is True

    def test_get_local_ips_returns_empty_in_docker(self, monkeypatch):
        monkeypatch.setattr(web, "_is_docker", lambda: True)
        assert web._get_local_ips() == []

    def test_get_local_ips_filters_loopback_and_link_local(self, monkeypatch):
        monkeypatch.setattr(web, "_is_docker", lambda: False)
        monkeypatch.setattr("socket.socket", lambda *a, **k: _raise(OSError()))
        monkeypatch.setattr(
            "socket.gethostbyname_ex",
            lambda hostname: ("host", [], ["127.0.0.1", "192.168.1.5", "169.254.1.1", "10.0.0.5"]),
        )
        ips = web._get_local_ips()
        assert "127.0.0.1" not in ips
        assert "169.254.1.1" not in ips
        assert "192.168.1.5" in ips
        assert "10.0.0.5" in ips

    def test_get_local_ips_uses_udp_socket_when_resolver_fails(self, monkeypatch):
        monkeypatch.setattr(web, "_is_docker", lambda: False)
        monkeypatch.setattr("socket.gethostbyname_ex", lambda hostname: _raise(OSError()))

        class FakeSocket:
            def __init__(self, *args, **kwargs):
                self.closed = False

            def connect(self, addr):
                pass

            def getsockname(self):
                return ("192.168.1.50", 0)

            def close(self):
                self.closed = True

        monkeypatch.setattr("socket.socket", FakeSocket)
        assert web._get_local_ips() == ["192.168.1.50"]

    def test_get_local_ips_handles_socket_failure(self, monkeypatch):
        monkeypatch.setattr(web, "_is_docker", lambda: False)
        monkeypatch.setattr("socket.socket", lambda *a, **k: _raise(OSError()))

        def boom(hostname):
            raise OSError("no host")

        monkeypatch.setattr("socket.gethostbyname_ex", boom)
        assert web._get_local_ips() == []

    def test_is_tailscale_mode_false_without_shared_dir(self):
        assert isinstance(web._is_tailscale_mode(), bool)

    def test_tailscale_info_none_when_not_tailscale_mode(self, monkeypatch):
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: False)
        assert web._tailscale_info() is None

    def test_tailscale_info_parses_status_file(self, monkeypatch, tmp_path):
        import json

        status_file = tmp_path / "status.json"
        status_file.write_text(json.dumps({"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.0.1"]}}))
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: True)
        monkeypatch.setattr(web, "TS_STATUS_FILE", str(status_file))
        info = web._tailscale_info()
        assert info["BackendState"] == "Running"

    def test_tailscale_info_none_on_missing_file(self, monkeypatch):
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: True)
        monkeypatch.setattr(web, "TS_STATUS_FILE", "/nonexistent/status.json")
        assert web._tailscale_info() is None

    def test_tailscale_info_none_on_invalid_json(self, monkeypatch, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json{{{")
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: True)
        monkeypatch.setattr(web, "TS_STATUS_FILE", str(bad))
        assert web._tailscale_info() is None

    def test_get_tailscale_ip_empty_when_not_running(self, monkeypatch):
        monkeypatch.setattr(web, "_tailscale_info", lambda: {"BackendState": "NeedsLogin"})
        assert web._get_tailscale_ip() == ""

    def test_get_tailscale_ip_returns_100_prefix(self, monkeypatch):
        monkeypatch.setattr(
            web,
            "_tailscale_info",
            lambda: {"BackendState": "Running", "Self": {"TailscaleIPs": ["192.168.1.1", "100.64.0.5"]}},
        )
        assert web._get_tailscale_ip() == "100.64.0.5"

    def test_get_tailscale_ip_empty_when_no_100_ip(self, monkeypatch):
        monkeypatch.setattr(
            web,
            "_tailscale_info",
            lambda: {"BackendState": "Running", "Self": {"TailscaleIPs": ["192.168.1.1"]}},
        )
        assert web._get_tailscale_ip() == ""

    def test_get_tailscale_dns_name(self, monkeypatch):
        monkeypatch.setattr(
            web,
            "_tailscale_info",
            lambda: {"BackendState": "Running", "Self": {"DNSName": "host.tailnet.ts.net."}},
        )
        assert web._get_tailscale_dns_name() == "host.tailnet.ts.net"

    def test_get_tailscale_dns_name_empty_when_not_running(self, monkeypatch):
        monkeypatch.setattr(web, "_tailscale_info", lambda: None)
        assert web._get_tailscale_dns_name() == ""

    def test_get_tailscale_login_url_empty_when_not_tailscale(self, monkeypatch):
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: False)
        assert web._get_tailscale_login_url() == ""

    def test_get_tailscale_login_url_reads_file(self, monkeypatch, tmp_path):
        url_file = tmp_path / "login_url.txt"
        url_file.write_text("https://login.tailscale.com/a/abc\n")
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: True)
        monkeypatch.setattr(web, "TS_LOGIN_URL_FILE", str(url_file))
        assert web._get_tailscale_login_url() == "https://login.tailscale.com/a/abc"

    def test_get_tailscale_login_url_empty_on_missing_file(self, monkeypatch):
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: True)
        monkeypatch.setattr(web, "TS_LOGIN_URL_FILE", "/nonexistent/login.txt")
        assert web._get_tailscale_login_url() == ""

    def test_get_tailscale_serve_url_empty_when_not_tailscale(self, monkeypatch):
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: False)
        assert web._get_tailscale_serve_url() == ""

    def test_get_tailscale_serve_url_empty_without_dns(self, monkeypatch):
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: True)
        monkeypatch.setattr(web, "_get_tailscale_dns_name", lambda: "")
        assert web._get_tailscale_serve_url() == ""

    def test_get_tailscale_serve_url_empty_when_serve_not_done(self, monkeypatch, tmp_path):
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: True)
        monkeypatch.setattr(web, "_get_tailscale_dns_name", lambda: "host.tailnet.ts.net")
        monkeypatch.setattr(web, "TS_SERVE_DONE_FILE", str(tmp_path / "missing"))
        assert web._get_tailscale_serve_url() == ""

    def test_get_tailscale_serve_url_returns_https_when_done(self, monkeypatch, tmp_path):
        done = tmp_path / "serve_done"
        done.write_text("")
        monkeypatch.setattr(web, "_is_tailscale_mode", lambda: True)
        monkeypatch.setattr(web, "_get_tailscale_dns_name", lambda: "inbox-lens.tailnet.ts.net")
        monkeypatch.setattr(web, "TS_SERVE_DONE_FILE", str(done))
        assert web._get_tailscale_serve_url() == "https://inbox-lens.tailnet.ts.net"


class TestSetupGuardMiddleware:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "mw.db")
        cache.init_db(self.db_path)

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def test_exempt_setup_path_no_credentials(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
        ):
            client = self._make_client()
            resp = client.get("/setup", follow_redirects=False)
        assert resp.status_code == 200

    def test_redirects_to_setup_dashboard_when_no_credentials_and_no_password(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
        ):
            client = self._make_client()
            resp = client.get("/emails", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/setup-dashboard"

    def test_redirects_to_setup_when_no_credentials_but_password_set(self):
        from src.scripts import auth

        auth.set_password("somepassword", self.db_path)
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
        ):
            client = self._make_client()
            client.post("/login", data={"password": "somepassword", "next": "/"}, follow_redirects=False)
            resp = client.get("/emails", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/setup"

    def test_passthrough_when_credentials_present(self):
        cache.save_email_credentials("u@e.com", "pass", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200

    def test_exempt_health_endpoint(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
        ):
            client = self._make_client()
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestWebExtraEndpoints:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "test_extra.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("test@test.com", "testpass", self.db_path)

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def _seed_emails(self, n=5):
        emails = [
            {
                "message_id": f"<web{i}@e.com>",
                "from": f"sender{i}@e.com",
                "subject": f"Web subject {i}",
                "date": f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
                "body": f"Body {i}",
                "_category": "7" if i % 2 == 0 else "3",
            }
            for i in range(n)
        ]
        _save_fetched_batch(emails, self.db_path)
        return emails

    def test_health_endpoint(self):
        client = self._make_client()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_setup_submit_validation_error(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
        ):
            client = self._make_client()
            resp = client.post("/setup", data={"email_user": "", "email_pass": ""}, follow_redirects=False)
        assert resp.status_code == 200
        assert "required" in resp.text.lower()

    def test_setup_submit_invalid_email_format(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
            patch(
                "src.scripts.email_reader.test_connection",
                return_value={"success": False, "error": "Invalid email or password."},
            ),
        ):
            client = self._make_client()
            resp = client.post(
                "/setup",
                data={"email_user": "nosymbol", "email_pass": "p"},
                follow_redirects=False,
            )
        assert resp.status_code == 200
        assert "Invalid" in resp.text

    def test_setup_submit_success_redirects(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
            patch("src.scripts.email_reader.test_connection", return_value={"success": True, "inbox_count": 0}),
            patch("src.scripts.idle_monitor.IdleMonitor") as MockMonitor,
            patch("src.scripts.idle_monitor.run_initial_fetch"),
            patch("src.scripts.outbox_worker.OutboxWorker") as MockOutbox,
        ):
            mock_instance = MockMonitor.return_value
            mock_outbox = MockOutbox.return_value
            client = self._make_client()
            resp = client.post(
                "/setup",
                data={"email_user": "new@e.com", "email_pass": "secret"},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert cache.has_email_credentials(self.db_path)
        mock_instance.start.assert_called_once()
        mock_outbox.start.assert_called_once()

    def test_setup_submit_resets_folder_caches(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.has_email_credentials", return_value=False),
            patch("src.scripts.email_reader.test_connection", return_value={"success": True, "inbox_count": 0}),
            patch("src.scripts.idle_monitor.IdleMonitor"),
            patch("src.scripts.idle_monitor.run_initial_fetch"),
            patch("src.scripts.outbox_worker.OutboxWorker"),
            patch("src.scripts.email_reader.reset_folder_caches") as mock_reset,
        ):
            client = self._make_client()
            resp = client.post(
                "/setup",
                data={"email_user": "new@e.com", "email_pass": "secret"},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        mock_reset.assert_called_once()

    def test_setup_submit_redirects_when_already_configured(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/setup", data={"email_user": "x", "email_pass": "y"}, follow_redirects=False)
        assert resp.status_code == 303

    def test_account_page_redirects_when_no_user(self, tmp_path):
        db = str(tmp_path / "no_user.db")
        cache.init_db(db)
        with patch.object(web, "DB_PATH", db):
            client = self._make_client()
            resp = client.get("/account", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/setup-dashboard"

    def test_account_disconnect_clears_credentials(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/account/disconnect", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/setup"
        assert not cache.has_email_credentials(self.db_path)

    def test_account_disconnect_clears_cached_emails(self):
        self._seed_emails(3)
        assert cache.get_total_count(self.db_path) == 3
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/account/disconnect", follow_redirects=False)
        assert resp.status_code == 303
        assert cache.get_total_count(self.db_path) == 0

    def test_settings_page_returns_200(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert 'id="confirm-modal"' in resp.text

    def test_settings_revoke_api_key_uses_confirm_dialog(self):
        from src.scripts import auth

        auth.save_api_key("dummytoken", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert "Revoke API key?" in resp.text
        assert 'data-confirm-tone="danger"' in resp.text

    def test_settings_network_access_toggle(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/settings/network-access", data={"enabled": "false"})
        assert resp.status_code == 200
        assert cache.get_setting("network_access", self.db_path) == "false"

    def test_partial_dashboard(self):
        self._seed_emails(3)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/partials/dashboard")
        assert resp.status_code == 200

    def test_partial_emails(self):
        self._seed_emails(3)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/partials/emails")
        assert resp.status_code == 200

    def test_partial_email_detail_found(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get(f"/partials/email-detail/{email_hash}")
        assert resp.status_code == 200

    def test_partial_email_detail_not_found(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/partials/email-detail/nonexistent")
        assert resp.status_code == 404

    def test_partial_tailscale_status(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/partials/tailscale-status")
        assert resp.status_code == 200


class TestReceivedAttachmentsEndpoints:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "test_recatt.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("test@test.com", "testpass", self.db_path)

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def _seed_email(self):
        _save_fetched_batch(
            [
                {
                    "message_id": "<rec0@e.com>",
                    "from": "sender@e.com",
                    "subject": "Rec subject",
                    "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                    "body": "Body 0",
                    "_category": "7",
                }
            ],
            self.db_path,
        )
        return cache._hash_message_id("<rec0@e.com>")

    def test_format_size_filter(self):
        assert web._format_size(0) == "0 B"
        assert web._format_size(512) == "512 B"
        assert web._format_size(2048) == "2.0 KB"
        assert web._format_size(2 * 1024 * 1024) == "2.0 MB"

    def test_download_attachment_returns_bytes_and_header(self):
        email_hash = self._seed_email()
        created = cache.store_received_attachments(
            self.db_path,
            email_hash,
            [{"filename": "doc.pdf", "mime_type": "application/pdf", "payload": b"%PDF-1.4"}],
        )[0]
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get(f"/emails/{email_hash}/attachments/{created['id']}")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4"
        assert resp.headers["content-type"] == "application/pdf"
        cd = resp.headers["content-disposition"]
        assert 'filename="doc.pdf"' in cd
        assert "filename*=UTF-8''doc.pdf" in cd

    def test_download_attachment_404_on_wrong_owner(self):
        email_hash = self._seed_email()
        created = cache.store_received_attachments(
            self.db_path,
            "otherhash",
            [{"filename": "secret.txt", "mime_type": "text/plain", "payload": b"secret"}],
        )[0]
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get(f"/emails/{email_hash}/attachments/{created['id']}")
        assert resp.status_code == 404

    def test_download_attachment_404_on_unknown_id(self):
        email_hash = self._seed_email()
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get(f"/emails/{email_hash}/attachments/does-not-exist")
        assert resp.status_code == 404

    def test_detail_partial_renders_attachment_chips(self):
        email_hash = self._seed_email()
        cache.store_received_attachments(
            self.db_path,
            email_hash,
            [{"filename": "doc.pdf", "mime_type": "application/pdf", "payload": b"%PDF-1.4"}],
        )
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get(f"/partials/email-detail/{email_hash}")
        assert resp.status_code == 200
        body = resp.text
        assert "received-attachments" in body
        assert "doc.pdf" in body
        assert f"/emails/{email_hash}/attachments/" in body
        assert "B" in body or "KB" in body


class TestAttachmentsPage:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "test_att.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("test@test.com", "testpass", self.db_path)

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def _seed_email(
        self, message_id="<rec@e.com>", sender="sender@e.com", subject="Subject", date="Mon, 01 Jan 2024 10:00:00 +0000"
    ):
        _save_fetched_batch(
            [
                {
                    "message_id": message_id,
                    "from": sender,
                    "subject": subject,
                    "date": date,
                    "body": "Body",
                    "_category": "7",
                }
            ],
            self.db_path,
        )
        return cache._hash_message_id(message_id)

    def _seed_attachment(self, email_hash, filename, mime, payload=b"x"):
        return cache.store_received_attachments(
            self.db_path, email_hash, [{"filename": filename, "mime_type": mime, "payload": payload}]
        )[0]

    def test_page_renders_with_items(self):
        email_hash = self._seed_email()
        self._seed_attachment(email_hash, "doc.pdf", "application/pdf", b"%PDF")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments")
        assert resp.status_code == 200
        assert "Attachments" in resp.text
        assert "doc.pdf" in resp.text
        assert "application/pdf" in resp.text
        assert "sender@e.com" in resp.text
        assert "Subject" in resp.text

    def test_page_renders_empty_state(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments")
        assert resp.status_code == 200
        assert "No attachments found" in resp.text or "empty-state" in resp.text

    def test_search_filter_substring(self):
        email_hash = self._seed_email()
        self._seed_attachment(email_hash, "invoice_2024.pdf", "application/pdf")
        self._seed_attachment(email_hash, "report.txt", "text/plain")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?search=invoice")
        assert resp.status_code == 200
        assert "invoice_2024.pdf" in resp.text
        assert "report.txt" not in resp.text

    def test_search_filter_extension(self):
        h1 = self._seed_email(message_id="<e1@e.com>")
        h2 = self._seed_email(message_id="<e2@e.com>")
        h3 = self._seed_email(message_id="<e3@e.com>")
        self._seed_attachment(h1, "a.txt", "text/plain")
        self._seed_attachment(h2, "b.txt", "text/plain")
        self._seed_attachment(h3, "c.png", "image/png")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?search=.txt")
        assert resp.status_code == 200
        assert "a.txt" in resp.text
        assert "b.txt" in resp.text
        assert "c.png" not in resp.text

    def test_type_filter_images(self):
        h1 = self._seed_email(message_id="<e1@e.com>")
        h2 = self._seed_email(message_id="<e2@e.com>")
        h3 = self._seed_email(message_id="<e3@e.com>")
        self._seed_attachment(h1, "a.png", "image/png")
        self._seed_attachment(h2, "b.jpg", "image/jpeg")
        self._seed_attachment(h3, "c.txt", "text/plain")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?type=images")
        assert resp.status_code == 200
        assert "a.png" in resp.text
        assert "b.jpg" in resp.text
        assert "c.txt" not in resp.text

    def test_type_filter_pdf(self):
        h1 = self._seed_email(message_id="<e1@e.com>")
        h2 = self._seed_email(message_id="<e2@e.com>")
        self._seed_attachment(h1, "a.pdf", "application/pdf")
        self._seed_attachment(h2, "b.txt", "text/plain")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?type=pdf")
        assert resp.status_code == 200
        assert "a.pdf" in resp.text
        assert "b.txt" not in resp.text

    def test_type_filter_documents(self):
        h1 = self._seed_email(message_id="<e1@e.com>")
        h2 = self._seed_email(message_id="<e2@e.com>")
        h3 = self._seed_email(message_id="<e3@e.com>")
        h4 = self._seed_email(message_id="<e4@e.com>")
        self._seed_attachment(h1, "a.txt", "text/plain")
        self._seed_attachment(h2, "b.csv", "text/csv")
        self._seed_attachment(h3, "c.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self._seed_attachment(h4, "d.png", "image/png")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?type=documents")
        assert resp.status_code == 200
        assert "a.txt" in resp.text
        assert "b.csv" in resp.text
        assert "c.docx" in resp.text
        assert "d.png" not in resp.text

    def test_type_filter_archives(self):
        h1 = self._seed_email(message_id="<e1@e.com>")
        h2 = self._seed_email(message_id="<e2@e.com>")
        h3 = self._seed_email(message_id="<e3@e.com>")
        self._seed_attachment(h1, "a.zip", "application/zip")
        self._seed_attachment(h2, "b.gz", "application/gzip")
        self._seed_attachment(h3, "c.txt", "text/plain")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?type=archives")
        assert resp.status_code == 200
        assert "a.zip" in resp.text
        assert "b.gz" in resp.text
        assert "c.txt" not in resp.text

    def test_type_filter_other(self):
        h1 = self._seed_email(message_id="<e1@e.com>")
        h2 = self._seed_email(message_id="<e2@e.com>")
        h3 = self._seed_email(message_id="<e3@e.com>")
        self._seed_attachment(h1, "a.txt", "text/plain")
        self._seed_attachment(h2, "b.pdf", "application/pdf")
        self._seed_attachment(h3, "c.unknown", "application/octet-stream")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?type=other")
        assert resp.status_code == 200
        assert "c.unknown" in resp.text
        assert "a.txt" not in resp.text
        assert "b.pdf" not in resp.text

    def test_size_sort_asc(self):
        h1 = self._seed_email(message_id="<e1@e.com>")
        h2 = self._seed_email(message_id="<e2@e.com>")
        h3 = self._seed_email(message_id="<e3@e.com>")
        self._seed_attachment(h1, "a.txt", "text/plain", b"aaa")  # 3
        self._seed_attachment(h2, "b.txt", "text/plain", b"bb")  # 2
        self._seed_attachment(h3, "c.txt", "text/plain", b"ccccc")  # 5
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?sort=asc")
        assert resp.status_code == 200
        idx_b = resp.text.index("b.txt")
        idx_a = resp.text.index("a.txt")
        idx_c = resp.text.index("c.txt")
        assert idx_b < idx_a < idx_c

    def test_size_sort_desc(self):
        h1 = self._seed_email(message_id="<e1@e.com>")
        h2 = self._seed_email(message_id="<e2@e.com>")
        h3 = self._seed_email(message_id="<e3@e.com>")
        self._seed_attachment(h1, "a.txt", "text/plain", b"aaa")  # 3
        self._seed_attachment(h2, "b.txt", "text/plain", b"bb")  # 2
        self._seed_attachment(h3, "c.txt", "text/plain", b"ccccc")  # 5
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments?sort=desc")
        assert resp.status_code == 200
        idx_c = resp.text.index("c.txt")
        idx_a = resp.text.index("a.txt")
        idx_b = resp.text.index("b.txt")
        assert idx_c < idx_a < idx_b

    def test_pagination(self):
        for i in range(11):
            h = self._seed_email(message_id=f"<e{i}@e.com>")
            self._seed_attachment(h, f"f{i}.txt", "text/plain", b"x")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            cache.save_setting("page_size", "10", self.db_path)
            resp = client.get("/attachments?page=1&sort=asc")
            assert resp.status_code == 200
            assert "Page 1 of 2" in resp.text
            assert "Next &raquo;" in resp.text
            resp2 = client.get("/attachments?page=2&sort=asc")
            assert resp2.status_code == 200
            assert "Page 2 of 2" in resp2.text
            assert "&laquo; Prev" in resp2.text

    def test_partial_endpoint_renders_table(self):
        email_hash = self._seed_email()
        self._seed_attachment(email_hash, "doc.pdf", "application/pdf")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/partials/attachments")
        assert resp.status_code == 200
        assert "doc.pdf" in resp.text
        assert "attachments-table" in resp.text or "table" in resp.text

    def test_nav_link_present_in_desktop_and_mobile(self):
        email_hash = self._seed_email()
        self._seed_attachment(email_hash, "doc.pdf", "application/pdf")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments")
        assert resp.status_code == 200
        body = resp.text
        assert '/attachments"' in body
        assert "paperclip" in body
        assert "Attachments" in body
        assert "mobile-menu" in body or "mobile-menu-links" in body
        assert "/attachments" in body

    def test_download_link_in_table(self):
        email_hash = self._seed_email()
        att = self._seed_attachment(email_hash, "doc.pdf", "application/pdf")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments")
        assert resp.status_code == 200
        assert f"/emails/{email_hash}/attachments/{att['id']}" in resp.text

    def test_open_email_link_in_table(self):
        email_hash = self._seed_email()
        self._seed_attachment(email_hash, "doc.pdf", "application/pdf")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/attachments")
        assert resp.status_code == 200
        assert f'/emails/{email_hash}"' in resp.text or f"/emails/{email_hash}'" in resp.text


class TestDashboardPriorityDistribution:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "prio.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("test@test.com", "testpass", self.db_path)

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def _seed_with_categories(self):
        emails = [
            {
                "message_id": "<c1@e.com>",
                "subject": "s",
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "body": "b",
                "_category": "10",
            },
            {
                "message_id": "<c2@e.com>",
                "subject": "s",
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "body": "b",
                "_category": "9",
            },
            {
                "message_id": "<h1@e.com>",
                "subject": "s",
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "body": "b",
                "_category": "7",
            },
            {
                "message_id": "<m1@e.com>",
                "subject": "s",
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "body": "b",
                "_category": "5",
            },
            {
                "message_id": "<l1@e.com>",
                "subject": "s",
                "date": "Mon, 01 Jan 2024 00:00:00 +0000",
                "body": "b",
                "_category": "2",
            },
            {"message_id": "<u1@e.com>", "subject": "s", "date": "Mon, 01 Jan 2024 00:00:00 +0000", "body": "b"},
        ]
        _save_fetched_batch(emails, self.db_path)
        hashes = [cache._hash_message_id(e["message_id"]) for e in emails]
        with cache._connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE emails SET status = 'checked' WHERE message_id_hash = ?",
                [(h,) for h in hashes],
            )

    def test_priority_counts_render_in_dashboard(self):
        self._seed_with_categories()
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.cache.get_priority_counts", return_value={"10": 2, "9": 0, "7": 1, "5": 1, "2": 1}),
        ):
            client = self._make_client()
            resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        for label in ("critical", "high", "medium", "low"):
            assert label.lower() in body.lower() or "priority" in body.lower()


class TestSseEvents:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "sse.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("u@e.com", "p", self.db_path)

    def test_sse_event_stream_publishes_event(self):
        import asyncio

        from src.scripts import event_bus

        class _StubRequest:
            async def is_disconnected(self):
                return False

        async def drive():
            response = await web.sse_events(_StubRequest())
            chunks = []

            async def consume():
                async for chunk in response.body_iterator:
                    chunks.append(chunk)
                    if isinstance(chunk, str) and "refresh" in chunk:
                        break

            async def publish():
                await asyncio.sleep(0.05)
                event_bus.bus.publish("refresh", {"hello": "world"})

            await asyncio.gather(consume(), publish())
            return chunks

        chunks = asyncio.run(drive())
        assert any("refresh" in c for c in chunks)
        assert any("hello" in c for c in chunks)

    def test_sse_unsubscribes_on_disconnect(self):
        import asyncio

        from src.scripts import event_bus

        class _StubRequest:
            async def is_disconnected(self):
                return True

        async def drive():
            response = await web.sse_events(_StubRequest())
            async for _ in response.body_iterator:
                break

        sub_count_before = len(event_bus.bus._subscribers)
        asyncio.run(drive())
        assert len(event_bus.bus._subscribers) <= sub_count_before


class TestResolveBindHost:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "bindhost.db")
        cache.init_db(self.db_path)

    def test_loopback_requested_returned_as_is(self):
        with patch.object(web, "DB_PATH", self.db_path):
            assert web._resolve_bind_host("127.0.0.1") == "127.0.0.1"
            assert web._resolve_bind_host("localhost") == "localhost"

    def test_forces_loopback_when_no_password(self):
        with patch.object(web, "DB_PATH", self.db_path):
            assert web._resolve_bind_host("0.0.0.0") == "127.0.0.1"

    def test_allows_external_when_password_set(self):
        from src.scripts import auth

        auth.set_password("somepassword", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            assert web._resolve_bind_host("0.0.0.0") == "0.0.0.0"

    def test_falls_back_to_requested_on_db_error(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch("src.scripts.web.cache.init_db", side_effect=RuntimeError("boom")),
        ):
            assert web._resolve_bind_host("0.0.0.0") == "0.0.0.0"


class TestAuthMiddleware:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from src.scripts import auth

        self.db_path = str(tmp_path / "auth_mw.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("test@test.com", "testpass", self.db_path)
        auth.set_password("dashboardpw", self.db_path)
        auth.login_rate_limiter = auth.LoginRateLimiter()

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def _login(self, client, password="dashboardpw"):
        client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)

    def test_unauthenticated_html_redirects_to_login(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/login?next=/")

    def test_unauthenticated_api_returns_401(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/", headers={"accept": "application/json"}, follow_redirects=False)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Not authenticated."

    def test_login_grants_access(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            self._login(client)
            resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200

    def test_api_key_grants_access(self):
        from src.scripts import auth

        token = auth.generate_api_key()
        auth.save_api_key(token, self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get(
                "/",
                headers={"accept": "text/html", "authorization": f"Bearer {token}"},
                follow_redirects=False,
            )
        assert resp.status_code == 200

    def test_bad_api_key_rejected(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get(
                "/",
                headers={"accept": "application/json", "authorization": "Bearer wrong"},
                follow_redirects=False,
            )
        assert resp.status_code == 401

    def test_logout_clears_session(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            self._login(client)
            resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"
        with patch.object(web, "DB_PATH", self.db_path):
            resp = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/login?next=/")

    def test_csrf_rejects_bad_origin(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            self._login(client)
            resp = client.post(
                "/settings/network-access",
                data={"enabled": "false"},
                headers={"origin": "http://evil.com"},
                follow_redirects=False,
            )
        assert resp.status_code == 403

    def test_csrf_allows_matching_origin(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            self._login(client)
            resp = client.post(
                "/settings/network-access",
                data={"enabled": "false"},
                headers={"origin": "http://testserver", "host": "testserver"},
                follow_redirects=False,
            )
        assert resp.status_code == 200

    def test_setup_dashboard_redirects_when_configured(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            self._login(client)
            resp = client.get("/setup-dashboard", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    def test_attachment_download_requires_auth(self):
        # Regression guard: attachment endpoints inherit the global auth gate.
        _save_fetched_batch(
            [
                {
                    "message_id": "<attach-auth@e.com>",
                    "from": "s@e.com",
                    "subject": "S",
                    "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                    "body": "b",
                    "_category": "7",
                }
            ],
            self.db_path,
        )
        email_hash = cache._hash_message_id("<attach-auth@e.com>")
        created = cache.store_received_attachments(
            self.db_path,
            email_hash,
            [{"filename": "f.txt", "mime_type": "text/plain", "payload": b"secret"}],
        )[0]
        att_id = created["id"]
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            html = client.get(
                f"/emails/{email_hash}/attachments/{att_id}",
                headers={"accept": "text/html"},
                follow_redirects=False,
            )
            assert html.status_code == 303
            assert html.headers["location"].startswith("/login?next=")
            api = client.get(
                f"/emails/{email_hash}/attachments/{att_id}",
                headers={"accept": "application/json"},
                follow_redirects=False,
            )
            assert api.status_code == 401
            assert api.json()["detail"] == "Not authenticated."


class TestAuthRoutes:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from src.scripts import auth

        self.db_path = str(tmp_path / "auth_routes.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("test@test.com", "testpass", self.db_path)
        auth.login_rate_limiter = auth.LoginRateLimiter()

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def test_setup_dashboard_get_when_not_configured(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/setup-dashboard", follow_redirects=False)
        assert resp.status_code == 200
        assert "Set Password" in resp.text

    def test_setup_dashboard_creates_password_and_logs_in(self):
        from src.scripts import auth

        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/setup-dashboard",
                data={"password": "newpass12", "confirm": "newpass12"},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert auth.is_auth_configured(self.db_path) is True
        with patch.object(web, "DB_PATH", self.db_path):
            resp = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        assert resp.status_code != 303 or not resp.headers["location"].startswith("/login")

    def test_setup_dashboard_password_mismatch(self):
        from src.scripts import auth

        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/setup-dashboard",
                data={"password": "newpass12", "confirm": "different12"},
                follow_redirects=False,
            )
        assert resp.status_code == 200
        assert "do not match" in resp.text
        assert auth.is_auth_configured(self.db_path) is False

    def test_setup_dashboard_short_password(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/setup-dashboard",
                data={"password": "short", "confirm": "short"},
                follow_redirects=False,
            )
        assert resp.status_code == 200
        assert "at least 8" in resp.text

    def test_setup_dashboard_generates_api_key(self):
        from src.scripts import auth

        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/setup-dashboard",
                data={"password": "newpass12", "confirm": "newpass12", "generate_api_key": "on"},
                follow_redirects=False,
            )
        assert resp.status_code == 200
        assert auth.get_api_key_created_at(self.db_path) is not None
        assert "Continue to Dashboard" in resp.text

    def test_login_wrong_password(self):
        from src.scripts import auth

        auth.set_password("dashboardpw", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/login", data={"password": "wrong", "next": "/"}, follow_redirects=False)
        assert resp.status_code == 200
        assert "Incorrect" in resp.text

    def test_login_correct_password(self):
        from src.scripts import auth

        auth.set_password("dashboardpw", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/login", data={"password": "dashboardpw", "next": "/"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert "inbox_lens_session" in resp.cookies

    def test_login_open_redirect_prevented(self):
        from src.scripts import auth

        auth.set_password("dashboardpw", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post(
                "/login",
                data={"password": "dashboardpw", "next": "//evil.com"},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    def test_login_rate_limiting(self):
        from src.scripts import auth

        auth.set_password("dashboardpw", self.db_path)
        auth.login_rate_limiter = auth.LoginRateLimiter(max_attempts=3, window_seconds=60)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            for _ in range(3):
                client.post("/login", data={"password": "wrong", "next": "/"}, follow_redirects=False)
            resp = client.post("/login", data={"password": "wrong", "next": "/"}, follow_redirects=False)
        assert resp.status_code == 200
        assert "Too many" in resp.text

    def test_settings_password_change(self):
        from src.scripts import auth

        auth.set_password("oldpass12", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            client.post("/login", data={"password": "oldpass12", "next": "/"}, follow_redirects=False)
            resp = client.post(
                "/settings/password",
                data={"old_password": "oldpass12", "new_password": "brandnew1", "confirm_password": "brandnew1"},
                follow_redirects=False,
            )
        assert resp.status_code == 200
        assert auth.verify_password("brandnew1", self.db_path) is True

    def test_settings_api_key_regenerate_and_revoke(self):
        from src.scripts import auth

        auth.set_password("dashboardpw", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            client.post("/login", data={"password": "dashboardpw", "next": "/"}, follow_redirects=False)
            resp = client.post("/settings/api-key/regenerate", follow_redirects=False)
        assert resp.status_code == 200
        assert auth.get_api_key_created_at(self.db_path) is not None
        assert "Bearer" in resp.text
        with patch.object(web, "DB_PATH", self.db_path):
            resp = client.post("/settings/api-key/revoke", follow_redirects=False)
        assert resp.status_code == 200
        assert auth.get_api_key_created_at(self.db_path) is None


class TestUpdateEndpoints:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "test_update.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("test@test.com", "testpass", self.db_path)

    def _make_client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def test_update_status_returns_fields(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.get("/api/update/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_version"] == "1.2.0"
        assert data["latest_version"] == "v1.3.0"
        assert data["update_available"] is True
        assert "phase" in data["update_state"]

    def test_banner_partial_shown_in_non_docker(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.get("/partials/update-banner")
        assert resp.status_code == 200
        assert b"A new version is available" in resp.content
        assert b"git" in resp.content
        assert b"Click here to update" not in resp.content

    def test_banner_partial_shown_in_docker_when_update_available(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: True),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.get("/partials/update-banner")
        assert resp.status_code == 200
        assert b"A new update is available" in resp.content
        assert b"Click here to update" in resp.content

    def test_banner_partial_hidden_when_dismissed(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: True),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
            patch.object(web.cache, "get_setting", lambda *a, **k: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.get("/partials/update-banner")
        assert resp.status_code == 200
        assert b"A new" not in resp.content

    def test_dismiss_persists_latest_version(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: True),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.post("/api/update/dismiss")
        assert resp.status_code == 200
        assert cache.get_setting(web.updater.DISMISSED_VERSION_KEY, self.db_path) == "v1.3.0"

    def test_check_endpoint_forces_refresh(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "fetch_latest_version") as mock_fetch,
            patch.object(web.updater, "get_current_version", lambda: "1.3.0"),
        ):
            mock_fetch.return_value = "v1.3.0"
            client = self._make_client()
            resp = client.post("/api/update/check")
        assert resp.status_code == 200
        assert any(call.kwargs.get("force") for call in mock_fetch.call_args_list)

    def test_check_endpoint_cooldown_skips_repeated_force(self):
        forced = {"n": 0}

        def fake_fetch(force=False):
            if force:
                forced["n"] += 1
            return "v1.3.0"

        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "fetch_latest_version", side_effect=fake_fetch),
            patch.object(web.updater, "get_current_version", lambda: "1.3.0"),
            patch.object(web, "MANUAL_CHECK_COOLDOWN", 30),
        ):
            client = self._make_client()
            r1 = client.post("/api/update/check")
            r2 = client.post("/api/update/check")  # within cooldown
        assert r1.status_code == 200 and r2.status_code == 200
        assert forced["n"] == 1  # only the first call bypassed the cache

    def test_check_endpoint_cooldown_expires(self):
        forced = {"n": 0}

        def fake_fetch(force=False):
            if force:
                forced["n"] += 1
            return "v1.3.0"

        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "fetch_latest_version", side_effect=fake_fetch),
            patch.object(web.updater, "get_current_version", lambda: "1.3.0"),
            patch.object(web, "MANUAL_CHECK_COOLDOWN", 0),
        ):
            client = self._make_client()
            client.post("/api/update/check")
            client.post("/api/update/check")
        assert forced["n"] == 2  # cooldown expired immediately -> both forced

    def test_update_info_exposes_rolled_back_flag(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
            patch.object(
                web.cache,
                "get_setting",
                lambda key, *a, **k: "1" if key == web.updater.LAST_UPDATE_ROLLED_BACK_KEY else None,
            ),
        ):
            info = web._update_info(self.db_path)
        assert info["update_rolled_back"] is True

    def test_check_endpoint_clears_rolled_back_flag(self):
        cleared = []

        def fake_get_setting(key, *a, **k):
            return "1" if key == web.updater.LAST_UPDATE_ROLLED_BACK_KEY else None

        def fake_delete_setting(key, *a, **k):
            cleared.append(key)

        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
            patch.object(web.updater, "get_current_version", lambda: "1.3.0"),
            patch.object(web.cache, "get_setting", side_effect=fake_get_setting),
            patch.object(web.cache, "delete_setting", side_effect=fake_delete_setting),
            patch.object(web, "MANUAL_CHECK_COOLDOWN", 0),
        ):
            client = self._make_client()
            resp = client.post("/api/update/check")
        assert resp.status_code == 200
        assert web.updater.LAST_UPDATE_ROLLED_BACK_KEY in cleared

    def test_run_endpoint_clears_rolled_back_flag(self):
        cleared = []

        def fake_delete_setting(key, *a, **k):
            cleared.append(key)

        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "is_docker_managed", lambda: True),
            patch.object(web.updater, "trigger_update", lambda: True),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
            patch.object(web.cache, "get_setting", lambda *a, **k: None),
            patch.object(web.cache, "delete_setting", side_effect=fake_delete_setting),
        ):
            client = self._make_client()
            resp = client.post("/api/update/run")
        assert resp.status_code == 200
        assert web.updater.LAST_UPDATE_ROLLED_BACK_KEY in cleared

    def test_run_endpoint_triggers_update_when_managed(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "is_docker_managed", lambda: True),
            patch.object(web.updater, "trigger_update", return_value=True) as mock_trigger,
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.post("/api/update/run")
        assert resp.status_code == 200
        mock_trigger.assert_called_once()

    def test_run_endpoint_skips_trigger_when_not_managed(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web.updater, "is_docker_managed", lambda: False),
            patch.object(web.updater, "trigger_update") as mock_trigger,
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.post("/api/update/run")
        assert resp.status_code == 200
        mock_trigger.assert_not_called()

    def test_settings_page_non_docker_shows_git_pull_hint(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "is_docker_environment", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b"git pull" in resp.content
        assert b"A new version is available" in resp.content

    def test_settings_page_docker_shows_update_panel(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: True),
            patch.object(web.updater, "is_docker_environment", lambda: True),
            patch.object(web.updater, "get_current_version", lambda: "1.2.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: "v1.3.0"),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b"Update Now" in resp.content or b"docker compose pull" in resp.content

    def test_settings_timezone_save_valid(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/timezone",
                data={"timezone": "Asia/Tokyo"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast") == "Updated"
        assert cache.get_setting("timezone", self.db_path) == "Asia/Tokyo"

    def test_settings_timezone_rejects_invalid(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web, "_LOCAL_TIMEZONE", "Asia/Tokyo"),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/timezone",
                data={"timezone": "Fake/Invalid_Zone"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        # Invalid input falls back to the detected device timezone.
        assert cache.get_setting("timezone", self.db_path) == "Asia/Tokyo"

    def test_settings_page_timezone_combobox_carries_saved_value(self):
        cache.save_setting("timezone", "Asia/Tokyo", self.db_path)
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'name="timezone"' in resp.content
        assert b'value="Asia/Tokyo"' in resp.content
        assert b"combobox" in resp.content

    def test_settings_page_defaults_to_device_timezone(self):
        # With no saved timezone, the page should default to the device's zone.
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web, "_LOCAL_TIMEZONE", "Europe/Berlin"),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'value="Europe/Berlin"' in resp.content

    def test_settings_preferences_save_valid(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/preferences",
                data={"date_format": "iso", "sender_display": "name"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast") == "Updated"
        assert cache.get_setting("date_format", self.db_path) == "iso"
        assert cache.get_setting("sender_display", self.db_path) == "name"

    def test_settings_preferences_rejects_invalid(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/preferences",
                data={"date_format": "nonsense", "sender_display": "also-bad"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        # Invalid inputs fall back to the defaults.
        assert cache.get_setting("date_format", self.db_path) == "default"
        assert cache.get_setting("sender_display", self.db_path) == "both"

    def test_settings_page_preferences_selects_carry_saved_values(self):
        cache.save_setting("date_format", "iso", self.db_path)
        cache.save_setting("sender_display", "email", self.db_path)
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'name="date_format"' in resp.content
        assert b'value="iso" selected' in resp.content
        assert b'name="sender_display"' in resp.content
        assert b'value="email" selected' in resp.content

    def test_settings_theme_save_valid(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/theme",
                data={"theme": "dark"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast") == "Updated"
        assert cache.get_setting("theme", self.db_path) == "dark"

    def test_settings_theme_rejects_invalid(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/theme",
                data={"theme": "neon"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        # Invalid input falls back to the default.
        assert cache.get_setting("theme", self.db_path) == "system"

    def test_settings_page_theme_select_carries_saved_value(self):
        cache.save_setting("theme", "dark", self.db_path)
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'name="theme"' in resp.content
        assert b'value="dark" selected' in resp.content

    def test_settings_page_size_save_valid(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/page-size",
                data={"page_size": "50"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast") == "Updated"
        assert cache.get_setting("page_size", self.db_path) == "50"

    def test_settings_page_size_rejects_invalid_out_of_set(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/page-size",
                data={"page_size": "999"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        # Out-of-set input falls back to the default.
        assert cache.get_setting("page_size", self.db_path) == str(web._DEFAULT_PAGE_SIZE)

    def test_settings_page_size_rejects_non_numeric(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/page-size",
                data={"page_size": "abc"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        # Non-numeric input falls back to the default.
        assert cache.get_setting("page_size", self.db_path) == str(web._DEFAULT_PAGE_SIZE)

    def test_settings_page_size_select_carries_saved_value(self):
        cache.save_setting("page_size", "100", self.db_path)
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'name="page_size"' in resp.content
        assert b'value="100" selected' in resp.content

    def test_settings_thread_replies_save_valid(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/thread-replies",
                data={"thread_replies": "10"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast") == "Updated"
        assert cache.get_setting("thread_limit", self.db_path) == "10"

    def test_settings_thread_replies_save_all(self):
        for raw in ("all", "ALL", "0"):
            cache.delete_setting("thread_limit", self.db_path)
            with (
                patch.object(web, "DB_PATH", self.db_path),
                patch.object(web, "_is_docker", lambda: False),
                patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
                patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
            ):
                client = self._make_client()
                resp = client.post(
                    "/settings/thread-replies",
                    data={"thread_replies": raw},
                    follow_redirects=False,
                )
            assert resp.status_code == 204
            assert cache.get_setting("thread_limit", self.db_path) == "0"

    def test_settings_thread_replies_rejects_invalid_out_of_set(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/thread-replies",
                data={"thread_replies": "999"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert cache.get_setting("thread_limit", self.db_path) == str(web._DEFAULT_THREAD_LIMIT)

    def test_settings_thread_replies_rejects_non_numeric(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.post(
                "/settings/thread-replies",
                data={"thread_replies": "abc"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert cache.get_setting("thread_limit", self.db_path) == str(web._DEFAULT_THREAD_LIMIT)

    def test_settings_thread_replies_select_carries_saved_value(self):
        cache.save_setting("thread_limit", "10", self.db_path)
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'name="thread_replies"' in resp.content
        assert b'value="10" selected' in resp.content

    def test_settings_page_renders_tab_nav(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(web, "_is_docker", lambda: False),
            patch.object(web.updater, "get_current_version", lambda: "1.0.0"),
            patch.object(web.updater, "fetch_latest_version", lambda force=False: None),
        ):
            client = self._make_client()
            resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'class="settings-tabs"' in resp.content
        assert b'data-tab="preferences"' in resp.content
        assert b'data-tab="system"' in resp.content
        assert b'data-tab="security"' in resp.content

    def test_format_sender_modes(self):
        raw = "Jane Doe <jane@example.com>"
        both = str(web._format_sender(raw, "both"))
        assert "sender-name" in both and "Jane Doe" in both
        assert "sender-email" in both and "jane@example.com" in both
        assert str(web._format_sender(raw, "name")) == "Jane Doe"
        assert str(web._format_sender(raw, "email")) == "jane@example.com"

    def test_format_sender_name_only_falls_back_to_email(self):
        raw = "plain@example.com"
        assert str(web._format_sender(raw, "name")) == "plain@example.com"
        assert str(web._format_sender(raw, "email")) == "plain@example.com"
        # "both" with no name renders only the email line.
        both = str(web._format_sender(raw, "both"))
        assert "sender-email" in both and "sender-name" not in both

    def test_format_sender_default_reads_setting(self):
        cache.save_setting("sender_display", "name", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            assert str(web._format_sender("Jane Doe <jane@example.com>")) == "Jane Doe"

    def test_timezone_groups_contains_all_major_zones(self):
        tz_ids = set(web._flat_timezone_ids())
        assert "Asia/Tokyo" in tz_ids
        assert "America/New_York" in tz_ids
        assert "Europe/London" in tz_ids
        assert len(tz_ids) > 100
        assert "UTC" not in tz_ids
        assert "UCT" not in tz_ids
        assert "Universal" not in tz_ids
        assert "GMT" not in tz_ids
        assert "Etc/UTC" not in tz_ids
        assert "US/Eastern" not in tz_ids
        assert all("/" in tz for tz in tz_ids)

    def test_detect_local_timezone_from_etc_timezone(self):
        from io import StringIO

        original_open = open

        def fake_open(path, *args, **kwargs):
            if str(path) == "/etc/timezone":
                return StringIO("Asia/Kolkata\n")
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", fake_open):
            assert web._detect_local_timezone() == "Asia/Kolkata"

    def test_detect_local_timezone_from_localtime_symlink(self):
        import builtins

        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if str(path) == "/etc/timezone":
                raise FileNotFoundError
            return original_open(path, *args, **kwargs)

        with (
            patch("builtins.open", fake_open),
            patch("os.path.realpath", return_value="/usr/share/zoneinfo/America/New_York"),
        ):
            assert web._detect_local_timezone() == "America/New_York"

    def test_detect_local_timezone_falls_back_to_utc(self):
        import builtins

        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if str(path) == "/etc/timezone":
                raise FileNotFoundError
            return original_open(path, *args, **kwargs)

        with (
            patch("builtins.open", fake_open),
            patch("os.path.realpath", return_value="/etc/localtime"),
        ):
            assert web._detect_local_timezone() == "UTC"


class TestComposePartial:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "compose_test.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("me@e.com", "secret", self.db_path)
        self.email = {
            "message_id": "<orig@e.com>",
            "from": "Alice <alice@example.com>",
            "subject": "Lunch?",
            "date": "Mon, 01 Jan 2024 10:00:00 +0000",
            "body": "Want to grab lunch?",
            "thread_id": _TID,
            "gm_thrid": _TID,
            "in_reply_to": "",
        }
        from src.tests._helpers import save_fetched

        save_fetched(self.email, self.db_path)
        self.hash = cache._hash_message_id(self.email["message_id"])

    def _client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def test_reply_partial_renders(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/partials/compose/{self.hash}?mode=reply")
        assert resp.status_code == 200
        body = resp.text
        assert "alice@example.com" in body
        assert 'name="to"' in body
        assert 'name="subject"' in body
        assert 'name="body"' in body
        assert "Re: Lunch?" in body
        assert "compose-form" in body

    def test_forward_partial_renders_with_quote(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/partials/compose/{self.hash}?mode=forward")
        assert resp.status_code == 200
        body = resp.text
        assert "Fwd: Lunch?" in body
        assert "Want to grab lunch?" in body

    def test_forward_partial_leaves_to_blank(self):
        import re

        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/partials/compose/{self.hash}?mode=forward")
        to_input = re.search(r'<input[^>]*name="to"[^>]*>', resp.text).group(0)
        assert 'value=""' in to_input

    def test_reply_partial_prefills_sender(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/partials/compose/{self.hash}?mode=reply")
        assert "alice@example.com" in resp.text

    def test_unknown_email_returns_404(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get("/partials/compose/nonexistent?mode=reply")
        assert resp.status_code == 404

    def test_forward_attachments_stages_originals_under_draft(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(
                email_reader,
                "fetch_original_attachments",
                return_value=[
                    {"filename": "report.pdf", "mime_type": "application/pdf", "payload": b"%PDF-1.4"},
                ],
            ),
        ):
            client = self._client()
            resp = client.post(f"/partials/forward-attachments/{self.hash}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["attachments"]) == 1
        assert data["attachments"][0]["filename"] == "report.pdf"
        draft_id = data["draft_id"]
        stored = cache.list_attachments(self.db_path, draft_id, cache.OWNER_DRAFT)
        assert len(stored) == 1
        assert cache.attachment_path(stored[0]).is_file()

    def test_forward_attachments_graceful_when_imap_fails(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(email_reader, "fetch_original_attachments", return_value=[]),
        ):
            client = self._client()
            resp = client.post(f"/partials/forward-attachments/{self.hash}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["attachments"] == []
        assert "notice" in data

    def test_forward_attachments_unknown_hash_404(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.post("/partials/forward-attachments/nonexistent")
        assert resp.status_code == 404

    def test_forward_attachments_accepts_blocked_extension_received_file(self):
        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(
                email_reader,
                "fetch_original_attachments",
                return_value=[
                    {"filename": "script.py", "mime_type": "text/x-python", "payload": b"print('hi')"},
                ],
            ),
        ):
            client = self._client()
            resp = client.post(f"/partials/forward-attachments/{self.hash}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["attachments"]) == 1
        assert data["attachments"][0]["filename"] == "script.py"
        stored = cache.list_attachments(self.db_path, data["draft_id"], cache.OWNER_DRAFT)
        assert len(stored) == 1
        assert cache.read_attachment_bytes(stored[0]) == b"print('hi')"

    def test_forward_attachments_cleans_up_on_partial_write_failure(self):
        from src.scripts.cache.db import _connect

        parts = [
            {"filename": "a.txt", "mime_type": "text/plain", "payload": b"a"},
            {"filename": "b.txt", "mime_type": "text/plain", "payload": b"b"},
        ]
        original_store = cache.store_staged_received
        call = {"n": 0}

        def store_then_fail(db_path, owner_id, owner_type, items):
            call["n"] += 1
            original_store(db_path, owner_id, owner_type, [items[0]])
            raise RuntimeError("disk full")

        with (
            patch.object(web, "DB_PATH", self.db_path),
            patch.object(email_reader, "fetch_original_attachments", return_value=parts),
            patch.object(cache, "store_staged_received", side_effect=store_then_fail),
        ):
            client = self._client()
            resp = client.post(f"/partials/forward-attachments/{self.hash}")
        assert resp.status_code == 500
        assert call["n"] == 1
        with _connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM attachments WHERE owner_type = ?", (cache.OWNER_DRAFT,)).fetchone()[
                0
            ]
        assert n == 0

    def test_abandon_endpoint_deletes_unpersisted_draft_attachments(self):
        owner_id = "abandon-1"
        created = cache.store_attachment_bytes(
            self.db_path,
            owner_id,
            cache.OWNER_DRAFT,
            [{"filename": "f.txt", "mime_type": "text/plain", "payload": b"x"}],
        )
        path = cache.attachment_path(created[0])
        assert path.is_file()

        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.post(f"/drafts/{owner_id}/abandon")

        assert resp.status_code == 204
        assert cache.list_attachments(self.db_path, owner_id, cache.OWNER_DRAFT) == []
        assert not path.exists()

    def test_abandon_is_noop_when_nothing_exists(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.post("/drafts/never-existed/abandon")
        assert resp.status_code == 204

    def test_compose_partial_escapes_xss_in_quoted_body(self):
        from src.tests._helpers import save_fetched

        save_fetched(
            {
                "message_id": "<xss@e.com>",
                "from": "Evil <evil@example.com>",
                "subject": "<script>alert(1)</script>",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "<script>alert('xss')</script><img src=x onerror=alert(1)>",
                "thread_id": _TID,
                "gm_thrid": _TID,
                "in_reply_to": "",
            },
            self.db_path,
        )
        xss_hash = cache._hash_message_id("<xss@e.com>")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/partials/compose/{xss_hash}?mode=forward")
        assert resp.status_code == 200
        body = resp.text
        assert "<script>alert('xss')</script>" not in body
        assert "<img src=x onerror=alert(1)>" not in body
        assert "&lt;script&gt;" in body
        assert "&lt;img" in body

    def test_compose_partial_escapes_xss_in_subject(self):
        from src.tests._helpers import save_fetched

        save_fetched(
            {
                "message_id": "<xss2@e.com>",
                "from": "x@e.com",
                "subject": "Hi <b>bold</b><script>alert(1)</script>",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "body",
                "thread_id": _TID,
                "gm_thrid": _TID,
                "in_reply_to": "",
            },
            self.db_path,
        )
        xss_hash = cache._hash_message_id("<xss2@e.com>")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/partials/compose/{xss_hash}?mode=reply")
        body = resp.text
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body


class TestComposeSend:
    def test_send_new_email(self, web_client, monkeypatch):
        captured = {}

        def fake_send(to_addrs, cc_addrs, bcc_addrs, subject, body_md, body_html, mode, **kw):
            captured.update(
                to=to_addrs,
                cc=cc_addrs,
                bcc=bcc_addrs,
                subject=subject,
                body_md=body_md,
                body_html=body_html,
                mode=mode,
                kw=kw,
            )

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "alice@example.com",
                "cc": "",
                "bcc": "",
                "subject": "Hi",
                "body": "Hello **world**",
                "mode": "new",
            },
        )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "success"
        assert captured["to"] == ["alice@example.com"]
        assert captured["cc"] == []
        assert captured["bcc"] == []
        assert captured["subject"] == "Hi"
        assert captured["body_md"] == "Hello **world**"
        assert captured["mode"] == "new"
        assert "<strong>world</strong>" in captured["body_html"]

    def test_send_with_cc_and_bcc(self, web_client, monkeypatch):
        captured = {}

        def fake_send(*args, **kw):
            captured.update(to=args[0], cc=args[1], bcc=args[2])

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "to@x.com",
                "cc": "a@x.com, b@x.com",
                "bcc": "secret@x.com",
                "subject": "Hi",
                "body": "Body",
                "mode": "new",
            },
        )
        assert resp.status_code == 204
        assert captured["to"] == ["to@x.com"]
        assert captured["cc"] == ["a@x.com", "b@x.com"]
        assert captured["bcc"] == ["secret@x.com"]

    def test_send_uploads_attachments(self, web_client, monkeypatch):
        captured = {}

        def fake_send(*args, **kw):
            captured["attachments"] = kw.get("attachments")

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "to@x.com",
                "cc": "",
                "bcc": "",
                "subject": "Hi",
                "body": "Body",
                "mode": "new",
            },
            files=[("attachments", ("file.txt", b"hello", "text/plain"))],
        )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "success"
        atts = captured["attachments"]
        assert atts and len(atts) == 1
        assert atts[0]["filename"] == "file.txt"
        from src.scripts import web as _web

        assert cache.list_attachments(_web.DB_PATH, atts[0]["owner_id"], cache.OWNER_OUTBOX) == []

    def test_send_rejects_blocked_attachment_type(self, web_client, monkeypatch):
        called = {"n": 0}

        def fake_send(*a, **k):
            called["n"] += 1

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "to@x.com",
                "subject": "Hi",
                "body": "Body",
                "mode": "new",
            },
            files=[("attachments", ("evil.exe", b"MZ", "application/x-msdownload"))],
        )
        assert resp.headers.get("X-Toast-Tone") == "error"
        assert called["n"] == 0

    def test_send_failure_enqueues_attachments_for_retry(self, web_client, monkeypatch):
        def fail(*a, **k):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(email_reader, "send_message", fail)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "to@x.com",
                "subject": "Hi",
                "body": "Body",
                "mode": "new",
            },
            files=[("attachments", ("keep.txt", b"data", "text/plain"))],
        )
        assert resp.headers.get("X-Toast-Tone") == "warning"
        from src.scripts import web as _web

        rows = cache.list_outbox(_web.DB_PATH)
        assert len(rows) == 1
        oid = rows[0]["id"]
        atts = cache.list_attachments(_web.DB_PATH, oid, cache.OWNER_OUTBOX)
        assert len(atts) == 1
        assert atts[0]["filename"] == "keep.txt"
        assert cache.attachment_path(atts[0]).is_file()

    def test_send_enqueue_failure_rolls_back_draft_attachments(self, web_client, monkeypatch):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d-rollback",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        created = cache.store_attachment_bytes(
            _web.DB_PATH,
            "d-rollback",
            cache.OWNER_DRAFT,
            [{"filename": "f.txt", "mime_type": "text/plain", "payload": b"x"}],
        )
        path = cache.attachment_path(created[0])

        def fail(*a, **k):
            raise RuntimeError("smtp down")

        def boom_enqueue(*a, **k):
            raise RuntimeError("db locked")

        monkeypatch.setattr(email_reader, "send_message", fail)
        monkeypatch.setattr(cache, "enqueue", boom_enqueue)
        resp = web_client.post(
            "/emails/send",
            data={"to": "to@x.com", "subject": "S", "body": "B", "mode": "new", "draft_id": "d-rollback"},
        )
        assert resp.headers.get("X-Toast-Tone") == "error"
        kept = cache.list_attachments(_web.DB_PATH, "d-rollback", cache.OWNER_DRAFT)
        assert len(kept) == 1
        assert path.is_file()

    def test_forward_round_trip_attaches_originals(self, web_client, monkeypatch):
        from src.scripts import web as _web

        _save_fetched(
            {
                "message_id": "<orig-fwd@e.com>",
                "from": "alice@e.com",
                "subject": "Lunch?",
                "date": "Mon, 01 Jan 2024 10:00:00 +0000",
                "body": "Want to grab lunch?",
                "thread_id": "t-fwd",
                "gm_thrid": "t-fwd",
                "in_reply_to": "",
            },
            _web.DB_PATH,
        )
        fwd_hash = cache._hash_message_id("<orig-fwd@e.com>")
        monkeypatch.setattr(
            email_reader,
            "fetch_original_attachments",
            lambda *a, **k: [
                {"filename": "menu.pdf", "mime_type": "application/pdf", "payload": b"%PDF menu"},
            ],
        )
        resp = web_client.post(f"/partials/forward-attachments/{fwd_hash}")
        draft_id = resp.json()["draft_id"]

        captured = {}

        def fake_send(*a, **k):
            captured["attachments"] = k.get("attachments")

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "bob@x.com",
                "subject": "Fwd: Lunch?",
                "body": "FYI",
                "mode": "forward",
                "original_message_id": "<orig-fwd@e.com>",
                "draft_id": draft_id,
            },
        )
        assert resp.headers.get("X-Toast-Tone") == "success"
        atts = captured["attachments"]
        assert atts and len(atts) == 1
        assert atts[0]["filename"] == "menu.pdf"
        assert cache.list_attachments(_web.DB_PATH, draft_id, cache.OWNER_DRAFT) == []

    def test_invalid_recipient_rejected(self, web_client, monkeypatch):
        called = {"n": 0}

        def fake_send(*a, **k):
            called["n"] += 1

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "not-an-email",
                "cc": "",
                "bcc": "",
                "subject": "Hi",
                "body": "Body",
                "mode": "new",
            },
        )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "error"
        assert called["n"] == 0

    def test_empty_to_rejected(self, web_client, monkeypatch):
        called = {"n": 0}

        def fake_send(*a, **k):
            called["n"] += 1

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "",
                "cc": "",
                "bcc": "",
                "subject": "Hi",
                "body": "Body",
                "mode": "new",
            },
        )
        assert resp.headers.get("X-Toast-Tone") == "error"
        assert called["n"] == 0

    def test_reply_passes_threading(self, web_client, monkeypatch):
        captured = {}

        def fake_send(*args, **kw):
            captured.update(kw=kw, mode=args[6])

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "alice@example.com",
                "cc": "",
                "bcc": "",
                "subject": "Re: Lunch?",
                "body": "Sure!",
                "mode": "reply",
                "original_message_id": "<orig@e.com>",
                "thread_id": "thread-1",
            },
        )
        assert resp.status_code == 204
        assert captured["mode"] == "reply"
        assert captured["kw"]["original_message_id"] == "<orig@e.com>"
        assert captured["kw"]["thread_id"] == "thread-1"

    def test_draft_deleted_on_success(self, web_client, monkeypatch):
        from src.scripts import web as _web

        monkeypatch.setattr(email_reader, "send_message", lambda *a, **k: None)
        cache.save_draft(
            {
                "id": "d1",
                "to_addr": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "draft_id": "d1",
            },
        )
        assert resp.status_code == 204
        assert cache.get_draft("d1", _web.DB_PATH) is None

    def test_send_failure_enqueues_to_outbox_and_drops_draft(self, web_client, monkeypatch):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d2",
                "to_addr": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )

        def boom(*a, **k):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(email_reader, "send_message", boom)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "draft_id": "d2",
            },
        )
        assert resp.headers.get("X-Toast-Tone") == "warning"
        assert "Outbox" in resp.headers.get("X-Toast", "")
        assert cache.get_draft("d2", _web.DB_PATH) is None
        outbox_rows = cache.list_outbox(_web.DB_PATH)
        assert len(outbox_rows) == 1
        row = outbox_rows[0]
        assert row["to_addr"] == "a@x.com"
        assert row["subject"] == "S"
        assert row["status"] == "pending"
        assert row["draft_id"] == "d2"

    def test_send_failure_enqueue_fallback_toast(self, web_client, monkeypatch):
        from src.scripts import web as _web

        def boom_send(*a, **k):
            raise RuntimeError("smtp down")

        def boom_enqueue(*a, **k):
            raise RuntimeError("db gone")

        monkeypatch.setattr(email_reader, "send_message", boom_send)
        monkeypatch.setattr(cache, "enqueue", boom_enqueue)
        resp = web_client.post(
            "/emails/send",
            data={"to": "a@x.com", "cc": "", "bcc": "", "subject": "S", "body": "B", "mode": "new"},
        )
        assert resp.headers.get("X-Toast-Tone") == "error"
        assert cache.list_outbox(_web.DB_PATH) == []

    def test_rate_limited(self, web_client, monkeypatch):
        from src.scripts import auth as _auth
        from src.scripts import web as _web

        cache.save_setting("email_user", "tester@e.com", _web.DB_PATH)
        for _ in range(15):
            _auth.send_rate_limiter.record_failure("tester@e.com")
        monkeypatch.setattr(email_reader, "send_message", lambda *a, **k: None)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
            },
        )
        assert resp.headers.get("X-Toast-Tone") == "warning"
        _auth.send_rate_limiter.reset("tester@e.com")

    def test_compose_new_partial(self, web_client):
        resp = web_client.get("/partials/compose-new")
        assert resp.status_code == 200
        assert "compose-body" in resp.text
        assert 'name="mode" value="new"' in resp.text

    def test_compose_partial_attachment_hint_reflects_setting(self, web_client):
        from src.scripts import web as _web

        resp = web_client.get("/partials/compose-new")
        assert "10 MB per file" in resp.text
        assert "up to" not in resp.text
        cache.save_setting("max_attachment_size", str(25 * 1024 * 1024), _web.DB_PATH)
        resp = web_client.get("/partials/compose-new")
        assert "25 MB per file" in resp.text
        assert "Max 10 MB per file, 25 MB total" not in resp.text
        assert "up to" not in resp.text

    def test_csrf_rejects_bad_origin_on_send(self, web_client, monkeypatch):
        from src.scripts import auth, web

        auth.set_password("dashboardpw", web.DB_PATH)
        web_client.post("/login", data={"password": "dashboardpw", "next": "/"}, follow_redirects=False)
        called = {"n": 0}
        monkeypatch.setattr(email_reader, "send_message", lambda *a, **k: called.update(n=called["n"] + 1))
        resp = web_client.post(
            "/emails/send",
            data={"to": "a@x.com", "cc": "", "bcc": "", "subject": "Hi", "body": "B", "mode": "new"},
            headers={"origin": "http://evil.com", "host": "testserver"},
        )
        assert resp.status_code == 403
        assert called["n"] == 0  # send_message never invoked

    def test_csrf_allows_matching_origin_on_send(self, web_client, monkeypatch):
        from src.scripts import auth, web

        auth.set_password("dashboardpw", web.DB_PATH)
        web_client.post("/login", data={"password": "dashboardpw", "next": "/"}, follow_redirects=False)
        monkeypatch.setattr(email_reader, "send_message", lambda *a, **k: None)
        resp = web_client.post(
            "/emails/send",
            data={"to": "a@x.com", "cc": "", "bcc": "", "subject": "Hi", "body": "B", "mode": "new"},
            headers={"origin": "http://testserver", "host": "testserver"},
        )
        assert resp.status_code == 204


class TestDrafts:
    def test_save_draft(self, web_client):
        from src.scripts import web as _web

        resp = web_client.post(
            "/drafts/save",
            data={
                "draft_id": "d10",
                "to": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "original_message_id": "",
                "thread_id": "",
            },
        )
        assert resp.status_code == 204
        got = cache.get_draft("d10", _web.DB_PATH)
        assert got is not None
        assert got["to_addr"] == "a@x.com"
        assert got["subject"] == "S"

    def test_drafts_page_lists_drafts(self, web_client):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d11",
                "to_addr": "a@x.com",
                "cc": "",
                "bcc": "",
                "subject": "My draft",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        resp = web_client.get("/drafts")
        assert resp.status_code == 200
        assert "My draft" in resp.text

    def test_delete_draft_route(self, web_client):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d12",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "X",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        resp = web_client.post("/drafts/d12/delete", follow_redirects=False)
        assert resp.status_code in (303, 204)
        assert cache.get_draft("d12", _web.DB_PATH) is None

    def test_draft_attach_and_detach(self, web_client):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d-att",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        resp = web_client.post(
            "/drafts/d-att/attachments",
            files=[("attachments", ("doc.txt", b"draft-bytes", "text/plain"))],
        )
        assert resp.status_code == 200
        atts = resp.json()["attachments"]
        assert len(atts) == 1
        assert atts[0]["filename"] == "doc.txt"
        stored = cache.list_attachments(_web.DB_PATH, "d-att", cache.OWNER_DRAFT)
        assert len(stored) == 1
        assert cache.attachment_path(stored[0]).is_file()

        resp = web_client.delete(f"/drafts/d-att/attachments/{atts[0]['id']}")
        assert resp.status_code == 200
        assert cache.list_attachments(_web.DB_PATH, "d-att", cache.OWNER_DRAFT) == []
        resp = web_client.delete("/drafts/d-att/attachments/nope")
        assert resp.status_code == 404

    def test_draft_detach_rejects_attachment_owned_by_another_draft(self, web_client):
        from src.scripts import web as _web

        for draft_id in ("d-owner", "d-other"):
            cache.save_draft(
                {
                    "id": draft_id,
                    "to_addr": "",
                    "cc": "",
                    "bcc": "",
                    "subject": "S",
                    "body": "B",
                    "mode": "new",
                    "thread_id": None,
                    "in_reply_to": None,
                },
                _web.DB_PATH,
            )
        created = cache.store_attachment_bytes(
            _web.DB_PATH,
            "d-owner",
            cache.OWNER_DRAFT,
            [{"filename": "owned.txt", "mime_type": "text/plain", "payload": b"owner"}],
        )
        att_id = created[0]["id"]
        path = cache.attachment_path(created[0])

        resp = web_client.delete(f"/drafts/d-other/attachments/{att_id}")
        assert resp.status_code == 404
        still = cache.list_attachments(_web.DB_PATH, "d-owner", cache.OWNER_DRAFT)
        assert len(still) == 1 and still[0]["id"] == att_id
        assert cache.list_attachments(_web.DB_PATH, "d-other", cache.OWNER_DRAFT) == []
        assert path.is_file()

    def test_draft_attach_rejects_blocked_type(self, web_client):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d-bad",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        resp = web_client.post(
            "/drafts/d-bad/attachments",
            files=[("attachments", ("evil.exe", b"MZ", "application/x-msdownload"))],
        )
        assert resp.status_code == 400
        assert cache.list_attachments(_web.DB_PATH, "d-bad", cache.OWNER_DRAFT) == []

    def test_delete_draft_cleans_up_attachments(self, web_client):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d-cleanup",
                "to_addr": "",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "B",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        created = cache.store_attachment_bytes(
            _web.DB_PATH,
            "d-cleanup",
            cache.OWNER_DRAFT,
            [{"filename": "f.txt", "mime_type": "text/plain", "payload": b"x"}],
        )
        path = cache.attachment_path(created[0])
        assert path.is_file()
        web_client.post("/drafts/d-cleanup/delete", follow_redirects=False)
        assert cache.list_attachments(_web.DB_PATH, "d-cleanup", cache.OWNER_DRAFT) == []
        assert not path.exists()

    def test_get_draft_json(self, web_client):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d13",
                "to_addr": "a@x.com",
                "cc": "c@x.com",
                "bcc": "",
                "subject": "S",
                "body": "Body",
                "mode": "reply",
                "thread_id": "t1",
                "in_reply_to": "<o@x.com>",
            },
            _web.DB_PATH,
        )
        resp = web_client.get("/drafts/d13.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "d13"
        assert data["cc"] == "c@x.com"
        assert data["in_reply_to"] == "<o@x.com>"

    def test_missing_draft_json_returns_404(self, web_client):
        resp = web_client.get("/drafts/does-not-exist.json")
        assert resp.status_code == 404

    def test_save_draft_rejects_oversized_subject(self, web_client):
        from src.scripts import web as _web

        resp = web_client.post(
            "/drafts/save",
            data={
                "draft_id": "big1",
                "subject": "x" * 501,
                "body": "B",
                "mode": "new",
            },
        )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "error"
        assert cache.get_draft("big1", _web.DB_PATH) is None

    def test_save_draft_rejects_oversized_body(self, web_client):
        from src.scripts import web as _web

        resp = web_client.post(
            "/drafts/save",
            data={
                "draft_id": "big2",
                "subject": "S",
                "body": "x" * 50001,
                "mode": "new",
            },
        )
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "error"
        assert cache.get_draft("big2", _web.DB_PATH) is None

    def test_compose_draft_endpoint_prefills_fields(self, web_client):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d14",
                "to_addr": "to@x.com",
                "cc": "cc@x.com",
                "bcc": "bcc@x.com",
                "subject": "Draft subject",
                "body": "Draft body",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        resp = web_client.get("/partials/compose-draft/d14")
        assert resp.status_code == 200
        assert "to@x.com" in resp.text
        assert "cc@x.com" in resp.text
        assert "bcc@x.com" in resp.text
        assert "Draft subject" in resp.text
        assert "Draft body" in resp.text
        assert 'value="d14"' in resp.text

    def test_compose_draft_endpoint_404_for_missing(self, web_client):
        resp = web_client.get("/partials/compose-draft/nope")
        assert resp.status_code == 404

    def test_emails_page_has_fab(self, web_client):
        resp = web_client.get("/emails")
        assert resp.status_code == 200
        assert "compose-fab" in resp.text

    def test_drafts_link_in_nav(self, web_client):
        resp = web_client.get("/emails")
        assert resp.status_code == 200
        assert 'href="/drafts"' in resp.text
        assert "folder-dropdown" in resp.text
        assert "Drafts" in resp.text


class TestComposeModalShell:
    def test_compose_modal_present_on_email_detail(self, web_db):
        from fastapi.testclient import TestClient

        from src.tests._helpers import save_fetched

        cache.save_email_credentials("me@e.com", "secret", web_db)
        email = {
            "message_id": "<shell@e.com>",
            "from": "a@b.com",
            "subject": "S",
            "date": "Mon, 01 Jan 2024 10:00:00 +0000",
            "body": "B",
            "thread_id": _TID,
            "gm_thrid": _TID,
            "in_reply_to": "",
        }
        save_fetched(email, web_db)
        h = cache._hash_message_id(email["message_id"])
        with patch.object(web, "DB_PATH", web_db):
            client = TestClient(web.app)
            resp = client.get(f"/emails/{h}")
        body = resp.text
        assert 'id="compose-modal"' in body
        assert 'id="compose-modal-body"' in body
        assert "compose.css" in body
        assert "compose.js" in body

    def test_compose_modal_has_aria_dialog_attributes(self, web_db):
        from fastapi.testclient import TestClient

        from src.tests._helpers import save_fetched

        cache.save_email_credentials("me@e.com", "secret", web_db)
        email = {
            "message_id": "<aria@e.com>",
            "from": "a@b.com",
            "subject": "S",
            "date": "Mon, 01 Jan 2024 10:00:00 +0000",
            "body": "B",
            "thread_id": _TID,
            "gm_thrid": _TID,
            "in_reply_to": "",
        }
        save_fetched(email, web_db)
        h = cache._hash_message_id(email["message_id"])
        with patch.object(web, "DB_PATH", web_db):
            client = TestClient(web.app)
            resp = client.get(f"/emails/{h}")
        body = resp.text
        assert 'role="dialog"' in body
        assert 'aria-modal="true"' in body
        assert 'aria-labelledby="compose-modal-title"' in body


class TestEmailDetailConversation:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.db_path = str(tmp_path / "thread_test.db")
        cache.init_db(self.db_path)
        cache.save_email_credentials("me@e.com", "secret", self.db_path)
        from src.tests._helpers import save_fetched

        self.save_fetched = save_fetched
        self.root = {
            "message_id": "<root@e.com>",
            "from": "Alice <alice@e.com>",
            "subject": "Topic",
            "date": "Mon, 01 Jan 2024 10:00:00 +0000",
            "body": "root body",
            "thread_id": _TID,
            "gm_thrid": _TID,
            "in_reply_to": "",
        }
        save_fetched(self.root, self.db_path)
        self.root_hash = cache._hash_message_id(self.root["message_id"])

    def _client(self):
        from fastapi.testclient import TestClient

        return TestClient(web.app)

    def _add_reply(self, mid, sender, day):
        self.save_fetched(
            {
                "message_id": mid,
                "from": sender,
                "subject": "Re: Topic",
                "date": f"Tue, 0{day} Jan 2024 10:00:00 +0000",
                "body": "reply",
                "thread_id": _TID,
                "gm_thrid": _TID,
                "in_reply_to": "<root@e.com>",
            },
            self.db_path,
        )

    def test_detail_page_shows_full_thread(self):
        self._add_reply("<r1@e.com>", "Bob <bob@e.com>", 2)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/emails/{self.root_hash}")
        assert resp.status_code == 200
        assert "2 messages in this conversation" in resp.text
        assert "alice@e.com" in resp.text  # root sender
        assert "bob@e.com" in resp.text  # reply sender

    def test_detail_page_no_conversation_meta_for_single_message(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/emails/{self.root_hash}")
        assert resp.status_code == 200
        assert "in this conversation" not in resp.text

    def test_conversation_ordered_oldest_first(self):
        self._add_reply("<r1@e.com>", "u1@e.com", 2)
        self._add_reply("<r2@e.com>", "u2@e.com", 3)
        r1_hash = cache._hash_message_id("<r1@e.com>")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/emails/{r1_hash}")
        body = resp.text
        assert body.index("alice@e.com") < body.index("u1@e.com") < body.index("u2@e.com")

    def test_body_strips_quoted_history_in_conversation(self):
        self.save_fetched(
            {
                "message_id": "<quoted-reply@e.com>",
                "from": "Bob <bob@e.com>",
                "subject": "Re: Topic",
                "date": "Tue, 02 Jan 2024 10:00:00 +0000",
                "body": ("Here is my fresh reply.\n\nOn Mon, 1 Jan 2024 10:00:00 +0000, Alice wrote:\n> root body\n"),
                "thread_id": _TID,
                "gm_thrid": _TID,
                "in_reply_to": "<root@e.com>",
            },
            self.db_path,
        )
        reply_hash = cache._hash_message_id("<quoted-reply@e.com>")
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            detail = client.get(f"/emails/{reply_hash}")
        assert detail.status_code == 200
        assert "Here is my fresh reply." in detail.text
        assert "On Mon, 1 Jan 2024" not in detail.text
        assert "root body" in detail.text

    def test_gmail_synced_reply_appears_in_conversation(self, monkeypatch):
        from unittest.mock import MagicMock

        from src.scripts.email_reader import imap as imap_mod
        from src.tests._helpers import fake_imap_session

        sent_raw = (
            "From: me@e.com\r\n"
            "Subject: Re: Topic\r\n"
            "Date: Tue, 02 Jan 2024 10:00:00 +0000\r\n"
            "Message-ID: <gmail-reply@e.com>\r\n"
            "In-Reply-To: <root@e.com>\r\n"
            "References: <root@e.com>\r\n"
            "\r\n"
            "sent from gmail\r\n"
        ).encode()

        fake = MagicMock()
        fake.list.return_value = ("OK", [b'(\\Sent) "/" "Sent"'])
        fake.select.return_value = ("OK", [b""])
        fake.uid.side_effect = [
            ("OK", [b"1"]),
            ("OK", [(b"UID 1 X-GM-THRID 1809095669921875987", sent_raw)]),  # header fetch
            ("OK", [b"1"]),
            ("OK", [(b"UID 1", sent_raw)]),  # body fetch
        ]
        fake_imap_session(monkeypatch, imap_mod, fake)

        result = email_reader.sync_sent_replies(db_path=self.db_path)
        assert result["synced"] == 1

        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/emails/{self.root_hash}")
        assert resp.status_code == 200
        assert "me@e.com" in resp.text
        assert "2 messages in this conversation" in resp.text

    def test_detail_shows_show_more_when_thread_over_limit(self):
        for i, day in enumerate(range(2, 9), 1):
            self._add_reply(f"<r{i}@e.com>", f"u{i}@e.com", day)
        cache.save_setting("thread_limit", "5", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/emails/{self.root_hash}")
        body = resp.text
        assert 'id="thread-overflow"' in body
        assert "thread-show-more" in body
        assert 'id="thread-overflow" hidden' in body
        assert "Show 3 more in conversation" in body

    def test_detail_no_show_more_when_under_limit(self):
        self._add_reply("<r1@e.com>", "u1@e.com", 2)
        cache.save_setting("thread_limit", "5", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/emails/{self.root_hash}")
        body = resp.text
        assert "thread-overflow" not in body
        assert "thread-show-more" not in body

    def test_detail_limit_zero_shows_all(self):
        self._add_reply("<r1@e.com>", "u1@e.com", 2)
        self._add_reply("<r2@e.com>", "u2@e.com", 3)
        cache.save_setting("thread_limit", "0", self.db_path)
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._client()
            resp = client.get(f"/emails/{self.root_hash}")
        body = resp.text
        assert "thread-overflow" not in body
        assert "thread-show-more" not in body


class TestSignature:
    def test_settings_page_shows_signature(self, web_client):
        from src.scripts import web as _web

        cache.save_setting("signature", "-- \nCheers", _web.DB_PATH)
        resp = web_client.get("/settings")
        assert resp.status_code == 200
        assert "Signature" in resp.text
        assert "Cheers" in resp.text

    def test_save_signature(self, web_client):
        from src.scripts import web as _web

        resp = web_client.post("/settings/signature", data={"signature": "-- \nThanks!"})
        assert resp.status_code == 204
        assert cache.get_setting("signature", _web.DB_PATH) == "-- \nThanks!"

    def test_signature_preview_renders_html(self, web_client):
        resp = web_client.get("/settings/signature/preview", params={"text": "**hi**"})
        assert resp.status_code == 200
        assert "<strong>hi</strong>" in resp.text

    def test_signature_preview_strips_script(self, web_client):
        resp = web_client.get("/settings/signature/preview", params={"text": "<script>x</script>**ok**"})
        assert resp.status_code == 200
        assert "<script" not in resp.text
        assert "<strong>ok</strong>" in resp.text

    def test_compose_draft_refreshes_signature_to_current(self, web_client):
        from src.scripts import web as _web

        cache.save_draft(
            {
                "id": "d-sig",
                "to_addr": "to@x.com",
                "cc": "",
                "bcc": "",
                "subject": "S",
                "body": "My reply\n\n-- \nOldSig",
                "mode": "new",
                "thread_id": None,
                "in_reply_to": None,
            },
            _web.DB_PATH,
        )
        cache.save_setting("signature", "NewSig", _web.DB_PATH)

        resp = web_client.get("/partials/compose-draft/d-sig")
        assert resp.status_code == 200
        assert "NewSig" in resp.text
        assert "OldSig" not in resp.text
        assert "My reply" in resp.text

    def test_with_signature_is_idempotent(self, tmp_path, monkeypatch):
        from src.scripts import web as _web

        db_path = str(tmp_path / "test_web.db")
        cache.init_db(db_path)
        monkeypatch.setattr(_web, "DB_PATH", db_path)

        _web.cache.save_setting("signature", "Sig", db_path)
        once = _web._with_signature("Body", db_path)
        twice = _web._with_signature(once, db_path)
        assert once == twice
        assert once.count("-- ") == 1
        assert "Sig" in once
        assert "Body" in once

    def test_with_signature_strips_block_when_setting_cleared(self, tmp_path, monkeypatch):
        from src.scripts import web as _web

        db_path = str(tmp_path / "test_web.db")
        cache.init_db(db_path)
        monkeypatch.setattr(_web, "DB_PATH", db_path)

        _web.cache.save_setting("signature", "", db_path)
        result = _web._with_signature("Body\n\n-- \nStale", db_path)
        assert "-- " not in result
        assert "Stale" not in result
        assert "Body" in result


class TestDraftPruning:
    def test_prune_stale_drafts_drops_old_rows(self, tmp_path):
        db_path = str(tmp_path / "p.db")
        cache.init_db(db_path)
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
            db_path,
        )
        with cache._connect(db_path) as conn:
            conn.execute("UPDATE drafts SET updated_at = '2000-01-01 00:00:00' WHERE id = 'old'")
        removed = cache.prune_stale_drafts(db_path, max_age_days=30)
        assert removed == 1
        assert cache.get_draft("old", db_path) is None


class TestOutbox:
    def _enqueue(self, db_path, **overrides):
        payload = {
            "to_addr": "a@x.com",
            "cc": "",
            "bcc": "",
            "subject": "Queued",
            "body": "B",
            "body_html": "<p>B</p>",
            "mode": "new",
            "original_message_id": None,
            "thread_id": None,
            "draft_id": None,
        }
        payload.update(overrides)
        return cache.enqueue(payload, db_path)

    def test_outbox_page_lists_pending(self, web_client):
        from src.scripts import web as _web

        self._enqueue(_web.DB_PATH, subject="Hello Outbox")
        resp = web_client.get("/outbox")
        assert resp.status_code == 200
        assert "Hello Outbox" in resp.text
        assert "pending" in resp.text

    def test_outbox_page_empty_state(self, web_client):
        resp = web_client.get("/outbox")
        assert resp.status_code == 200
        assert "Outbox is empty" in resp.text

    def test_outbox_retry_requeues_failed(self, web_client):
        from src.scripts import web as _web

        oid = self._enqueue(_web.DB_PATH)
        cache.mark_outbox_failed(oid, "boom", None, _web.DB_PATH)
        assert cache.get_outbox(oid, _web.DB_PATH)["status"] == "failed"
        resp = web_client.post(f"/outbox/{oid}/retry")
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "success"
        row = cache.get_outbox(oid, _web.DB_PATH)
        assert row["status"] == "pending"
        assert row["next_attempt_at"] is None

    def test_outbox_retry_missing(self, web_client):
        resp = web_client.post("/outbox/nope/retry")
        assert resp.status_code == 204
        assert resp.headers.get("X-Toast-Tone") == "error"

    def test_outbox_delete(self, web_client):
        from src.scripts import web as _web

        oid = self._enqueue(_web.DB_PATH)
        resp = web_client.post(f"/outbox/{oid}/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/outbox"
        assert cache.get_outbox(oid, _web.DB_PATH) is None

    def test_outbox_compose_partial_moves_to_draft_and_renders(self, web_client):
        from src.scripts import web as _web

        oid = self._enqueue(_web.DB_PATH, subject="Edit me", to_addr="to@x.com", cc="c@x.com")
        resp = web_client.post(f"/partials/compose-outbox/{oid}")
        assert resp.status_code == 200
        assert "Edit me" in resp.text
        assert "to@x.com" in resp.text
        assert cache.get_outbox(oid, _web.DB_PATH) is None
        draft = cache.get_draft(oid, _web.DB_PATH)
        assert draft is not None
        assert draft["subject"] == "Edit me"
        assert draft["to_addr"] == "to@x.com"
        assert draft["cc"] == "c@x.com"

    def test_outbox_compose_partial_missing(self, web_client):
        resp = web_client.post("/partials/compose-outbox/nope")
        assert resp.status_code == 404

    def test_outbox_page_shows_status_and_attempts(self, web_client):
        from src.scripts import web as _web

        oid = self._enqueue(_web.DB_PATH)
        cache.mark_outbox_failed(oid, "smtp unreachable", None, _web.DB_PATH)
        resp = web_client.get("/outbox")
        assert resp.status_code == 200
        assert "failed" in resp.text
        assert "1 attempt" in resp.text
        assert "smtp unreachable" in resp.text

    def test_edited_outbox_send_success_drops_draft(self, web_client, monkeypatch):
        from src.scripts import web as _web

        oid = self._enqueue(_web.DB_PATH, subject="Retry me", to_addr="to@x.com")
        web_client.post(f"/partials/compose-outbox/{oid}")
        assert cache.get_draft(oid, _web.DB_PATH) is not None
        monkeypatch.setattr(email_reader, "send_message", lambda *a, **k: None)
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "to@x.com",
                "cc": "",
                "bcc": "",
                "subject": "Retry me",
                "body": "Body",
                "mode": "new",
                "draft_id": oid,
            },
        )
        assert resp.headers.get("X-Toast-Tone") == "success"
        assert cache.get_draft(oid, _web.DB_PATH) is None
        assert cache.get_outbox(oid, _web.DB_PATH) is None

    def test_edited_outbox_resend_failure_creates_new_row(self, web_client, monkeypatch):
        from src.scripts import web as _web

        oid = self._enqueue(_web.DB_PATH, subject="Will fail again", to_addr="to@x.com")
        web_client.post(f"/partials/compose-outbox/{oid}")
        monkeypatch.setattr(email_reader, "send_message", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        resp = web_client.post(
            "/emails/send",
            data={
                "to": "to@x.com",
                "cc": "",
                "bcc": "",
                "subject": "Will fail again",
                "body": "Body",
                "mode": "new",
                "draft_id": oid,
            },
        )
        assert resp.headers.get("X-Toast-Tone") == "warning"
        assert cache.get_outbox(oid, _web.DB_PATH) is None
        rows = cache.list_outbox(_web.DB_PATH)
        assert len(rows) == 1
        assert rows[0]["id"] != oid
        assert rows[0]["subject"] == "Will fail again"
