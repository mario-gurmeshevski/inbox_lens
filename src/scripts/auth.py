import hashlib
import secrets
import time
from urllib.parse import urlparse

import bcrypt
from starlette.requests import Request

from src.scripts.cache import crypto
from src.scripts.constants import DB_PATH

PASSWORD_KEY = "dashboard_password_hash"
API_KEY_HASH_KEY = "api_key_hash"
API_KEY_CREATED_KEY = "api_key_created_at"

MIN_PASSWORD_LENGTH = 8


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def verify_hash(plaintext: str, hashed: str) -> bool:
    if not plaintext or not hashed:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def is_auth_configured(db_path: str = DB_PATH) -> bool:
    return bool(crypto.get_setting(PASSWORD_KEY, db_path))


def set_password(plaintext: str, db_path: str = DB_PATH) -> None:
    crypto.save_setting(PASSWORD_KEY, hash_password(plaintext), db_path)


def verify_password(plaintext: str, db_path: str = DB_PATH) -> bool:
    stored = crypto.get_setting(PASSWORD_KEY, db_path)
    return verify_hash(plaintext, stored) if stored else False


def change_password(old: str, new: str, db_path: str = DB_PATH) -> bool:
    if not verify_password(old, db_path):
        return False
    set_password(new, db_path)
    return True


def _hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def save_api_key(token: str, db_path: str = DB_PATH) -> None:
    crypto.save_setting(API_KEY_HASH_KEY, _hash_api_key(token), db_path)
    crypto.save_setting(API_KEY_CREATED_KEY, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), db_path)


def revoke_api_key(db_path: str = DB_PATH) -> None:
    crypto.delete_setting(API_KEY_HASH_KEY, db_path)
    crypto.delete_setting(API_KEY_CREATED_KEY, db_path)


def is_valid_api_key(token: str, db_path: str = DB_PATH) -> bool:
    if not token:
        return False
    stored = crypto.get_setting(API_KEY_HASH_KEY, db_path)
    if not stored:
        return False
    return secrets.compare_digest(_hash_api_key(token), stored)


def get_api_key_created_at(db_path: str = DB_PATH) -> str | None:
    return crypto.get_setting(API_KEY_CREATED_KEY, db_path)


def get_api_key_from_request(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if request.url.path == "/api/events":
        param = request.query_params.get("api_key", "")
        if param:
            return param
    return ""


def mark_logged_in(request: Request) -> None:
    request.session.clear()
    request.session["authed"] = True
    request.session["login_at"] = int(time.time())


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("authed"))


def logout(request: Request) -> None:
    request.session.clear()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}

    def _prune(self, ip: str, now: float) -> list[float]:
        recent = self._attempts.get(ip, [])
        recent = [t for t in recent if now - t < self.window_seconds]
        if recent:
            self._attempts[ip] = recent
        else:
            self._attempts.pop(ip, None)
        return recent

    def is_limited(self, ip: str) -> bool:
        if not ip:
            return False
        return len(self._prune(ip, time.monotonic())) >= self.max_attempts

    def record_failure(self, ip: str) -> None:
        if not ip:
            return
        now = time.monotonic()
        recent = self._prune(ip, now)
        recent.append(now)
        self._attempts[ip] = recent

    def reset(self, ip: str) -> None:
        self._attempts.pop(ip, None)


login_rate_limiter = LoginRateLimiter()


def origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True
    host = request.headers.get("host", "")
    parsed = urlparse(origin)
    return bool(host) and parsed.netloc == host


def validate_password(plaintext: str) -> str | None:
    if not plaintext or len(plaintext) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None
