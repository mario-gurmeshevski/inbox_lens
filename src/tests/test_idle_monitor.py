import imaplib
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.scripts import cache, email_reader
import src.scripts.idle_monitor as idle_mod
from src.scripts.idle_monitor import (
    ConnectionLost,
    IdleMonitor,
    run_initial_fetch,
)


@pytest.fixture(autouse=True)
def fast_constants(monkeypatch):
    monkeypatch.setattr(idle_mod, "RECONNECT_DELAY", 0.01)
    monkeypatch.setattr(idle_mod, "IDLE_CHECK_INTERVAL", 0.01)
    monkeypatch.setattr(idle_mod, "IDLE_RENEW_INTERVAL", 9999)
    monkeypatch.setattr(idle_mod, "POLL_FALLBACK_INTERVAL", 9999)


@pytest.fixture
def idle_conn():
    conn = MagicMock()
    conn._new_tag.return_value = b"TAG1"
    conn.readline.return_value = b"+ idling\r\n"
    conn.send.return_value = None
    conn.socket.return_value = MagicMock()
    conn.capability.return_value = ("OK", [b"IMAP4rev1 IDLE"])
    return conn


class TestConnectionLost:
    def test_is_exception_subclass(self):
        assert issubclass(ConnectionLost, Exception)


class TestIdleMonitorInit:
    def test_default_params(self):
        m = IdleMonitor()
        assert m.db_path is not None
        assert m.on_refresh is None
        assert not m._running
        assert m._thread is None

    def test_custom_params(self):
        def cb():
            return None

        m = IdleMonitor(db_path="/x.db", on_refresh=cb)
        assert m.db_path == "/x.db"
        assert m.on_refresh is cb


