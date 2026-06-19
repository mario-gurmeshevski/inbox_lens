from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet, InvalidToken

from src.scripts.cache import crypto


class TestEnsureSecretKey:
    def test_generates_key_when_missing(self, isolated_secret_key):
        assert not isolated_secret_key.exists()
        key = crypto._ensure_secret_key()
        assert isolated_secret_key.exists()
        assert key == isolated_secret_key.read_bytes().strip()
        Fernet(key)

    def test_reuses_existing_key(self, isolated_secret_key):
        first = crypto._ensure_secret_key()
        second = crypto._ensure_secret_key()
        assert first == second
        assert isolated_secret_key.read_bytes().strip() == first

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        nested = tmp_path / "deep" / "nested" / ".secret.key"
        monkeypatch.setattr(crypto, "SECRET_KEY_PATH", str(nested))
        monkeypatch.setattr(crypto, "_fernet_instance", None)
        key = crypto._ensure_secret_key()
        assert nested.exists()
        assert key == nested.read_bytes().strip()
        monkeypatch.setattr(crypto, "_fernet_instance", None)


class TestGetFernet:
    def test_returns_singleton(self, isolated_secret_key):
        crypto._fernet_instance = None
        first = crypto._get_fernet()
        second = crypto._get_fernet()
        assert first is second

    def test_double_checked_locking(self, isolated_secret_key):
        crypto._fernet_instance = None
        f = crypto._get_fernet()
        assert isinstance(f, Fernet)


class TestEncryptDecrypt:
    def test_roundtrip(self, isolated_secret_key):
        crypto._fernet_instance = None
        plaintext = "super-secret-password"
        ciphertext = crypto._encrypt(plaintext)
        assert ciphertext != plaintext
        assert crypto._decrypt(ciphertext) == plaintext

    def test_ciphertext_is_not_plaintext(self, isolated_secret_key):
        crypto._fernet_instance = None
        plaintext = "my-app-password"
        ciphertext = crypto._encrypt(plaintext)
        assert plaintext not in ciphertext
        assert plaintext.encode() not in ciphertext.encode()

    def test_decrypt_invalid_token_raises(self, isolated_secret_key):
        crypto._fernet_instance = None
        with pytest.raises(InvalidToken):
            crypto._decrypt("not-a-valid-token")


class TestSettings:
    def test_save_and_get_setting(self, tmp_db):
        crypto.save_setting("foo", "bar", tmp_db)
        assert crypto.get_setting("foo", tmp_db) == "bar"

    def test_get_missing_setting_returns_none(self, tmp_db):
        assert crypto.get_setting("missing", tmp_db) is None

    def test_save_setting_overwrites(self, tmp_db):
        crypto.save_setting("k", "v1", tmp_db)
        crypto.save_setting("k", "v2", tmp_db)
        assert crypto.get_setting("k", tmp_db) == "v2"

    def test_delete_setting(self, tmp_db):
        crypto.save_setting("k", "v", tmp_db)
        crypto.delete_setting("k", tmp_db)
        assert crypto.get_setting("k", tmp_db) is None

    def test_delete_missing_setting_noop(self, tmp_db):
        crypto.delete_setting("absent", tmp_db)


class TestEmailCredentials:
    def test_save_and_get_credentials_roundtrip(self, tmp_db, isolated_secret_key):
        crypto._fernet_instance = None
        crypto.save_email_credentials("user@example.com", "pass123", tmp_db)
        user, passwd = crypto.get_email_credentials(tmp_db)
        assert user == "user@example.com"
        assert passwd == "pass123"

    def test_stored_password_is_encrypted(self, tmp_db, isolated_secret_key):
        crypto._fernet_instance = None
        crypto.save_email_credentials("user@example.com", "pass123", tmp_db)
        raw = crypto.get_setting("email_pass", tmp_db)
        assert raw != "pass123"
        assert "pass123" not in raw

    def test_has_email_credentials_true_when_present(self, tmp_db, isolated_secret_key):
        crypto._fernet_instance = None
        crypto.save_email_credentials("u@e.com", "p", tmp_db)
        assert crypto.has_email_credentials(tmp_db) is True

    def test_has_email_credentials_false_when_absent(self, tmp_db):
        assert crypto.has_email_credentials(tmp_db) is False

    def test_has_email_credentials_false_when_user_only(self, tmp_db):
        crypto.save_setting("email_user", "u@e.com", tmp_db)
        assert crypto.has_email_credentials(tmp_db) is False

    def test_has_email_credentials_false_when_pass_only(self, tmp_db, isolated_secret_key):
        crypto._fernet_instance = None
        crypto.save_setting("email_pass", crypto._encrypt("p"), tmp_db)
        assert crypto.has_email_credentials(tmp_db) is False

    def test_get_credentials_returns_none_pass_on_decrypt_failure(
        self, tmp_db, isolated_secret_key
    ):
        crypto._fernet_instance = None
        crypto.save_email_credentials("u@e.com", "p", tmp_db)
        crypto._fernet_instance = None
        with patch.object(crypto, "_ensure_secret_key", return_value=Fernet.generate_key()):
            crypto._fernet_instance = None
            user, passwd = crypto.get_email_credentials(tmp_db)
        assert user == "u@e.com"
        assert passwd is None

    def test_delete_email_credentials(self, tmp_db, isolated_secret_key):
        crypto._fernet_instance = None
        crypto.save_email_credentials("u@e.com", "p", tmp_db)
        crypto.delete_email_credentials(tmp_db)
        assert crypto.has_email_credentials(tmp_db) is False
        assert crypto.get_setting("email_user", tmp_db) is None
        assert crypto.get_setting("email_pass", tmp_db) is None

    def test_delete_email_credentials_when_absent(self, tmp_db):
        crypto.delete_email_credentials(tmp_db)
