from unittest.mock import patch

import pytest

from src.scripts import cache, web


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
                "date": f"Mon, 0{i+1} Jan 2024 10:00:00 +0000",
                "body": f"Body {i}",
                "_category": "7" if i % 2 == 0 else "3",
            }
            for i in range(n)
        ]
        cache.save_emails_batch(emails, self.db_path)
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
        with patch.object(web, "DB_PATH", self.db_path), \
             patch("src.scripts.email_reader.delete_email") as mock_del:
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
        with patch.object(web, "DB_PATH", self.db_path), \
             patch("src.scripts.cache.has_email_credentials", return_value=False):
            client = self._make_client()
            resp = client.get("/setup")
        assert resp.status_code == 200

    def test_account_page_shows_email(self):
        with patch.object(web, "DB_PATH", self.db_path):
            client = self._make_client()
            resp = client.get("/account")
        assert resp.status_code == 200
