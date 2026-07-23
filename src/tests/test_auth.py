from unittest.mock import MagicMock

import pytest

from src.scripts import auth
from src.scripts.cache import crypto


@pytest.fixture
def db(tmp_path, isolated_secret_key):
    from src.scripts import cache

    db_path = str(tmp_path / "auth.db")
    cache.init_db(db_path)
    return db_path


class TestPassword:
    def test_not_configured_by_default(self, db):
        assert auth.is_auth_configured(db) is False

    def test_set_password_marks_configured(self, db):
        auth.set_password("hunter22", db)
        assert auth.is_auth_configured(db) is True

    def test_verify_correct_password(self, db):
        auth.set_password("hunter22", db)
        assert auth.verify_password("hunter22", db) is True

    def test_verify_wrong_password(self, db):
        auth.set_password("hunter22", db)
        assert auth.verify_password("nope", db) is False

    def test_verify_when_not_configured(self, db):
        assert auth.verify_password("anything", db) is False

    def test_verify_empty_inputs(self, db):
        assert auth.verify_password("", db) is False

    def test_hash_is_bcrypt_format(self, db):
        h = auth.hash_password("whatever12")
        assert h.startswith("$2b$")

    def test_change_password_requires_correct_old(self, db):
        auth.set_password("hunter22", db)
        assert auth.change_password("wrong", "newpass12", db) is False
        assert auth.verify_password("hunter22", db) is True

    def test_change_password_success(self, db):
        auth.set_password("hunter22", db)
        assert auth.change_password("hunter22", "newpass12", db) is True
        assert auth.verify_password("newpass12", db) is True
        assert auth.verify_password("hunter22", db) is False

    def test_validate_password_short(self):
        assert auth.validate_password("ab") is not None

    def test_validate_password_long_enough(self):
        assert auth.validate_password("longenough") is None

    def test_verify_hash_handles_garbage(self):
        assert auth.verify_hash("x", "not-a-hash") is False
        assert auth.verify_hash("", "$2b$12$abc") is False


class TestApiKey:
    def test_generate_is_unique(self):
        a = auth.generate_api_key()
        b = auth.generate_api_key()
        assert a != b
        assert len(a) > 20

    def test_hash_api_key_is_sha256_hex(self):
        h = auth._hash_api_key("abc")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_save_then_validate(self, db):
        token = auth.generate_api_key()
        auth.save_api_key(token, db)
        assert auth.is_valid_api_key(token, db) is True

    def test_validate_wrong_token(self, db):
        token = auth.generate_api_key()
        auth.save_api_key(token, db)
        assert auth.is_valid_api_key("wrong-token", db) is False

    def test_validate_when_none_configured(self, db):
        assert auth.is_valid_api_key("anything", db) is False

    def test_validate_empty(self, db):
        assert auth.is_valid_api_key("", db) is False

    def test_revoke(self, db):
        token = auth.generate_api_key()
        auth.save_api_key(token, db)
        assert auth.is_valid_api_key(token, db) is True
        auth.revoke_api_key(db)
        assert auth.is_valid_api_key(token, db) is False

    def test_created_at_recorded(self, db):
        assert auth.get_api_key_created_at(db) is None
        token = auth.generate_api_key()
        auth.save_api_key(token, db)
        created = auth.get_api_key_created_at(db)
        assert created is not None and "UTC" in created

    def test_stored_value_is_hashed_not_plaintext(self, db):
        token = auth.generate_api_key()
        auth.save_api_key(token, db)
        stored = crypto.get_setting(auth.API_KEY_HASH_KEY, db)
        assert stored != token
        assert token not in (stored or "")


class TestSessionHelpers:
    def _request(self, session=None):
        req = MagicMock()
        req.session = session if session is not None else {}
        return req

    def test_mark_logged_in_sets_session(self):
        req = self._request()
        auth.mark_logged_in(req)
        assert req.session.get("authed") is True
        assert "login_at" in req.session

    def test_is_logged_in_true(self):
        req = self._request({"authed": True})
        assert auth.is_logged_in(req) is True

    def test_is_logged_in_false(self):
        req = self._request({})
        assert auth.is_logged_in(req) is False

    def test_logout_clears_session(self):
        req = self._request({"authed": True, "login_at": 1})
        auth.logout(req)
        assert req.session == {}