class TestIdleMonitorRunning:
    def test_returns_false_initially(self):
        assert not IdleMonitor().running

    def test_returns_true_after_start(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        m = IdleMonitor()
        m.start()
        assert m.running is True
        m.stop()

    def test_returns_false_after_stop(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        m = IdleMonitor()
        m.start()
        m.stop()
        assert m.running is False


class TestIdleMonitorStartStop:
    def test_start_creates_daemon_thread(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        m = IdleMonitor()
        m.start()
        assert m._thread is not None
        assert m._thread.is_alive()
        assert m._thread.daemon is True
        m.stop()

    def test_start_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        m = IdleMonitor()
        m.start()
        t = m._thread
        m.start()
        assert m._thread is t
        m.stop()

    def test_stop_joins_thread(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        m = IdleMonitor()
        m.start()
        m.stop()
        assert not m._thread.is_alive()

    def test_stop_before_start_does_not_raise(self):
        IdleMonitor().stop()


class TestIdleMonitorRun:
    def test_loops_when_no_credentials(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: False)
        m = IdleMonitor()
        m.start()
        time.sleep(0.05)
        m.stop()
        assert not m._thread.is_alive()

    def test_calls_idle_loop_when_credentials_present(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        entered = threading.Event()
        monkeypatch.setattr(idle_mod.IdleMonitor, "_idle_loop", lambda self: entered.set())
        m = IdleMonitor()
        m.start()
        assert entered.wait(timeout=1)
        m.stop()

    def test_exception_in_idle_loop_is_caught(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        call_count = []

        def counting_idle(self):
            call_count.append(1)
            if len(call_count) == 1:
                raise RuntimeError("boom")

        monkeypatch.setattr(idle_mod.IdleMonitor, "_idle_loop", counting_idle)
        m = IdleMonitor()
        m.start()
        time.sleep(0.1)
        m.stop()
        assert len(call_count) >= 1

    def test_sets_running_false_when_stopped(self, monkeypatch):
        monkeypatch.setattr(cache, "has_email_credentials", lambda db_path=None: True)
        monkeypatch.setattr(idle_mod.IdleMonitor, "_idle_loop", lambda self: None)
        m = IdleMonitor()
        m.start()
        time.sleep(0.05)
        m.stop()
        assert m.running is False


class TestCheckIdleSupport:
    def test_returns_true_when_idle_in_caps(self, idle_conn):
        assert IdleMonitor()._check_idle_support(idle_conn) is True

    def test_returns_false_when_idle_not_in_caps(self, idle_conn):
        idle_conn.capability.return_value = ("OK", [b"IMAP4rev1"])
        assert IdleMonitor()._check_idle_support(idle_conn) is False

    def test_returns_false_on_non_ok_status(self, idle_conn):
        idle_conn.capability.return_value = ("BAD", [])
        assert IdleMonitor()._check_idle_support(idle_conn) is False

    def test_returns_false_on_exception(self, idle_conn):
        idle_conn.capability.side_effect = RuntimeError("boom")
        assert IdleMonitor()._check_idle_support(idle_conn) is False


class TestEndIdle:
    def test_sends_done_and_reads_until_tag(self, idle_conn):
        idle_conn.readline.return_value = b"TAG1 OK IDLE done\r\n"
        result = IdleMonitor()._end_idle(idle_conn, b"TAG1")
        idle_conn.send.assert_called_with(b"DONE\r\n")
        idle_conn.readline.assert_called()
        assert result is False

    def test_returns_on_send_exception(self, idle_conn):
        idle_conn.send.side_effect = RuntimeError("boom")
        result = IdleMonitor()._end_idle(idle_conn, b"TAG1")
        idle_conn.readline.assert_not_called()
        assert result is False

    def test_breaks_on_empty_readline(self, idle_conn):
        idle_conn.readline.return_value = b""
        result = IdleMonitor()._end_idle(idle_conn, b"TAG1")
        idle_conn.send.assert_called_once()
        assert result is False

    def test_breaks_on_readline_exception(self, idle_conn):
        idle_conn.readline.side_effect = RuntimeError("boom")
        IdleMonitor()._end_idle(idle_conn, b"TAG1")

    def test_detects_exists_while_draining(self, idle_conn):
        idle_conn.readline.side_effect = [
            b"* 5 EXISTS\r\n",
            b"TAG1 OK IDLE done\r\n",
        ]
        assert IdleMonitor()._end_idle(idle_conn, b"TAG1") is True

    def test_detects_recent_while_draining(self, idle_conn):
        idle_conn.readline.side_effect = [
            b"* 3 RECENT\r\n",
            b"TAG1 OK IDLE done\r\n",
        ]
        assert IdleMonitor()._end_idle(idle_conn, b"TAG1") is True

    def test_detects_mail_among_other_lines(self, idle_conn):
        idle_conn.readline.side_effect = [
            b"* 2 RECENT\r\n",
            b"* FLAGS (\\Seen)\r\n",
            b"TAG1 OK IDLE terminated\r\n",
        ]
        assert IdleMonitor()._end_idle(idle_conn, b"TAG1") is True


class TestDoIdle:
    def make_conn(self, **kwargs):
        conn = MagicMock()
        conn._new_tag.return_value = b"TAG1"
        conn.readline.return_value = b"+ idling\r\n"
        conn.send.return_value = None
        conn.socket.return_value = MagicMock()
        conn.configure_mock(**kwargs)
        return conn

    def test_returns_true_on_exists(self, monkeypatch):
        conn = self.make_conn()
        conn.readline.side_effect = [
            b"+ idling\r\n",
            b"* 3 EXISTS\r\n",
        ]
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([r[0]], [], []))
        end_calls = []
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: end_calls.append(tag))
        assert IdleMonitor()._do_idle(conn) is True
        assert len(end_calls) == 1

    def test_returns_true_on_recent(self, monkeypatch):
        conn = self.make_conn()
        conn.readline.side_effect = [
            b"+ idling\r\n",
            b"* 1 RECENT\r\n",
        ]
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([r[0]], [], []))
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: None)
        assert IdleMonitor()._do_idle(conn) is True

    def test_returns_false_on_stop_during_select_timeout(self, monkeypatch):
        conn = self.make_conn()
        select_calls = []

        def mock_select(r, w, x, t):
            select_calls.append(1)
            if len(select_calls) >= 2:
                m._stop.set()
            return ([], [], [])

        monkeypatch.setattr(idle_mod.select, "select", mock_select)
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: None)
        m = IdleMonitor()
        assert m._do_idle(conn) is False

    def test_raises_connection_lost_on_send_broken_pipe(self):
        conn = self.make_conn()
        conn.send.side_effect = BrokenPipeError("broken")
        with pytest.raises(ConnectionLost):
            IdleMonitor()._do_idle(conn)

    def test_raises_connection_lost_on_send_connection_reset(self):
        conn = self.make_conn()
        conn.send.side_effect = ConnectionResetError("reset")
        with pytest.raises(ConnectionLost):
            IdleMonitor()._do_idle(conn)

    def test_raises_connection_lost_on_send_imap_abort(self):
        conn = self.make_conn()
        conn.send.side_effect = imaplib.IMAP4.abort("abort")
        with pytest.raises(ConnectionLost):
            IdleMonitor()._do_idle(conn)

    def test_raises_connection_lost_on_send_os_error(self):
        conn = self.make_conn()
        conn.send.side_effect = OSError("os error")
        with pytest.raises(ConnectionLost):
            IdleMonitor()._do_idle(conn)

    def test_returns_false_on_other_send_exception(self):
        conn = self.make_conn()
        conn.send.side_effect = RuntimeError("other")
        assert IdleMonitor()._do_idle(conn) is False

    def test_returns_false_when_continuation_not_ok(self):
        conn = self.make_conn()
        conn.readline.return_value = b"NO IDLE disabled\r\n"
        assert IdleMonitor()._do_idle(conn) is False

    def test_returns_false_when_continuation_is_none(self):
        conn = self.make_conn()
        conn.readline.return_value = None
        assert IdleMonitor()._do_idle(conn) is False

    def test_raises_connection_lost_on_continuation_broken_pipe(self):
        conn = self.make_conn()
        conn.readline.side_effect = BrokenPipeError("broken")
        with pytest.raises(ConnectionLost):
            IdleMonitor()._do_idle(conn)

    def test_returns_false_on_other_continuation_exception(self):
        conn = self.make_conn()
        conn.readline.side_effect = RuntimeError("boom")
        assert IdleMonitor()._do_idle(conn) is False

    def test_raises_connection_lost_on_select_os_error(self, monkeypatch):
        conn = self.make_conn()
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: (_ for _ in ()).throw(OSError("sel fail")))
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: None)
        with pytest.raises(ConnectionLost):
            IdleMonitor()._do_idle(conn)

    def test_raises_connection_lost_on_select_value_error(self, monkeypatch):
        conn = self.make_conn()
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: (_ for _ in ()).throw(ValueError("bad fd")))
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: None)
        with pytest.raises(ConnectionLost):
            IdleMonitor()._do_idle(conn)

    def test_returns_false_on_empty_data_readline(self, monkeypatch):
        conn = self.make_conn()
        conn.readline.side_effect = [
            b"+ idling\r\n",
            b"",
        ]
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([r[0]], [], []))
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: None)
        assert IdleMonitor()._do_idle(conn) is False

    def test_returns_false_on_readline_exception(self, monkeypatch):
        conn = self.make_conn()
        conn.readline.side_effect = [
            b"+ idling\r\n",
            RuntimeError("boom"),
        ]
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([r[0]], [], []))
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: None)
        assert IdleMonitor()._do_idle(conn) is False

    def test_raises_connection_lost_on_readline_connection_error(self, monkeypatch):
        conn = self.make_conn()
        conn.readline.side_effect = [
            b"+ idling\r\n",
            ConnectionResetError("reset"),
        ]
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([r[0]], [], []))
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: None)
        with pytest.raises(ConnectionLost):
            IdleMonitor()._do_idle(conn)

    def test_renews_idle_when_elapsed_exceeds_interval(self, monkeypatch):
        conn = self.make_conn()
        end_calls = []
        monkeypatch.setattr(idle_mod, "IDLE_RENEW_INTERVAL", 100)
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([], [], []))
        monkeypatch.setattr(
            idle_mod.IdleMonitor,
            "_end_idle",
            lambda self, conn, tag: (end_calls.append(tag), m._stop.set(), False)[2],
        )

        monotonic_calls = []

        def mock_monotonic():
            monotonic_calls.append(1)
            return 200.0 if len(monotonic_calls) > 2 else 0.0

        monkeypatch.setattr(idle_mod.time, "monotonic", mock_monotonic)
        m = IdleMonitor()
        assert m._do_idle(conn) is False
        assert len(end_calls) == 1

    def test_renew_returns_true_when_mail_observed(self, monkeypatch):
        conn = self.make_conn()
        monkeypatch.setattr(idle_mod, "IDLE_RENEW_INTERVAL", 100)
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([], [], []))
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, c, t: True)

        monotonic_calls = []

        def mock_monotonic():
            monotonic_calls.append(1)
            return 200.0 if len(monotonic_calls) > 2 else 0.0

        monkeypatch.setattr(idle_mod.time, "monotonic", mock_monotonic)
        assert IdleMonitor()._do_idle(conn) is True

    def test_poll_fallback_triggers_fetch(self, monkeypatch):
        conn = self.make_conn()
        end_calls = []
        monkeypatch.setattr(idle_mod, "POLL_FALLBACK_INTERVAL", 100)
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([], [], []))
        monkeypatch.setattr(
            idle_mod.IdleMonitor,
            "_end_idle",
            lambda self, c, t: (end_calls.append(t), False)[1],
        )

        monotonic_calls = []

        def mock_monotonic():
            monotonic_calls.append(1)
            return 200.0 if len(monotonic_calls) > 2 else 0.0

        monkeypatch.setattr(idle_mod.time, "monotonic", mock_monotonic)
        assert IdleMonitor()._do_idle(conn) is True
        assert len(end_calls) == 1

    def test_renew_fires_before_poll_fallback_when_ordered(self, monkeypatch):
        conn = self.make_conn()
        end_calls = []
        monkeypatch.setattr(idle_mod, "IDLE_RENEW_INTERVAL", 100)
        monkeypatch.setattr(idle_mod, "POLL_FALLBACK_INTERVAL", 150)
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([], [], []))
        monkeypatch.setattr(
            idle_mod.IdleMonitor,
            "_end_idle",
            lambda self, c, t: (end_calls.append(t), False)[1],
        )

        seq = iter([0.0, 0.0, 100.0, 100.0, 100.0, 150.0])

        def mock_monotonic():
            try:
                return next(seq)
            except StopIteration:
                return 150.0

        monkeypatch.setattr(idle_mod.time, "monotonic", mock_monotonic)

        assert IdleMonitor()._do_idle(conn) is True
        assert len(end_calls) == 2  # one renew + one poll fallback
        assert conn.send.call_count == 2  # IDLE issued twice -> renewal re-entered

    def test_sends_idle_with_tag(self, monkeypatch):
        conn = self.make_conn()
        monkeypatch.setattr(idle_mod.select, "select", lambda r, w, x, t: ([], [], []))
        select_calls = []

        def mock_select(r, w, x, t):
            select_calls.append(1)
            if len(select_calls) >= 2:
                m._stop.set()
            return ([], [], [])

        monkeypatch.setattr(idle_mod.select, "select", mock_select)
        monkeypatch.setattr(idle_mod.IdleMonitor, "_end_idle", lambda self, conn, tag: None)
        m = IdleMonitor()
        m._do_idle(conn)
        conn._new_tag.assert_called()
        conn.send.assert_called_with(b"TAG1 IDLE\r\n")


