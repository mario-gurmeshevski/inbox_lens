import pytest
from unittest.mock import patch

from src.scripts import cache, email_reader
from src.tests._helpers import save_fetched_batch  # noqa: F401


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_emails.db")
    cache.init_db(db_path)
    return db_path


@pytest.fixture(autouse=True)
def cleanup_db_connections():
    yield
    from src.scripts.cache.db import close_all_connections

    close_all_connections()
    from src.scripts.email_reader import imap as _imap

    _imap.reset_folder_caches()


@pytest.fixture(autouse=True)
def _reset_request_settings():
    from src.scripts import web

    token = web._request_settings.set(None)
    try:
        yield
    finally:
        web._request_settings.reset(token)


@pytest.fixture
def isolated_secret_key(tmp_path, monkeypatch):
    from src.scripts.cache import crypto

    key_path = tmp_path / ".secret.key"
    monkeypatch.setattr(crypto, "SECRET_KEY_PATH", str(key_path))
    monkeypatch.setattr(crypto, "_fernet_instance", None)
    yield key_path
    monkeypatch.setattr(crypto, "_fernet_instance", None)


@pytest.fixture
def fake_credentials(monkeypatch):
    monkeypatch.setattr(cache, "get_email_credentials", lambda db_path=None: ("user@e.com", "pass"))
    monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)


@pytest.fixture
def fake_mail():
    from unittest.mock import MagicMock

    mail = MagicMock(name="imap_conn")
    mail.uid.return_value = ("OK", [b""])
    mail.select.return_value = ("OK", [b""])
    mail.list.return_value = ("OK", [])
    mail.capability.return_value = ("OK", [b"IMAP4rev1 IDLE"])
    mail.login.return_value = ("OK", [b""])
    mail.close.return_value = ("OK", [b""])
    mail.logout.return_value = ("OK", [b""])
    mail.send.return_value = None
    mail.readline.return_value = b"+ idling\r\n"
    mail._new_tag.return_value = b"AB0001"
    return mail


@pytest.fixture
def sample_email():
    return {
        "message_id": "<test123@example.com>",
        "from": "Alice <alice@example.com>",
        "subject": "Test Subject",
        "date": "Mon, 01 Jan 2024 10:00:00 +0000",
        "body": "Hello, this is a test email body.",
        "keyword_matches": {"8": ["invoice"]},
        "_category": "8",
        "thread_id": "abc123def456",
        "in_reply_to": "<parent@example.com>",
    }


@pytest.fixture
def sample_emails_batch():
    return [
        {
            "message_id": f"<batch{i}@example.com>",
            "from": f"sender{i}@example.com",
            "subject": f"Batch subject {i}",
            "date": f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
            "body": f"Body {i}",
            "keyword_matches": {"7": ["problem"]} if i % 2 == 0 else None,
            "_category": "7" if i % 2 == 0 else None,
            "thread_id": f"thread_{i}",
            "in_reply_to": None,
        }
        for i in range(5)
    ]


@pytest.fixture
def compiled_patterns():
    categories = {
        "10": ["important", "immediately"],
        "8": ["invoice", "payment"],
        "7": ["problem", "mistake"],
        "1": ["unsubscribe"],
    }
    return email_reader.build_compiled_patterns(categories)


@pytest.fixture
def sample_headers_batch():
    return [
        {
            "message_id": f"<header{i}@example.com>",
            "from": f"sender{i}@example.com",
            "subject": f"Header subject {i}",
            "date": f"Mon, 0{i + 1} Jan 2024 10:00:00 +0000",
            "thread_id": f"thread_{i}",
            "in_reply_to": None,
        }
        for i in range(3)
    ]


@pytest.fixture
def save_batch():
    return save_fetched_batch


@pytest.fixture
def web_db(tmp_path, monkeypatch):
    from src.scripts import web

    db_path = str(tmp_path / "web_test.db")
    cache.init_db(db_path)
    monkeypatch.setattr(web, "DB_PATH", db_path)
    return db_path


@pytest.fixture
def web_client(web_db):
    from fastapi.testclient import TestClient
    from src.scripts import web

    with patch.object(web, "DB_PATH", web_db):
        yield TestClient(web.app)
