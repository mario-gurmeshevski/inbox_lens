import json
from io import BytesIO

from src.scripts import updater


class TestVersionParsing:
    def test_parse_plain(self):
        assert updater._parse_version("1.2.3") == (1, 2, 3)

    def test_parse_v_prefix(self):
        assert updater._parse_version("v1.2.3") == (1, 2, 3)
        assert updater._parse_version("V1.2.3") == (1, 2, 3)

    def test_parse_trailing_suffix(self):
        assert updater._parse_version("1.2.3-rc1") == (1, 2, 3)

    def test_parse_short(self):
        assert updater._parse_version("2") == (2,)

    def test_parse_empty(self):
        assert updater._parse_version("") == ()

    def test_parse_none(self):
        assert updater._parse_version(None) == ()


class TestIsNewer:
    def test_greater(self):
        assert updater.is_newer("1.3.0", "1.2.0") is True

    def test_equal(self):
        assert updater.is_newer("1.2.0", "1.2.0") is False

    def test_lower(self):
        assert updater.is_newer("1.1.0", "1.2.0") is False

    def test_major_jump(self):
        assert updater.is_newer("2.0.0", "1.9.9") is True

    def test_different_length(self):
        assert updater.is_newer("1.2", "1.1.9") is True

    def test_with_v_prefixes(self):
        assert updater.is_newer("v1.3.0", "v1.2.0") is True

    def test_malformed_returns_false(self):
        assert updater.is_newer(None, "1.0.0") is False


class TestGetCurrentVersion:
    def test_returns_nonempty_string(self):
        v = updater.get_current_version()
        assert isinstance(v, str) and v