class TestApiKeyExtraction:
    def _request(self, headers=None, path="/", query=None):
        req = MagicMock()
        req.headers = headers or {}
        req.url.path = path
        req.url.query = query or ""
        req.query_params = {"api_key": query.split("=", 1)[1]} if (query and query.startswith("api_key=")) else {}
        return req

    def test_bearer_header(self):
        req = self._request({"authorization": "Bearer abc123"})
        assert auth.get_api_key_from_request(req) == "abc123"

    def test_bearer_header_case_insensitive(self):
        req = self._request({"authorization": "bearer xyz"})
        assert auth.get_api_key_from_request(req) == "xyz"

    def test_no_auth_header(self):
        req = self._request({})
        assert auth.get_api_key_from_request(req) == ""

    def test_query_param_only_on_sse(self):
        req = self._request({}, path="/api/events", query="api_key=fromquery")
        assert auth.get_api_key_from_request(req) == "fromquery"

    def test_query_param_ignored_on_other_paths(self):
        req = self._request({}, path="/api/data", query="api_key=fromquery")
        assert auth.get_api_key_from_request(req) == ""

    def test_bearer_takes_precedence(self):
        req = self._request({"authorization": "Bearer header"}, path="/api/events", query="api_key=query")
        assert auth.get_api_key_from_request(req) == "header"


class TestOriginCheck:
    def _request(self, headers=None):
        req = MagicMock()
        req.headers = headers or {}
        return req

    def test_no_origin_allows(self):
        assert auth.origin_ok(self._request({})) is True

    def test_matching_origin_allows(self):
        req = self._request({"origin": "http://example.com", "host": "example.com"})
        assert auth.origin_ok(req) is True

    def test_mismatched_origin_rejects(self):
        req = self._request({"origin": "http://evil.com", "host": "example.com"})
        assert auth.origin_ok(req) is False

    def test_referer_fallback_matches(self):
        req = self._request({"referer": "http://example.com/emails", "host": "example.com"})
        assert auth.origin_ok(req) is True

    def test_referer_fallback_mismatch(self):
        req = self._request({"referer": "http://evil.com/x", "host": "example.com"})
        assert auth.origin_ok(req) is False


class TestRateLimiter:
    def test_not_limited_initially(self):
        rl = auth.LoginRateLimiter(max_attempts=3, window_seconds=60)
        assert rl.is_limited("1.2.3.4") is False

    def test_limited_after_max(self):
        rl = auth.LoginRateLimiter(max_attempts=2, window_seconds=60)
        rl.record_failure("1.2.3.4")
        rl.record_failure("1.2.3.4")
        assert rl.is_limited("1.2.3.4") is True

    def test_reset_clears(self):
        rl = auth.LoginRateLimiter(max_attempts=2, window_seconds=60)
        rl.record_failure("1.2.3.4")
        rl.record_failure("1.2.3.4")
        rl.reset("1.2.3.4")
        assert rl.is_limited("1.2.3.4") is False

    def test_per_ip_isolation(self):
        rl = auth.LoginRateLimiter(max_attempts=2, window_seconds=60)
        rl.record_failure("1.1.1.1")
        rl.record_failure("1.1.1.1")
        assert rl.is_limited("1.1.1.1") is True
        assert rl.is_limited("2.2.2.2") is False

    def test_empty_ip_never_limited(self):
        rl = auth.LoginRateLimiter(max_attempts=1, window_seconds=60)
        rl.record_failure("")
        assert rl.is_limited("") is False

    def test_window_expiry(self, monkeypatch):
        import time as time_mod

        rl = auth.LoginRateLimiter(max_attempts=2, window_seconds=10)
        clock = [100.0]
        monkeypatch.setattr(time_mod, "monotonic", lambda: clock[0])
        rl.record_failure("9.9.9.9")
        rl.record_failure("9.9.9.9")
        assert rl.is_limited("9.9.9.9") is True
        clock[0] = 200.0
        assert rl.is_limited("9.9.9.9") is False

    def test_rate_limiter_is_generic_key(self):
        rl = auth.RateLimiter(max_attempts=2, window_seconds=60)
        rl.record_failure("send:user@e.com")
        rl.record_failure("send:user@e.com")
        assert rl.is_limited("send:user@e.com") is True
        assert rl.is_limited("send:other@e.com") is False

    def test_login_alias_is_rate_limiter(self):
        assert auth.LoginRateLimiter is auth.RateLimiter

    def test_send_rate_limiter_defaults(self):
        assert auth.send_rate_limiter.max_attempts == 10
        assert auth.send_rate_limiter.window_seconds == 60