class TestIdleLoop:
    def test_connects_and_loops_calling_do_idle(self, monkeypatch, idle_conn):
        monkeypatch.setattr(email_reader, "_imap_connect", lambda db_path=None: idle_conn)
        do_calls = []

        def tracking_do_idle(self, conn):
            do_calls.append(1)
            if len(do_calls) >= 2:
                self._stop.set()
            return False

        monkeypatch.setattr(idle_mod.IdleMonitor, "_do_idle", tracking_do_idle)
        m = IdleMonitor()
        m._idle_loop()
        assert len(do_calls) >= 1

    def test_returns_early_when_idle_unsupported(self, monkeypatch):
        unsupported = MagicMock()
        unsupported.capability.return_value = ("OK", [b"IMAP4rev1"])
        monkeypatch.setattr(email_reader, "_imap_connect", lambda db_path=None: unsupported)
        never_called = []
        monkeypatch.setattr(idle_mod.IdleMonitor, "_do_idle", lambda self, conn: never_called.append(1))
        IdleMonitor()._idle_loop()
        assert never_called == []

    def test_fetches_new_mail_when_do_idle_returns_true(self, monkeypatch, idle_conn):
        monkeypatch.setattr(email_reader, "_imap_connect", lambda db_path=None: idle_conn)
        monkeypatch.setattr(email_reader, "_safe_close", lambda c: None)

        do_calls = []

        def do_idle_then_stop(self, conn):
            do_calls.append(1)
            if len(do_calls) == 1:
                return True
            self._stop.set()
            return False

        monkeypatch.setattr(idle_mod.IdleMonitor, "_do_idle", do_idle_then_stop)
        fetch_calls = []
        monkeypatch.setattr(idle_mod.IdleMonitor, "_fetch_new", lambda self: fetch_calls.append(1))
        m = IdleMonitor()
        m._idle_loop()
        assert len(fetch_calls) == 1

    def test_reconnects_on_connection_lost(self, monkeypatch, idle_conn):
        monkeypatch.setattr(email_reader, "_imap_connect", lambda db_path=None: idle_conn)
        monkeypatch.setattr(email_reader, "_safe_close", lambda c: None)

        do_calls = []

        def raise_then_stop(self, conn):
            do_calls.append(1)
            if len(do_calls) == 1:
                raise ConnectionLost()
            self._stop.set()
            return False

        monkeypatch.setattr(idle_mod.IdleMonitor, "_do_idle", raise_then_stop)
        m = IdleMonitor()
        m._idle_loop()
        assert len(do_calls) >= 1

    def test_breaks_on_connection_lost_when_stop_set(self, monkeypatch, idle_conn):
        monkeypatch.setattr(email_reader, "_imap_connect", lambda db_path=None: idle_conn)
        monkeypatch.setattr(email_reader, "_safe_close", lambda c: None)

        def set_stop_then_raise(self, conn):
            self._stop.set()
            raise ConnectionLost()

        monkeypatch.setattr(idle_mod.IdleMonitor, "_do_idle", set_stop_then_raise)
        IdleMonitor()._idle_loop()

    def test_closes_connection_in_finally(self, monkeypatch, idle_conn):
        monkeypatch.setattr(email_reader, "_imap_connect", lambda db_path=None: idle_conn)
        close_calls = []
        monkeypatch.setattr(email_reader, "_safe_close", lambda c: close_calls.append(c))

        do_calls = []

        def do_then_stop(self, conn):
            do_calls.append(1)
            if len(do_calls) >= 2:
                self._stop.set()
            return False

        monkeypatch.setattr(idle_mod.IdleMonitor, "_do_idle", do_then_stop)
        m = IdleMonitor()
        m._idle_loop()
        assert len(close_calls) >= 1