class TestFetchLatestVersion:
    def test_parses_first_tag(self, monkeypatch):
        class FakeResp:
            def __init__(self, data):
                self._buf = BytesIO(data)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return self._buf.read()

        payload = json.dumps([{"name": "v1.4.0"}, {"name": "v1.3.0"}]).encode()
        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=10: FakeResp(payload))
        updater._latest_cache["value"] = None
        result = updater.fetch_latest_version(force=True)
        assert result == "v1.4.0"

    def test_returns_none_on_empty_tags(self, monkeypatch):
        class FakeResp:
            def __init__(self, data):
                self._buf = BytesIO(data)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return self._buf.read()

        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=10: FakeResp(b"[]"))
        updater._latest_cache["value"] = None
        assert updater.fetch_latest_version(force=True) is None

    def test_returns_none_on_network_error(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("network down")

        monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
        updater._latest_cache["value"] = None
        assert updater.fetch_latest_version(force=True) is None

    def test_uses_cache_when_fresh(self):
        updater._latest_cache["value"] = "v9.9.9"
        updater._latest_cache["at"] = float("inf")
        try:
            assert updater.fetch_latest_version(force=False) == "v9.9.9"
        finally:
            updater._latest_cache["value"] = None
            updater._latest_cache["at"] = 0.0


class TestCheckForUpdate:
    def test_update_available(self, monkeypatch):
        monkeypatch.setattr(updater, "get_current_version", lambda: "1.2.0")
        monkeypatch.setattr(updater, "fetch_latest_version", lambda force=False: "v1.3.0")
        result = updater.check_for_update()
        assert result["update_available"] is True
        assert result["current"] == "1.2.0"
        assert result["latest"] == "v1.3.0"
        assert result["error"] is False

    def test_up_to_date(self, monkeypatch):
        monkeypatch.setattr(updater, "get_current_version", lambda: "1.3.0")
        monkeypatch.setattr(updater, "fetch_latest_version", lambda force=False: "v1.3.0")
        result = updater.check_for_update()
        assert result["update_available"] is False

    def test_error_when_fetch_fails(self, monkeypatch):
        monkeypatch.setattr(updater, "get_current_version", lambda: "1.2.0")
        monkeypatch.setattr(updater, "fetch_latest_version", lambda force=False: None)
        result = updater.check_for_update()
        assert result["error"] is True
        assert result["update_available"] is False


class TestDockerDetection:
    def test_is_docker_environment_bool(self):
        assert isinstance(updater.is_docker_environment(), bool)

    def test_daemon_unavailable_without_socket(self, monkeypatch):
        class FakePath:
            def __init__(self, p):
                self.p = p

            def exists(self):
                return False

        monkeypatch.setattr(updater, "Path", FakePath)
        assert updater.docker_daemon_available() is False

    def test_is_docker_managed_requires_both(self, monkeypatch):
        monkeypatch.setattr(updater, "is_docker_environment", lambda: True)
        monkeypatch.setattr(updater, "docker_daemon_available", lambda: False)
        assert updater.is_docker_managed() is False
        monkeypatch.setattr(updater, "docker_daemon_available", lambda: True)
        assert updater.is_docker_managed() is True


class TestUpdateState:
    def setup_method(self):
        updater._set_state("idle")

    def test_initial_idle(self):
        assert updater.update_state()["phase"] == "idle"

    def test_update_in_progress_flag(self):
        updater._set_state("pulling", "working")
        assert updater.update_in_progress() is True
        updater._set_state("idle")
        assert updater.update_in_progress() is False

    def test_trigger_rejects_when_active(self):
        updater._set_state("pulling")
        assert updater.trigger_update() is False
        updater._set_state("idle")

    def test_trigger_starts_worker(self, monkeypatch):
        started = {"called": False}

        def fake_worker():
            started["called"] = True
            updater._set_state("succeeded", "done")

        monkeypatch.setattr(updater, "_update_worker", fake_worker)
        assert updater.trigger_update() is True
        import time

        for _ in range(50):
            if started["called"]:
                break
            time.sleep(0.01)
        assert started["called"] is True
        updater._set_state("idle")


class TestPerformUpdateSync:
    def setup_method(self):
        updater._set_state("idle")

    def test_blocks_when_not_managed(self, monkeypatch):
        monkeypatch.setattr(updater, "is_docker_managed", lambda: False)
        result = updater._perform_update_sync()
        assert result["ok"] is False

    def test_blocks_when_no_container_id(self, monkeypatch):
        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: None)
        result = updater._perform_update_sync()
        assert result["ok"] is False
        assert "container" in result["message"].lower()

    def test_pull_failure_aborts(self, monkeypatch):
        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "INBOX_LENS_IMAGE", None, raising=False)
        monkeypatch.setattr(updater, "_docker_request", lambda *a, **k: (500, b""))
        result = updater._perform_update_sync()
        assert result["ok"] is False
        assert "pull" in result["message"].lower()

    def test_full_success_path(self, monkeypatch):
        calls = []

        def fake_request(method, path, body=None, timeout=30):
            calls.append((method, path))
            if path.endswith("/images/create"):
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {"Name": "/inbox-lens-web-1", "Config": {"Env": ["X=1"]}, "HostConfig": {}}
                ).encode()
            if path.endswith("/containers/create?name=inbox-lens-web-1-new"):
                return 201, json.dumps({"Id": "newid"}).encode()
            return 200, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        result = updater._perform_update_sync()
        assert result["ok"] is True
        methods = [c[0] for c in calls]
        # pull, inspect, create, stop, rename(old), rename(new), start, delete
        assert "POST" in methods
        assert "DELETE" in methods
        updater._set_state("idle")


class TestUpdateChecker:
    def test_check_now_returns_result(self, monkeypatch):
        monkeypatch.setattr(
            updater, "check_for_update", lambda force=True: {"update_available": False, "current": "1.0.0"}
        )
        checker = updater.UpdateChecker(on_update=lambda r: None)
        result = checker.check_now()
        assert result["current"] == "1.0.0"

    def test_start_stop_idempotent(self):
        checker = updater.UpdateChecker(on_update=lambda r: None, interval=9999)
        checker.start()
        checker.start()
        assert checker.running is True
        checker.stop()
        assert checker.running is False
