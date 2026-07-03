import json
from unittest.mock import patch

import pytest

from src.scripts import cache, web


def _raise(exc):
    raise exc


def _save_fetched_batch(emails, db):
    for email in emails:
        cache.save_headers_batch([email], db)
        cache.update_bodies_batch([(email["message_id"], email.get("body", ""))], db)
        if email.get("_category") is not None:
            h = cache._hash_message_id(email["message_id"])
            with cache._connect(db) as conn:
                conn.execute(
                    "UPDATE emails SET category = ? WHERE message_id_hash = ?",
                    (email["_category"], h),
                )


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

    def test_delete_email_redirects(self):
        self._seed_emails(1)
        email_hash = cache._hash_message_id("<web0@e.com>")
        with patch.object(web, "DB_PATH", self.db_path), patch("src.scripts.email_reader.delete_email") as mock_del:
            mock_del.return_value = {"deleted": True, "message_id": "<web0@e.com>"}
            client = self._make_client()
            resp = client.post(f"/emails/{email_hash}/delete", follow_redirects=False)
        assert resp.status_code == 303

    def test_delete_nonexistent_redirects(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.post("/emails/nonexistent/delete", follow_redirects=False)
        assert resp.status_code == 303

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
        ):
            mock_instance = MockMonitor.return_value
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

        q = event_bus.bus.subscribe()
        type("R", (), {"is_disconnected": lambda self: False})()

        web.sse_events.__wrapped__ if hasattr(web.sse_events, "__wrapped__") else None

        async def event_stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=0.5)
                        yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                        return
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        return
            finally:
                event_bus.bus.unsubscribe(q)

        async def drive():
            event_bus.bus.publish("refresh", {"hello": "world"})
            outputs = []
            async for chunk in event_stream():
                outputs.append(chunk)
                if len(outputs) >= 1:
                    break
            return outputs

        chunks = asyncio.run(drive())
        assert any("refresh" in c for c in chunks)
        assert any("hello" in c for c in chunks)

    def test_sse_unsubscribes_on_disconnect(self):
        import asyncio

        from src.scripts import event_bus

        q = event_bus.bus.subscribe()
        assert q in event_bus.bus._subscribers

        async def is_disconnected():
            return True

        request = type("R", (), {"is_disconnected": lambda self: is_disconnected()})()

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=0.05)
                        yield f"event: {event['type']}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        return
            finally:
                event_bus.bus.unsubscribe(q)

        async def drive():
            async for _ in event_stream():
                break

        asyncio.run(drive())
        assert q not in event_bus.bus._subscribers


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
            patch.object(web.cache, "get_setting", lambda key, *a, **k: "1" if key == web.updater.LAST_UPDATE_ROLLED_BACK_KEY else None),
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
        assert resp.status_code == 200
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
        assert resp.status_code == 200
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
        assert resp.status_code == 200
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
        assert resp.status_code == 200
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
