from datetime import datetime, timedelta, timezone
import time

from src.scripts import cache, email_reader
from src.scripts import outbox_worker as ow


def _payload(**overrides):
    base = {
        "to_addr": "a@x.com",
        "cc": "",
        "bcc": "",
        "subject": "Hi",
        "body": "Body",
        "body_html": "<p>Body</p>",
        "mode": "new",
        "original_message_id": None,
        "thread_id": None,
        "draft_id": None,
    }
    base.update(overrides)
    return base


class TestNextAttemptAt:
    def _parse(self, value):
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    def test_first_failure_schedules_short_backoff(self):
        before = datetime.now(timezone.utc) - timedelta(seconds=1)
        value = ow._next_attempt_at(0)
        parsed = self._parse(value)
        after = datetime.now(timezone.utc) + timedelta(seconds=1)
        # ~60s out, with a small clock skew window.
        assert before + timedelta(seconds=60) <= parsed <= after + timedelta(seconds=60)

    def test_last_backoff_before_exhaustion(self):
        last_idx = len(ow.BACKOFF_SCHEDULE) - 1
        before = datetime.now(timezone.utc) - timedelta(seconds=1)
        value = ow._next_attempt_at(last_idx)
        parsed = self._parse(value)
        after = datetime.now(timezone.utc) + timedelta(seconds=1)
        delay = ow.BACKOFF_SCHEDULE[last_idx]
        assert before + timedelta(seconds=delay) <= parsed <= after + timedelta(seconds=delay)
        assert ow._next_attempt_at(last_idx + 1) is None

    def test_returns_absolute_timestamp_not_modifier(self):
        for i in range(len(ow.BACKOFF_SCHEDULE)):
            value = ow._next_attempt_at(i)
            assert value and not value.startswith("+")
            assert datetime.strptime(value, "%Y-%m-%d %H:%M:%S")

    def test_exhausted_returns_none(self):
        assert ow._next_attempt_at(ow.MAX_ATTEMPTS) is None

    def test_beyond_schedule_returns_none(self):
        assert ow._next_attempt_at(ow.MAX_ATTEMPTS + 5) is None


class TestSplitAddrs:
    def test_none_and_empty(self):
        assert ow._split_addrs(None) == []
        assert ow._split_addrs("") == []

    def test_splits_and_trims(self):
        assert ow._split_addrs("a@x.com, b@x.com") == ["a@x.com", "b@x.com"]

    def test_drops_empty_parts(self):
        assert ow._split_addrs("a@x.com, , b@x.com") == ["a@x.com", "b@x.com"]


