import json

from src.scripts import cache


def _raise(exc):
    raise exc


def fake_imap_session(monkeypatch, imap_mod, mail):
    class _FakeSession:
        def __enter__(self_inner):
            return mail

        def __exit__(self_inner, *a):
            return None

    monkeypatch.setattr(imap_mod, "imap_session", lambda db_path=None: _FakeSession())


def save_fetched(email, db):
    cache.save_headers_batch([email], db)
    cache.update_bodies_batch([(email["message_id"], email.get("body", ""))], db)
    h = cache._hash_message_id(email["message_id"])
    keyword_matches = email.get("keyword_matches")
    keyword_json = json.dumps(keyword_matches, ensure_ascii=False) if keyword_matches else None
    with cache._connect(db) as conn:
        conn.execute(
            "UPDATE emails SET category = ?, keyword_matches = ? WHERE message_id_hash = ?",
            (email.get("_category"), keyword_json, h),
        )


def save_fetched_batch(emails, db):
    for email in emails:
        save_fetched(email, db)
