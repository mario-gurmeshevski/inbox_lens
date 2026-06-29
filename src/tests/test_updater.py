import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
from io import BytesIO

from src.scripts import updater


def _run_swap_helper(responses: dict, args: list):
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
            status = 204
            if len(parts) >= 2:
                recorded.append((parts[0], parts[1]))
                for key, val in responses.items():
                    if key in parts[1]:
                        status = val
                        break
            head = ("HTTP/1.1 %d OK\r\nContent-Length: 0\r\n\r\n" % status).encode()
            try:
                self.request.sendall(head)
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
        script = updater._SWAP_HELPER_SCRIPT % {"api": updater.DOCKER_API}
        script = script.replace('SOCK = "/var/run/docker.sock"', 'SOCK = %r' % sock_path)
        script = script.replace("time.sleep(2)", "time.sleep(0)")
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
        # Reproduces the Tailscale setup: HOSTNAME is a name, not the short id.
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
            if method == "DELETE":
                return 204, b""
            return 404, b""

        monkeypatch.setattr(updater, "is_docker_managed", lambda: True)
        monkeypatch.setattr(updater, "_current_container_id", lambda: "abc")
        monkeypatch.setattr(updater, "_docker_request", fake_request)
        updater._perform_update_sync()

        hb = captured["body"]
        assert hb["HostConfig"]["AutoRemove"] is True
        assert "/var/run/docker.sock:/var/run/docker.sock" in hb["HostConfig"]["Binds"]
        cmd = hb["Cmd"]
        assert cmd[0] == "python3"
        assert "abc" in cmd
        assert "newid" in cmd
        script_and_args = " ".join(cmd)
        assert "/var/run/docker.sock" in script_and_args
        assert "time.sleep" in script_and_args
        assert "stop?t=10" in script_and_args
        assert "rename?name=" in script_and_args
        assert "/start" in script_and_args
        assert "force=true" in script_and_args
        updater._set_state("idle")

    def test_swap_helper_script_compiles(self):
        compile(
            updater._SWAP_HELPER_SCRIPT % {"api": updater.DOCKER_API},
            "<swap_helper>",
            "exec",
        )

    def test_swap_helper_aborts_when_stop_fails(self):
        recorded, rc = _run_swap_helper({"stop?t=10": 500}, ["old", "new", "oldn", "newn"])
        paths = [p for _, p in recorded]
        assert any("stop?t=10" in p for p in paths)
        assert not any("/start" in p for p in paths)
        assert not any("force=true" in p for p in paths)
        assert rc != 0

    def test_swap_helper_runs_full_sequence_on_success(self):
        recorded, rc = _run_swap_helper({}, ["old", "new", "oldn", "newn"])
        paths = [p for _, p in recorded]
        assert any("stop?t=10" in p for p in paths)
        assert any("rename?name=oldn" in p for p in paths)
        assert any("rename?name=newn" in p for p in paths)
        assert any("/start" in p for p in paths)
        assert any("force=true" in p for p in paths)
        assert rc == 0

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
        # The network mode itself must be preserved so the recreated container
        # keeps sharing the tailscale container's namespace.
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
