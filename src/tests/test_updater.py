import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from io import BytesIO
from pathlib import Path

from src.scripts import updater


def _run_swap_helper(responses: dict, args: list, inspect_state: dict | None = None):
    class _Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    data += chunk
            except OSError:
                return
            parts = data.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
            method = parts[0] if parts else ""
            path = parts[1] if len(parts) >= 2 else ""
            status = 204
            body = b""
            if path:
                recorded.append((method, path))
                for key, val in responses.items():
                    if key in path:
                        status = val
                        break
            if method == "GET" and path.endswith("/json"):
                status = 200
                state = {"State": {"Running": True, "Health": {"Status": "healthy"}}}
                if inspect_state:
                    for key, val in inspect_state.items():
                        if key in path:
                            state = val
                            break
                body = json.dumps(state).encode()
            head = ("HTTP/1.1 %d OK\r\nContent-Length: %d\r\n\r\n" % (status, len(body))).encode()
            try:
                self.request.sendall(head + body)
            except OSError:
                return

    recorded: list = []
    base = "/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir()
    work = tempfile.mkdtemp(prefix="sw", dir=base)
    sock_path = os.path.join(work, "s.sock")
    server = socketserver.UnixStreamServer(sock_path, _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = updater._SWAP_HELPER_SCRIPT % {"api": updater.DOCKER_API, "health_timeout": 3}
        script = script.replace('SOCK = "/var/run/docker.sock"', "SOCK = %r" % sock_path)
        proc = subprocess.run(
            [sys.executable, "-c", script, *args],
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        for path in (sock_path, work):
            try:
                os.unlink(path)
            except OSError:
                pass
    return recorded, proc.returncode


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

    def test_parse_strips_alpha_beta_prerelease(self):
        assert updater._parse_version("1.5.0-alpha") == (1, 5, 0)
        assert updater._parse_version("1.5.0-beta.1") == (1, 5, 0)
        assert updater._parse_version("2.0.0-rc1") == (2, 0, 0)
        assert updater._parse_version("1.5.0+build.7") == (1, 5, 0)


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

    def test_clean_patch_progression(self):
        assert updater.is_newer("1.5.1", "1.5.0") is True
        assert updater.is_newer("1.5.0", "1.5.1") is False

    def test_prerelease_not_treated_as_newer(self):
        assert updater.is_newer("1.5.0-beta", "1.5.0") is False
        assert updater.is_newer("1.5.0-alpha.3", "1.5.0") is False
        assert updater.is_newer("2.0.0-rc1", "2.0.0") is False


class TestGetCurrentVersion:
    def test_returns_nonempty_string(self):
        v = updater.get_current_version()
        assert isinstance(v, str) and v

    def test_returns_none_when_undetectable(self, monkeypatch):
        class _UnreadablePath:
            def __init__(self, target):
                pass

            def read_text(self, errors="ignore"):
                raise OSError("no pyproject")

        monkeypatch.setattr(updater, "Path", lambda target: _UnreadablePath(target))

        def _missing(_name):
            raise ModuleNotFoundError("not installed")

        monkeypatch.setattr("importlib.metadata.version", _missing)
        assert updater.get_current_version() is None


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
        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=10, **k: FakeResp(payload))
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

        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=10, **k: FakeResp(b"[]"))
        updater._latest_cache["value"] = None
        assert updater.fetch_latest_version(force=True) is None

    def test_returns_none_on_network_error(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("network down")

        monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
        updater._latest_cache["value"] = None
        assert updater.fetch_latest_version(force=True) is None

    def test_fetch_detects_github_rate_limit(self, monkeypatch):
        # GitHub rejects with 403 when the unauthenticated quota is exhausted.
        def rate_limited(*a, **k):
            raise urllib.error.HTTPError(updater.TAGS_URL, 403, "Forbidden", hdrs={}, fp=BytesIO(b"{}"))

        monkeypatch.setattr(updater.urllib.request, "urlopen", rate_limited)
        updater._latest_cache["value"] = None
        updater.reset_rate_limit()
        try:
            assert updater.fetch_latest_version(force=True) is None
            assert updater.is_rate_limited() is True
        finally:
            updater.reset_rate_limit()

    def test_successful_fetch_clears_rate_limit_flag(self, monkeypatch):
        class FakeResp:
            def __init__(self, data):
                self._buf = BytesIO(data)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return self._buf.read()

        payload = json.dumps([{"name": "v1.4.0"}]).encode()
        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=10, **k: FakeResp(payload))
        updater._latest_cache["value"] = None
        updater._rate_limited_at = time.monotonic()  # pretend a prior call was limited
        try:
            assert updater.fetch_latest_version(force=True) == "v1.4.0"
            assert updater.is_rate_limited() is False
        finally:
            updater.reset_rate_limit()
            updater._latest_cache["value"] = None

    def test_uses_cache_when_fresh(self):
        updater._latest_cache["value"] = "v9.9.9"
        updater._latest_cache["at"] = float("inf")
        try:
            assert updater.fetch_latest_version(force=False) == "v9.9.9"
        finally:
            updater._latest_cache["value"] = None
            updater._latest_cache["at"] = 0.0

    def test_concurrent_calls_share_one_fetch(self, monkeypatch):
        calls = {"n": 0}

        class FakeResp:
            def __init__(self, data):
                self._buf = BytesIO(data)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return self._buf.read()

        def fake_urlopen(req, timeout=10, **k):
            calls["n"] += 1
            time.sleep(0.5)
            return FakeResp(json.dumps([{"name": "v1.4.0"}]).encode())

        monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
        updater._latest_cache["value"] = None
        updater._latest_cache["at"] = 0.0

        barrier = threading.Barrier(2)
        results: list = [None, None]

        def runner(idx, force):
            barrier.wait()
            results[idx] = updater.fetch_latest_version(force=force)

        threads = [
            threading.Thread(target=runner, args=(0, True)),
            threading.Thread(target=runner, args=(1, False)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert calls["n"] == 1
        assert results == ["v1.4.0", "v1.4.0"]
        updater._latest_cache["value"] = None
        updater._latest_cache["at"] = 0.0

    def test_force_bypasses_fresh_cache(self, monkeypatch):
        calls = {"n": 0}

        class FakeResp:
            def __init__(self, data):
                self._buf = BytesIO(data)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return self._buf.read()

        def fake_urlopen(req, timeout=10, **k):
            calls["n"] += 1
            return FakeResp(json.dumps([{"name": "v1.5.0"}]).encode())

        monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
        updater._latest_cache["value"] = "v9.9.9"
        updater._latest_cache["at"] = float("inf")  # fresh
        try:
            # A lone force call must still fetch even when the cache is fresh.
            assert updater.fetch_latest_version(force=True) == "v1.5.0"
            assert calls["n"] == 1
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

    def test_error_message_when_version_undetectable(self, monkeypatch):
        monkeypatch.setattr(updater, "get_current_version", lambda: None)
        monkeypatch.setattr(updater, "fetch_latest_version", lambda force=False: "v1.3.0")
        result = updater.check_for_update()
        assert result["error"] is True
        assert result["update_available"] is False
        assert result["current"] is None
        assert "current version" in result["message"].lower()

    def test_no_false_positive_when_version_undetectable(self, monkeypatch):
        monkeypatch.setattr(updater, "get_current_version", lambda: None)
        monkeypatch.setattr(updater, "fetch_latest_version", lambda force=False: "v9.9.9")
        result = updater.check_for_update()
        assert result["update_available"] is False

    def test_network_error_message(self, monkeypatch):
        monkeypatch.setattr(updater, "get_current_version", lambda: "1.2.0")
        monkeypatch.setattr(updater, "fetch_latest_version", lambda force=False: None)
        result = updater.check_for_update()
        assert result["error"] is True
        assert "version source" in result["message"].lower()


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

    def test_failed_state_records_timestamp_and_detail(self):
        updater._set_state("failed", "boom", "raw daemon: nope")
        state = updater.update_state()
        assert state["phase"] == "failed"
        assert state["message"] == "boom"
        assert state["error_detail"] == "raw daemon: nope"
        assert state["failed_at"] > 0.0

    def test_active_state_clears_error_detail(self):
        # Entering an active phase should reset prior failure fields.
        updater._set_state("failed", "boom", "detail")
        updater._set_state("pulling", "retrying")
        state = updater.update_state()
        assert state["error_detail"] == ""
        assert state["failed_at"] == 0.0

    def test_worker_surfaces_detail_from_failure_result(self, monkeypatch):
        monkeypatch.setattr(
            updater,
            "_perform_update_sync",
            lambda: {"ok": False, "message": "boom", "detail": "raw daemon: x"},
        )
        updater._update_worker()
        state = updater.update_state()
        assert state["phase"] == "failed"
        assert state["error_detail"] == "raw daemon: x"
        assert state["failed_at"] > 0.0
        updater._set_state("idle")

    def test_worker_captures_traceback_on_crash(self, monkeypatch):
        def boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(updater, "_perform_update_sync", boom)
        updater._update_worker()
        state = updater.update_state()
        assert state["phase"] == "failed"
        assert "Traceback" in state["error_detail"]
        assert "kaboom" in state["message"]
        updater._set_state("idle")

    def test_concurrent_trigger_update_one_starts_one_rejects(self, monkeypatch):
        started = threading.Event()

        def blocking_worker():
            started.set()
            # Hold the active phase until the test releases us.
            hold.wait(5)
            updater._set_state("idle")

        hold = threading.Event()
        monkeypatch.setattr(updater, "_update_worker", blocking_worker)
        updater._set_state("idle")

        results: list = [None, None]
        barrier = threading.Barrier(2)

        def runner(idx):
            barrier.wait()
            results[idx] = updater.trigger_update()

        threads = [threading.Thread(target=runner, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        hold.set()  # release the stubbed worker
        for _ in range(200):
            if updater.update_state()["phase"] == "idle":
                break
            time.sleep(0.01)

        assert sorted(results) == [False, True]


class TestCurrentContainerId:
    @staticmethod
    def _fake_path(files):
        class _P:
            def __init__(self, target):
                self._target = str(target)

            def read_text(self, errors="ignore"):
                return files.get(self._target, "")

        return lambda target: _P(target)

    def test_resolves_via_proc_when_hostname_overridden(self, monkeypatch):
        real_id = "a" * 64
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setattr(
            updater,
            "Path",
            self._fake_path(
                {
                    "/proc/1/cgroup": "0::/\n",
                    "/proc/self/cgroup": "0::/\n",
                    "/proc/1/mountinfo": f"1234 /var/lib/docker/containers/{real_id}/{real_id}-json.log\n",
                }
            ),
        )

        def fake_request(method, path, body=None, timeout=30):
            if path == f"/v1.41/containers/{real_id}/json":
                return 200, json.dumps({"Config": {"Image": updater.DEFAULT_IMAGE}}).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() == real_id

    def test_falls_back_to_hostname_prefix(self, monkeypatch):
        monkeypatch.setenv("HOSTNAME", "abcd1234abcd")
        monkeypatch.setattr(updater, "Path", self._fake_path({}))

        def fake_request(method, path, body=None, timeout=30):
            if path.endswith("/containers/json?all=true"):
                return 200, json.dumps([{"Id": "abcd1234abcd5678", "Image": "x"}]).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() == "abcd1234abcd5678"

    def test_returns_none_when_nothing_matches(self, monkeypatch):
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.setattr(updater, "Path", self._fake_path({}))

        def fake_request(method, path, body=None, timeout=30):
            if path.endswith("/containers/json?all=true"):
                return 200, json.dumps([{"Id": "zzzz", "Image": "other"}]).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() is None

    def test_resolves_via_cgroup_v2_path(self, monkeypatch):
        # cgroup v2 embeds the container id directly in the cgroup path.
        real_id = "b" * 64
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setattr(
            updater,
            "Path",
            self._fake_path({"/proc/1/cgroup": f"0::/docker/{real_id}\n"}),
        )

        def fake_request(method, path, body=None, timeout=30):
            if path == f"/v1.41/containers/{real_id}/json":
                return 200, json.dumps({"Config": {"Image": updater.DEFAULT_IMAGE}}).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() == real_id

    def test_multiple_proc_candidates_first_invalid(self, monkeypatch):
        spurious = "c" * 64
        real_id = "d" * 64
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setattr(
            updater,
            "Path",
            self._fake_path({"/proc/1/cgroup": f"0::/docker/{spurious}\n1::/docker/{real_id}\n"}),
        )

        def fake_request(method, path, body=None, timeout=30):
            if path == f"/v1.41/containers/{spurious}/json":
                return 200, json.dumps({"Config": {"Image": "tailscale/tailscale:latest"}}).encode()
            if path == f"/v1.41/containers/{real_id}/json":
                return 200, json.dumps({"Config": {"Image": updater.DEFAULT_IMAGE}}).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() == real_id

    def test_rejects_tailscale_cid_from_mountinfo(self, monkeypatch):
        ts_cid = "e" * 64
        web_cid = "f" * 64
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setattr(
            updater,
            "Path",
            self._fake_path(
                {
                    "/proc/1/cgroup": "0::/\n",
                    "/proc/self/cgroup": "0::/\n",
                    "/proc/1/mountinfo": (
                        f"1234 /var/lib/docker/containers/{ts_cid}/hosts /etc/hosts\n"
                        f"1235 /var/lib/docker/containers/{ts_cid}/hostname /etc/hostname\n"
                        f"1236 /var/lib/docker/containers/{ts_cid}/resolv.conf /etc/resolv.conf\n"
                    ),
                }
            ),
        )
        image = updater.DEFAULT_IMAGE
        image_id = "sha256:cafef00d" + "0" * 55

        def fake_request(method, path, body=None, timeout=30):
            if path == f"/v1.41/containers/{ts_cid}/json":
                return 200, json.dumps({"Config": {"Image": "tailscale/tailscale:latest"}}).encode()
            if path == f"/v1.41/images/{image}/json":
                return 200, json.dumps({"Id": image_id}).encode()
            if path.endswith("/containers/json?all=true"):
                return 200, json.dumps([{"Id": web_cid, "ImageID": image_id, "Image": image}]).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() == web_cid

    def test_falls_back_to_single_own_image(self, monkeypatch):
        # HOSTNAME and /proc both useless; exactly one container runs our image.
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.setattr(updater, "Path", self._fake_path({}))
        image = updater.DEFAULT_IMAGE
        image_id = "sha256:cafef00d" + "0" * 55

        def fake_request(method, path, body=None, timeout=30):
            if path == f"/v1.41/images/{image}/json":
                return 200, json.dumps({"Id": image_id}).encode()
            if path.endswith("/containers/json?all=true"):
                return 200, json.dumps([{"Id": "own1", "ImageID": image_id, "Image": image}]).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() == "own1"

    def test_image_heuristic_skipped_when_multiple_matches(self, monkeypatch):
        # Two containers share our image -> refuse to guess.
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.setattr(updater, "Path", self._fake_path({}))
        image = updater.DEFAULT_IMAGE
        image_id = "sha256:cafef00d" + "0" * 55

        def fake_request(method, path, body=None, timeout=30):
            if path == f"/v1.41/images/{image}/json":
                return 200, json.dumps({"Id": image_id}).encode()
            if path.endswith("/containers/json?all=true"):
                return 200, json.dumps(
                    [
                        {"Id": "own1", "ImageID": image_id, "Image": image},
                        {"Id": "own2", "ImageID": image_id, "Image": image},
                    ]
                ).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() is None

    def test_resolves_via_self_label(self, monkeypatch):
        web_cid = "a" * 64
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setattr(updater, "Path", self._fake_path({}))

        def fake_request(method, path, body=None, timeout=30):
            if "filters=" in path and "/containers/json" in path:
                assert "inbox-lens.self" in path
                return 200, json.dumps([{"Id": web_cid, "Image": updater.DEFAULT_IMAGE}]).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() == web_cid

    def test_label_lookup_rejected_when_image_mismatch(self, monkeypatch):
        # A container carrying our label but a different image must not be used.
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setattr(updater, "Path", self._fake_path({}))

        def fake_request(method, path, body=None, timeout=30):
            if "filters=" in path and "/containers/json" in path:
                return 200, json.dumps([{"Id": "stray", "Image": "other/image:latest"}]).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() is None

    def test_falls_through_to_proc_when_unlabeled(self, monkeypatch):
        real_id = "b" * 64
        monkeypatch.setenv("HOSTNAME", "inbox-lens")
        monkeypatch.delenv("INBOX_LENS_IMAGE", raising=False)
        monkeypatch.setattr(
            updater,
            "Path",
            self._fake_path({"/proc/1/cgroup": f"0::/docker/{real_id}\n"}),
        )

        def fake_request(method, path, body=None, timeout=30):
            if "filters=" in path and "/containers/json" in path:
                return 200, json.dumps([]).encode()
            if path == f"/v1.41/containers/{real_id}/json":
                return 200, json.dumps({"Config": {"Image": updater.DEFAULT_IMAGE}}).encode()
            return 404, b""

        monkeypatch.setattr(updater, "_docker_request", fake_request)
        assert updater._current_container_id() == real_id


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
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {"Name": "/inbox-lens-web-1", "Config": {"Env": ["X=1"]}, "HostConfig": {}}
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                return 201, json.dumps({"Id": "helperid"}).encode()
            if "/containers/helperid/start" in path:
                return 204, b""
            if "/containers/helperid/json" in path:
                return 200, json.dumps({"State": {"Status": "exited", "ExitCode": 0}}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        result = updater._perform_update_sync()
        assert result["ok"] is True
        paths = [c[1] for c in calls]
        assert any("/images/create" in p for p in paths)
        assert any("/containers/abc/json" in p for p in paths)
        assert any("create" in p and "inbox-lens-web-1-new" in p for p in paths)
        assert any("create" in p and "inbox-lens-web-1-swap" in p for p in paths)
        assert any("/containers/helperid/start" in p for p in paths)
        updater._set_state("idle")

    def test_helper_script_contains_swap_commands(self, monkeypatch):
        captured = {}

        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {"Name": "/inbox-lens-web-1", "Config": {"Env": ["X=1"]}, "HostConfig": {}}
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                captured["body"] = body
                return 201, json.dumps({"Id": "helperid"}).encode()
            if "/containers/helperid/start" in path:
                return 204, b""
            if "/containers/helperid/json" in path:
                return 200, json.dumps({"State": {"Status": "exited", "ExitCode": 0}}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        updater._perform_update_sync()

        hb = captured["body"]
        assert "AutoRemove" not in hb["HostConfig"]
        assert hb["Entrypoint"] == []
        assert "/var/run/docker.sock:/var/run/docker.sock" in hb["HostConfig"]["Binds"]
        cmd = hb["Cmd"]
        assert cmd[0] == "python3"
        assert "abc" in cmd
        assert "newid" in cmd
        assert "inbox-lens-web-1-failed" in cmd
        script_and_args = " ".join(cmd)
        assert "/var/run/docker.sock" in script_and_args
        assert "stop?t=10" in script_and_args
        assert "rename?name=" in script_and_args
        assert "/start" in script_and_args
        assert "force=true" in script_and_args
        assert "rollback" in script_and_args
        assert "HEALTH_TIMEOUT" in script_and_args
        updater._set_state("idle")

    def test_swap_helper_script_compiles(self):
        compile(
            updater._SWAP_HELPER_SCRIPT % {"api": updater.DOCKER_API, "health_timeout": updater._SWAP_HEALTH_TIMEOUT},
            "<swap_helper>",
            "exec",
        )

    def test_entrypoint_passes_through_args(self):
        entrypoint = Path(__file__).resolve().parents[2] / "entrypoint.sh"
        text = entrypoint.read_text()
        guard_idx = text.index("if [ $# -gt 0 ]")
        exec_idx = text.index('exec "$@"')
        data_idx = text.index("DATA_DIR=")
        assert exec_idx > guard_idx
        assert guard_idx < data_idx

    def test_swap_helper_aborts_when_stop_fails(self):
        recorded, rc = _run_swap_helper({"stop?t=10": 500}, ["old", "new", "oldn", "newn", "failedn"])
        paths = [p for _, p in recorded]
        assert any("stop?t=10" in p for p in paths)
        assert not any("/start" in p for p in paths)
        assert not any("force=true" in p for p in paths)
        assert rc != 0

    def test_swap_helper_runs_full_sequence_on_success(self):
        recorded, rc = _run_swap_helper({}, ["old", "new", "oldn", "newn", "failedn"])
        paths = [p for _, p in recorded]
        assert any("stop?t=10" in p for p in paths)
        assert any("rename?name=oldn" in p for p in paths)
        assert any("rename?name=newn" in p for p in paths)
        assert any("/start" in p for p in paths)
        assert any("force=true" in p for p in paths)
        assert rc == 0

    def test_swap_helper_rolls_back_when_new_unhealthy(self):
        unhealthy = {"State": {"Running": True, "Health": {"Status": "unhealthy"}}}
        recorded, rc = _run_swap_helper(
            {}, ["old", "new", "oldn", "newn", "failedn"], inspect_state={"/containers/new/json": unhealthy}
        )
        paths = [p for _, p in recorded]
        assert rc != 0  # rolled back
        assert any("/containers/old/stop" in p for p in paths)
        assert any("/containers/new/stop" in p for p in paths)
        assert any("rename?name=failedn" in p for p in paths)
        assert any("rename?name=newn" in p for p in paths)
        assert any("/containers/old/start" in p for p in paths)
        # The old container is NOT deleted on rollback (only on success).
        assert not any("/containers/old" in p and "force=true" in p for p in paths)

    def test_swap_helper_rolls_back_on_health_timeout(self):
        starting = {"State": {"Running": True, "Health": {"Status": "starting"}}}
        recorded, rc = _run_swap_helper(
            {}, ["old", "new", "oldn", "newn", "failedn"], inspect_state={"/containers/new/json": starting}
        )
        paths = [p for _, p in recorded]
        assert rc != 0  # timed out + rolled back
        assert any("/containers/new/stop" in p for p in paths)
        assert any("rename?name=failedn" in p for p in paths)
        assert any("/containers/old/start" in p for p in paths)

    def test_swap_helper_aborts_on_start_network_conflict(self):
        recorded, rc = _run_swap_helper({"/containers/new/start": 409}, ["old", "new", "oldn", "newn", "failedn"])
        paths = [p for _, p in recorded]
        assert rc != 0
        assert any("/containers/new/stop" in p for p in paths)
        assert any("rename?name=failedn" in p for p in paths)
        assert any("/containers/old/start" in p for p in paths)

    def test_cleans_up_stale_containers(self, monkeypatch):
        deleted = []

        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {"Name": "/inbox-lens-web-1", "Config": {"Env": ["X=1"]}, "HostConfig": {}}
                ).encode()
            if "create" in path and method == "POST":
                return 201, json.dumps({"Id": "xid"}).encode()
            if "/start" in path:
                return 204, b""
            if method == "DELETE":
                deleted.append(path)
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        updater._perform_update_sync()

        assert any("inbox-lens-web-1-old" in p for p in deleted)
        assert any("inbox-lens-web-1-new" in p for p in deleted)
        assert any("inbox-lens-web-1-swap" in p for p in deleted)
        updater._set_state("idle")

    def test_healthcheck_copied_to_new_container(self, monkeypatch):
        captured = {}

        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {
                        "Name": "/inbox-lens-web-1",
                        "Config": {
                            "Env": ["X=1"],
                            "Healthcheck": {
                                "Test": ["CMD", "python3", "/app/src/scripts/healthcheck.py"],
                            },
                            "ExposedPorts": {"8000/tcp": {}},
                        },
                        "HostConfig": {},
                    }
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                captured["body"] = body
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                return 201, json.dumps({"Id": "helperid"}).encode()
            if "/containers/helperid/start" in path:
                return 204, b""
            if "/containers/helperid/json" in path:
                return 200, json.dumps({"State": {"Status": "exited", "ExitCode": 0}}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        updater._perform_update_sync()

        assert "Healthcheck" in captured["body"]
        assert captured["body"]["Healthcheck"]["Test"][0] == "CMD"
        assert "ExposedPorts" in captured["body"]
        updater._set_state("idle")

    def test_helper_failure_cleans_up_new_container(self, monkeypatch):
        deleted = []

        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {"Name": "/inbox-lens-web-1", "Config": {"Env": ["X=1"]}, "HostConfig": {}}
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                return 500, b""
            if method == "DELETE":
                deleted.append(path)
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        result = updater._perform_update_sync()

        assert result["ok"] is False
        assert any("newid" in p for p in deleted)
        updater._set_state("idle")

    def test_stale_bind_failure_rolls_back_and_surfaces_detail(self, monkeypatch):
        deleted = []

        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path and method == "GET":
                return 200, json.dumps(
                    {"Name": "/inbox-lens-web-1", "Config": {"Env": ["X=1"]}, "HostConfig": {}}
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                return 201, json.dumps({"Id": "swapid"}).encode()
            if path.endswith("/containers/swapid/start"):
                return 204, b""
            if path.endswith("/containers/swapid/json"):
                return 200, json.dumps({"State": {"Status": "exited", "ExitCode": 1}}).encode()
            if "/containers/swapid/logs" in path:
                return 200, b"bad mount path: /missing/host/path"
            if method == "DELETE":
                deleted.append(path)
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        monkeypatch.setattr(updater.time, "sleep", lambda *_: None)
        result = updater._perform_update_sync()

        assert result["ok"] is False
        assert result["message"] == "Update failed. The previous container was restored."
        assert any("newid" in p for p in deleted)
        assert any("inbox-lens-web-1-failed" in p for p in deleted)
        assert "/missing/host/path" in result["detail"]
        updater._set_state("idle")

    def test_create_failure_surfaces_daemon_message(self, monkeypatch):
        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {"Name": "/inbox-lens-web-1", "Config": {"Env": ["X=1"]}, "HostConfig": {}}
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                return 400, json.dumps({"message": "boom: detailed reason"}).encode()
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        result = updater._perform_update_sync()

        assert result["ok"] is False
        assert "HTTP 400" in result["message"]
        assert "boom: detailed reason" in result["message"]
        updater._set_state("idle")

    def test_pull_failure_surfaces_daemon_message(self, monkeypatch):
        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 500, json.dumps({"message": "manifest unknown"}).encode()
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        result = updater._perform_update_sync()

        assert result["ok"] is False
        assert "manifest unknown" in result["message"].lower()
        updater._set_state("idle")

    def test_drops_exposed_ports_under_container_network_mode(self, monkeypatch):
        captured = {}

        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {
                        "Name": "/inbox-lens-web-1",
                        "Config": {
                            "Env": ["X=1"],
                            "ExposedPorts": {"8000/tcp": {}},
                            "Healthcheck": {
                                "Test": ["CMD", "python3", "/app/src/scripts/healthcheck.py"],
                            },
                        },
                        "HostConfig": {
                            "NetworkMode": "container:e0c1ad6f2cfc",
                            "PortBindings": {"8000/tcp": [{"HostPort": "8000"}]},
                            "RestartPolicy": {"Name": "unless-stopped"},
                        },
                        "NetworkSettings": {"Networks": {}},
                    }
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                captured["body"] = body
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                return 201, json.dumps({"Id": "helperid"}).encode()
            if "/containers/helperid/start" in path:
                return 204, b""
            if "/containers/helperid/json" in path:
                return 200, json.dumps({"State": {"Status": "exited", "ExitCode": 0}}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        result = updater._perform_update_sync()

        assert result["ok"] is True, result
        body = captured["body"]
        assert "ExposedPorts" not in body
        assert "PortBindings" not in body["HostConfig"]
        assert "NetworkingConfig" not in body
        assert body["HostConfig"]["NetworkMode"] == "container:e0c1ad6f2cfc"
        updater._set_state("idle")

    def test_keeps_exposed_ports_under_bridge(self, monkeypatch):
        captured = {}

        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {
                        "Name": "/inbox-lens-web-1",
                        "Config": {"Env": ["X=1"], "ExposedPorts": {"8000/tcp": {}}},
                        "HostConfig": {"NetworkMode": "bridge"},
                    }
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                captured["body"] = body
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                return 201, json.dumps({"Id": "helperid"}).encode()
            if "/containers/helperid/start" in path:
                return 204, b""
            if "/containers/helperid/json" in path:
                return 200, json.dumps({"State": {"Status": "exited", "ExitCode": 0}}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        result = updater._perform_update_sync()

        assert result["ok"] is True, result
        assert captured["body"].get("ExposedPorts") == {"8000/tcp": {}}
        updater._set_state("idle")

    def test_sanitizes_network_endpoint_runtime_fields(self, monkeypatch):
        captured = {}

        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {
                        "Name": "/inbox-lens-web-1",
                        "Config": {"Env": ["X=1"]},
                        "HostConfig": {"NetworkMode": "bridge"},
                        "NetworkSettings": {
                            "Networks": {
                                "bridge": {
                                    "IPAMConfig": {"IPv4Address": "172.17.0.2"},
                                    "Links": None,
                                    "Aliases": ["web"],
                                    "EndpointID": "abc",
                                    "Gateway": "172.17.0.1",
                                    "IPAddress": "172.17.0.2",
                                    "MacAddress": "02:42:ac:11:00:02",
                                    "NetworkID": "deadbeef",
                                }
                            }
                        },
                    }
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                captured["body"] = body
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                return 201, json.dumps({"Id": "helperid"}).encode()
            if "/containers/helperid/start" in path:
                return 204, b""
            if "/containers/helperid/json" in path:
                return 200, json.dumps({"State": {"Status": "exited", "ExitCode": 0}}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        updater._perform_update_sync()

        ep = captured["body"]["NetworkingConfig"]["EndpointsConfig"]["bridge"]
        assert set(ep.keys()) == {"IPAMConfig", "Links", "Aliases"}
        assert ep["Aliases"] == ["web"]
        updater._set_state("idle")

    @staticmethod
    def _helper_fail_request_factory():
        def fake_request(method, path, body=None, timeout=30):
            if "/images/create" in path:
                return 200, b""
            if "/containers/abc/json" in path:
                return 200, json.dumps(
                    {"Name": "/inbox-lens-web-1", "Config": {"Env": ["X=1"]}, "HostConfig": {}}
                ).encode()
            if "create" in path and "inbox-lens-web-1-new" in path:
                return 201, json.dumps({"Id": "newid"}).encode()
            if "create" in path and "inbox-lens-web-1-swap" in path:
                return 400, json.dumps({"message": "helper rejected"}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""

        return fake_request

    def test_swap_helper_create_surfaces_daemon_message(self, monkeypatch, caplog):
        import logging
        
        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", self._helper_fail_request_factory())
        with caplog.at_level(logging.WARNING):
            result = updater._perform_update_sync()
        assert result["ok"] is False
        assert any("helper rejected" in rec.getMessage() for rec in caplog.records)
        updater._set_state("idle")


class TestCleanupStaleContainers:
    def test_preserves_foreign_named_container(self, monkeypatch):
        deleted = []

        def fake_request(method, path, body=None, timeout=30):
            if method == "GET" and path.endswith("/containers/selfcid/json"):
                return 200, json.dumps({"Name": "/inbox-lens-web-1"}).encode()
            if method == "DELETE":
                deleted.append(path)
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "selfcid")
        monkeypatch.setattr(updater, "_docker_request", fake_request)

        updater.cleanup_stale_containers()

        assert any("inbox-lens-web-1-old" in p for p in deleted)
        assert any("inbox-lens-web-1-new" in p for p in deleted)
        assert any("inbox-lens-web-1-swap" in p for p in deleted)
        assert any("inbox-lens-web-1-failed" in p for p in deleted)
        assert not any("myapp-old" in p for p in deleted)

    def test_no_self_cid_does_nothing(self, monkeypatch):
        deleted = []
        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: None)
        monkeypatch.setattr(updater, "_docker_request", lambda *a, **k: deleted.append("call") or (404, b""))

        updater.cleanup_stale_containers()
        assert deleted == []  # never inspects/deletes when we can't identify self

    def test_skips_when_not_docker_managed(self, monkeypatch):
        called = []
        monkeypatch.setattr(updater, "is_docker_managed", lambda: False)
        monkeypatch.setattr(updater, "_docker_request", lambda *a, **k: called.append("call") or (404, b""))
        updater.cleanup_stale_containers()
        assert called == []

    def test_returns_true_when_failed_container_present(self, monkeypatch):
        # A leftover <name>-failed container means the previous update rolled back.
        def fake_request(method, path, body=None, timeout=30):
            if method == "GET" and path.endswith("/containers/selfcid/json"):
                return 200, json.dumps({"Name": "/inbox-lens-web-1"}).encode()
            if method == "GET" and "inbox-lens-web-1-failed" in path:
                return 200, json.dumps({"State": {"Status": "exited"}}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "selfcid")
        monkeypatch.setattr(updater, "_docker_request", fake_request)

        assert updater.cleanup_stale_containers() is True

    def test_returns_false_when_no_failed_container(self, monkeypatch):
        def fake_request(method, path, body=None, timeout=30):
            if method == "GET" and path.endswith("/containers/selfcid/json"):
                return 200, json.dumps({"Name": "/inbox-lens-web-1"}).encode()
            if method == "DELETE":
                return 204, b""
            return 404, b""  # the -failed inspect misses

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "selfcid")
        monkeypatch.setattr(updater, "_docker_request", fake_request)

        assert updater.cleanup_stale_containers() is False


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