class TestAttemptSuccess:
    def test_success_deletes_row_and_notifies(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        sent = {"n": 0}
        notified = {"n": 0}

        def fake_send(*a, **k):
            sent["n"] += 1

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        oid = cache.enqueue(_payload(), tmp_db)
        row = cache.get_outbox(oid, tmp_db)
        worker = ow.OutboxWorker(db_path=tmp_db, on_change=lambda: notified.__setitem__("n", notified["n"] + 1))
        worker._attempt(row)

        assert sent["n"] == 1
        assert notified["n"] == 1
        assert cache.get_outbox(oid, tmp_db) is None

    def test_success_passes_split_addresses(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        captured = {}

        def fake_send(to_addrs, cc_addrs, bcc_addrs, subject, body, body_html, mode, **kw):
            captured.update(to=to_addrs, cc=cc_addrs, bcc=bcc_addrs, subject=subject, mode=mode)

        monkeypatch.setattr(email_reader, "send_message", fake_send)
        oid = cache.enqueue(
            _payload(to_addr="a@x.com, b@x.com", cc="c@x.com", subject="Hi", mode="reply"), tmp_db
        )
        row = cache.get_outbox(oid, tmp_db)
        ow.OutboxWorker(db_path=tmp_db)._attempt(row)
        assert captured["to"] == ["a@x.com", "b@x.com"]
        assert captured["cc"] == ["c@x.com"]
        assert captured["mode"] == "reply"


class TestAttemptFailure:
    def test_failure_records_error_and_backoff(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)

        def boom(*a, **k):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(email_reader, "send_message", boom)
        oid = cache.enqueue(_payload(), tmp_db)
        row = cache.get_outbox(oid, tmp_db)
        notified = {"n": 0}
        worker = ow.OutboxWorker(db_path=tmp_db, on_change=lambda: notified.__setitem__("n", notified["n"] + 1))
        worker._attempt(row)

        updated = cache.get_outbox(oid, tmp_db)
        assert updated["status"] == "pending"
        assert updated["attempts"] == 1
        next_at = datetime.strptime(updated["next_attempt_at"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        now = datetime.now(timezone.utc)
        assert now + timedelta(seconds=50) <= next_at <= now + timedelta(seconds=70)
        assert "smtp down" in updated["last_error"]
        assert notified["n"] == 1

    def test_claimed_row_is_not_reattempted(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        sent = {"n": 0}
        monkeypatch.setattr(email_reader, "send_message", lambda *a, **k: sent.__setitem__("n", sent["n"] + 1))
        oid = cache.enqueue(_payload(), tmp_db)
        cache.mark_outbox_attempting(oid, tmp_db)
        row = cache.get_outbox(oid, tmp_db)
        ow.OutboxWorker(db_path=tmp_db)._attempt(row)
        assert sent["n"] == 0

    def test_terminal_failure_after_max_attempts(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)

        def boom(*a, **k):
            raise RuntimeError("nope")

        monkeypatch.setattr(email_reader, "send_message", boom)
        oid = cache.enqueue(_payload(), tmp_db)
        cache.mark_outbox_failed(oid, "x", ow._next_attempt_at(0), tmp_db)
        for _ in range(ow.MAX_ATTEMPTS - 2):
            cache.mark_outbox_failed(oid, "x", ow._next_attempt_at(0), tmp_db)
        row = cache.get_outbox(oid, tmp_db)
        assert row["attempts"] == ow.MAX_ATTEMPTS - 1
        ow.OutboxWorker(db_path=tmp_db)._attempt(row)
        updated = cache.get_outbox(oid, tmp_db)
        assert updated["attempts"] == ow.MAX_ATTEMPTS
        assert updated["status"] == "failed"
        assert updated["next_attempt_at"] is None


class TestProcessBatch:
    def test_processes_all_due_rows(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        for i in range(3):
            cache.enqueue(_payload(subject=f"S{i}"), tmp_db)
        sent_subjects = []
        monkeypatch.setattr(
            email_reader, "send_message", lambda to, cc, bcc, subject, *a, **k: sent_subjects.append(subject)
        )
        worker = ow.OutboxWorker(db_path=tmp_db)
        worker._process_batch()
        assert sorted(sent_subjects) == ["S0", "S1", "S2"]
        assert cache.list_outbox(tmp_db) == []

    def test_no_due_rows_is_noop(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        called = {"n": 0}
        monkeypatch.setattr(
            email_reader, "send_message", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
        )
        ow.OutboxWorker(db_path=tmp_db)._process_batch()
        assert called["n"] == 0


class TestWorkerLifecycle:
    def test_start_without_credentials_does_not_send(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        monkeypatch.setattr(ow, "POLL_INTERVAL", 0.01)
        cache.enqueue(_payload(), tmp_db)
        called = {"n": 0}
        monkeypatch.setattr(
            email_reader, "send_message", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
        )

        worker = ow.OutboxWorker(db_path=tmp_db)
        worker.start()
        try:
            assert worker.running is True
            time.sleep(0.1)
        finally:
            worker.stop()

        assert called["n"] == 0
        assert cache.count_pending(tmp_db) == 1


class TestConcurrentWorkers:
    def test_two_workers_do_not_duplicate_send(self, tmp_db, monkeypatch):
        import threading as _threading
        n_rows = 20
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        oids = [cache.enqueue(_payload(subject=f"S{i}"), tmp_db) for i in range(n_rows)]

        sent_counts = {}
        sent_lock = _threading.Lock()
        barrier = _threading.Barrier(2)

        def counting_send(to_addrs, cc_addrs, bcc_addrs, subject, *a, **k):
            with sent_lock:
                sent_counts[subject] = sent_counts.get(subject, 0) + 1

        monkeypatch.setattr(email_reader, "send_message", counting_send)

        def run(worker):
            barrier.wait(timeout=5)
            worker._process_batch()

        w1 = ow.OutboxWorker(db_path=tmp_db)
        w2 = ow.OutboxWorker(db_path=tmp_db)
        t1 = _threading.Thread(target=run, args=(w1,))
        t2 = _threading.Thread(target=run, args=(w2,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert len(sent_counts) == n_rows
        assert all(count == 1 for count in sent_counts.values()), sent_counts
        assert cache.list_outbox(tmp_db) == []
        assert {oid for oid in oids} == set(oids)