class TestFetchNew:
    def test_calls_run_initial_fetch_with_params(self, monkeypatch):
        kwargs = {}
        monkeypatch.setattr(idle_mod, "run_initial_fetch", lambda **kw: kwargs.update(kw))

        def cb():
            return None

        m = IdleMonitor(db_path="/x.db", on_refresh=cb)
        m._fetch_new()
        assert kwargs["db_path"] == "/x.db"
        assert kwargs["on_refresh"] is cb

    def test_swallows_exception(self, monkeypatch):
        monkeypatch.setattr(idle_mod, "run_initial_fetch", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        IdleMonitor()._fetch_new()


class TestRunInitialFetch:
    def _patch_sync_all_mail(self, monkeypatch):
        monkeypatch.setattr(
            email_reader,
            "sync_all_mail",
            lambda db_path=None: {"synced": 0, "skipped": 0, "hashes": set()},
        )

    def test_calls_fetch_headers_and_cache(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(
            email_reader, "fetch_headers_and_cache", lambda db_path=None, **kw: {"new_count": 0, "existing_count": 0}
        )
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        result = run_initial_fetch(db_path="/db")
        assert result["new_count"] == 0

    def test_returns_error_result(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(email_reader, "fetch_headers_and_cache", lambda db_path=None, **kw: {"error": "bad"})
        result = run_initial_fetch(db_path="/db")
        assert result["error"] == "bad"

    def test_calls_on_refresh_three_times(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(email_reader, "fetch_headers_and_cache", lambda db_path=None, **kw: {"new_count": 0})
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        calls = []
        run_initial_fetch(db_path="/db", on_refresh=lambda: calls.append(1))
        assert len(calls) == 3

    def test_swallows_on_refresh_exception(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(email_reader, "fetch_headers_and_cache", lambda db_path=None, **kw: {"new_count": 0})
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        run_initial_fetch(db_path="/db", on_refresh=lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    def test_fetches_bodies_for_imap_id_pairs(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(
            email_reader,
            "fetch_headers_and_cache",
            lambda db_path=None, **kw: {"new_count": 1, "imap_id_pairs": [(b"1", "<m@e.com>")]},
        )
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        fetches = []
        monkeypatch.setattr(email_reader, "fetch_bodies_for_ids", lambda pairs, db_path=None: fetches.append(pairs))
        run_initial_fetch(db_path="/db")
        assert len(fetches) == 1

    def test_fetches_bodies_for_headers_only_ids(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(email_reader, "fetch_headers_and_cache", lambda db_path=None, **kw: {"new_count": 0})
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: ["<h@e.com>"])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        fetches = []
        monkeypatch.setattr(email_reader, "fetch_bodies_by_message_ids", lambda ids, db_path=None: fetches.append(ids))
        run_initial_fetch(db_path="/db")
        assert len(fetches) == 1

    def test_scans_emails(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(email_reader, "fetch_headers_and_cache", lambda db_path=None, **kw: {"new_count": 0})
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [{"message_id": "<m@e.com>"}])
        scans = []
        monkeypatch.setattr(email_reader, "scan_emails", lambda emails, db_path=None: scans.append(emails))
        run_initial_fetch(db_path="/db")
        assert len(scans) == 1

    def test_top_level_exception_returns_error(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(
            email_reader,
            "fetch_headers_and_cache",
            lambda db_path=None, **kw: (_ for _ in ()).throw(RuntimeError("kaboom")),
        )
        result = run_initial_fetch(db_path="/db")
        assert "error" in result

    def test_calls_sync_sent_replies(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(email_reader, "fetch_headers_and_cache", lambda db_path=None, **kw: {"new_count": 0})
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        calls = []
        monkeypatch.setattr(
            email_reader, "sync_sent_replies", lambda db_path=None: calls.append(db_path) or {"synced": 0, "skipped": 0}
        )
        run_initial_fetch(db_path="/db")
        assert calls == ["/db"]

    def test_swallows_sync_sent_replies_exception(self, monkeypatch):
        self._patch_sync_all_mail(monkeypatch)
        monkeypatch.setattr(email_reader, "fetch_headers_and_cache", lambda db_path=None, **kw: {"new_count": 0})
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        monkeypatch.setattr(
            email_reader, "sync_sent_replies", lambda db_path=None: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        run_initial_fetch(db_path="/db")

    def test_calls_sync_all_mail_before_inbox_fetch(self, monkeypatch):
        order = []
        monkeypatch.setattr(
            email_reader,
            "sync_all_mail",
            lambda db_path=None: order.append("all_mail") or {"synced": 0, "skipped": 0, "hashes": set()},
        )
        monkeypatch.setattr(
            email_reader,
            "fetch_headers_and_cache",
            lambda db_path=None, **kw: order.append("inbox") or {"new_count": 0},
        )
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        run_initial_fetch(db_path="/db")
        assert order[0] == "all_mail"
        assert order[1] == "inbox"

    def test_passes_all_mail_hashes_as_protected(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            email_reader,
            "sync_all_mail",
            lambda db_path=None: {"synced": 0, "skipped": 0, "hashes": {"abc", "def"}},
        )
        monkeypatch.setattr(
            email_reader,
            "fetch_headers_and_cache",
            lambda db_path=None, protected_hashes=None, **kw: (
                seen.update(protected=protected_hashes) or {"new_count": 0}
            ),
        )
        monkeypatch.setattr(cache, "get_headers_only_message_ids", lambda db_path=None: [])
        monkeypatch.setattr(cache, "read_emails", lambda db_path, limit=None: [])
        run_initial_fetch(db_path="/db")
        assert seen["protected"] == {"abc", "def"}
