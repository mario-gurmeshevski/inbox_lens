import logging
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from src.scripts.cache.db import _connect
from src.scripts.constants import DB_PATH, SECRET_KEY_PATH, SESSION_SECRET_PATH

logger = logging.getLogger(__name__)


def save_setting(key: str, value: str, db_path: str = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )


def get_setting(key: str, db_path: str = DB_PATH) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def delete_setting(key: str, db_path: str = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def save_email_credentials(email_user: str, email_pass: str, db_path: str = DB_PATH) -> None:
    save_setting("email_user", email_user, db_path)
    save_setting("email_pass", _encrypt(email_pass), db_path)


def get_email_credentials(db_path: str = DB_PATH) -> tuple[str | None, str | None]:
    user = get_setting("email_user", db_path)
    encrypted_pass = get_setting("email_pass", db_path)
    if user and encrypted_pass:
        try:
            passwd = _decrypt(encrypted_pass)
        except Exception:
            logger.warning("Failed to decrypt email password — key may have changed")
            return user, None
    else:
        passwd = None
    return user, passwd


def has_email_credentials(db_path: str = DB_PATH) -> bool:
    user, passwd = get_email_credentials(db_path)
    return bool(user and passwd)


def delete_email_credentials(db_path: str = DB_PATH) -> None:
    delete_setting("email_user", db_path)
    delete_setting("email_pass", db_path)


def _ensure_secret_key() -> bytes:
    path = Path(SECRET_KEY_PATH)
    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.write_bytes(key)
    logger.info("Generated new encryption key at %s", SECRET_KEY_PATH)
    return key


_fernet_instance: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(_ensure_secret_key())
    return _fernet_instance


def _encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def _ensure_session_key() -> str:
    path = Path(SESSION_SECRET_PATH)
    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(64)
    path.write_text(key)
    logger.info("Generated new session secret at %s", SESSION_SECRET_PATH)
    return key
