"""In-app update system.

Detects the deployment environment, checks for newer versions against GitHub,
and (when running in a Docker-managed deployment with the Docker socket
mounted) can pull the latest image and recreate the running container.

The Docker self-update talks to the local Docker Engine API over the mounted
unix socket (no compose path-resolution issues, no extra dependencies). The
sequence mirrors what Watchtower does: pull -> create replacement -> stop old
-> rename -> start new -> remove old.
"""

import http.client
import json
import logging
import os
import socket
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_OWNER = "mario-gurmeshevski"
REPO_NAME = "inbox_lens"
TAGS_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/tags"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"

DOCKER_SOCKET = "/var/run/docker.sock"
DOCKER_API = "/v1.41"
DEFAULT_IMAGE = f"ghcr.io/{REPO_OWNER}/{REPO_NAME}:latest"

CHECK_INTERVAL = 6 * 60 * 60  # 6 hours between background checks
FETCH_CACHE_TTL = 15 * 60  # 15 min cache to respect GitHub rate limits
UPDATE_TIMEOUT = 5 * 60  # 5 min ceiling for the whole update

DISMISSED_VERSION_KEY = "update_dismissed_version"


# --------------------------------------------------------------------------- #
# Version helpers
# --------------------------------------------------------------------------- #
def get_current_version() -> str:
    try:
        from importlib.metadata import version as _v

        try:
            return _v("inbox-lens")
        except Exception:
            return _v("inbox_lens")
    except Exception:
        pass
    try:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        for line in pyproject.read_text().splitlines():
            line = line.strip()
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "0.0.0"


def _parse_version(v: str) -> tuple[int, ...]:
    v = (v or "").strip().lstrip("vV")
    if not v:
        return ()
    parts: list[int] = []
    for chunk in v.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    try:
        return _parse_version(latest) > _parse_version(current)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Latest-version fetch (GitHub tags API)
# --------------------------------------------------------------------------- #
_latest_cache: dict = {"value": None, "at": 0.0}
_cache_lock = threading.Lock()


def fetch_latest_version(force: bool = False) -> str | None:
    now = time.monotonic()
    with _cache_lock:
        cached = _latest_cache["value"]
        if not force and cached is not None and (now - _latest_cache["at"]) < FETCH_CACHE_TTL:
            return cached
    try:
        req = urllib.request.Request(
            TAGS_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "inbox-lens-updater",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        if not isinstance(tags, list) or not tags:
            return None
        name = tags[0].get("name", "")
        if not name:
            return None
        with _cache_lock:
            _latest_cache["value"] = name
            _latest_cache["at"] = now
        return name
    except Exception:
        logger.warning("Failed to fetch latest version", exc_info=True)
        return None


def check_for_update(force: bool = False) -> dict:
    current = get_current_version()
    latest = fetch_latest_version(force=force)
    if latest is None:
        return {"current": current, "latest": None, "update_available": False, "error": True}
    return {
        "current": current,
        "latest": latest,
        "update_available": is_newer(latest, current),
        "error": False,
    }


# --------------------------------------------------------------------------- #
# Environment detection
# --------------------------------------------------------------------------- #
def is_docker_environment() -> bool:
    return Path("/.dockerenv").exists()


def _docker_request(method: str, path: str, body=None, timeout: float = 30) -> tuple[int, bytes]:
    conn = _UnixHTTPConnection(DOCKER_SOCKET, timeout=timeout)
    headers = {"Host": "localhost"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
    finally:
        conn.close()
    return status, raw


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, unix_socket: str, timeout: float = 30):
        super().__init__("localhost", timeout=timeout)
        self._unix_socket = unix_socket

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._unix_socket)


def docker_daemon_available() -> bool:
    if not Path(DOCKER_SOCKET).exists():
        return False
    try:
        status, _ = _docker_request("GET", f"{DOCKER_API}/version", timeout=5)
        return status == 200
    except Exception:
        return False


def is_docker_managed() -> bool:
    """True when running in Docker AND the daemon socket is reachable.

    Only in this state can the app perform a real one-click self-update.
    """
    return is_docker_environment() and docker_daemon_available()


def _current_container_id() -> str | None:
    short = os.environ.get("HOSTNAME", "").strip()
    if not short:
        return None
    try:
        status, raw = _docker_request("GET", f"{DOCKER_API}/containers/json?all=true")
        if status != 200:
            return None
        containers = json.loads(raw.decode("utf-8"))
        for c in containers or []:
            cid = c.get("Id", "")
            if cid.startswith(short):
                return cid
    except Exception:
        logger.warning("Could not resolve current container id", exc_info=True)
    return None


def _image_name() -> str:
    return os.environ.get("INBOX_LENS_IMAGE", DEFAULT_IMAGE)


# --------------------------------------------------------------------------- #
# Update execution
# --------------------------------------------------------------------------- #
_update_state: dict = {"phase": "idle", "message": "", "started_at": 0.0}
_state_lock = threading.Lock()

_PHASES_ACTIVE = {"pulling", "recreating", "restarting"}


def update_state() -> dict:
    with _state_lock:
        return dict(_update_state)


def update_in_progress() -> bool:
    with _state_lock:
        return _update_state["phase"] in _PHASES_ACTIVE


def _set_state(phase: str, message: str = "") -> None:
    with _state_lock:
        _update_state["phase"] = phase
        _update_state["message"] = message
        if phase in _PHASES_ACTIVE:
            _update_state["started_at"] = time.time()


def trigger_update() -> bool:
    """Kick off an asynchronous update. Returns False if one is already running."""
    with _state_lock:
        if _update_state["phase"] in _PHASES_ACTIVE:
            return False
        _update_state["phase"] = "pulling"
        _update_state["message"] = "Starting update..."
        _update_state["started_at"] = time.time()
    t = threading.Thread(target=_update_worker, daemon=True)
    t.start()
    return True


def _update_worker() -> None:
    try:
        result = _perform_update_sync()
        if result["ok"]:
            _set_state("restarting", result["message"])
        else:
            _set_state("failed", result["message"])
    except Exception as exc:
        logger.warning("Update worker crashed", exc_info=True)
        _set_state("failed", f"Update failed: {exc}")


def _perform_update_sync() -> dict:
    if not is_docker_managed():
        return {
            "ok": False,
            "message": "Docker socket is unavailable. Run the update from the host.",
        }

    image = _image_name()
    cid = _current_container_id()
    if not cid:
        return {"ok": False, "message": "Could not identify the running container."}

    repo, _, tag = image.partition(":")
    pull_qs = urllib.parse.urlencode({"fromImage": repo, "tag": tag or "latest"})

    _set_state("pulling", f"Pulling {image}...")
    try:
        status, _ = _docker_request("POST", f"{DOCKER_API}/images/create?{pull_qs}", timeout=UPDATE_TIMEOUT)
        if status not in (200, 204):
            return {"ok": False, "message": f"Image pull failed (HTTP {status})."}
    except Exception:
        logger.warning("Image pull failed", exc_info=True)
        return {"ok": False, "message": "Image pull failed."}

    _set_state("recreating", "Preparing the replacement container...")
    try:
        status, raw = _docker_request("GET", f"{DOCKER_API}/containers/{cid}/json")
        if status != 200:
            return {"ok": False, "message": "Could not inspect the running container."}
        info = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": False, "message": "Could not inspect the running container."}

    name = (info.get("Name") or "").lstrip("/")
    config = info.get("Config") or {}
    create_body: dict = {"Image": image}
    for key in ("Cmd", "Entrypoint", "Env", "WorkingDir", "Labels", "User", "Tty", "OpenStdin"):
        if key in config:
            create_body[key] = config[key]
    create_body["HostConfig"] = info.get("HostConfig") or {}
    networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
    if networks:
        create_body["NetworkingConfig"] = {"EndpointsConfig": networks}

    create_qs = f"?name={urllib.parse.quote(name + '-new')}" if name else ""
    try:
        status, raw = _docker_request("POST", f"{DOCKER_API}/containers/create{create_qs}", body=create_body)
        if status not in (200, 201):
            return {"ok": False, "message": f"Could not create the replacement container (HTTP {status})."}
        new_cid = (json.loads(raw.decode("utf-8")) or {}).get("Id")
        if not new_cid:
            return {"ok": False, "message": "Replacement container created without an id."}
    except Exception:
        return {"ok": False, "message": "Could not create the replacement container."}

    # Swap: stop old -> rename old to backup -> rename new into place -> start new.
    try:
        _docker_request("POST", f"{DOCKER_API}/containers/{cid}/stop?t=10")
        if name:
            _docker_request(
                "POST",
                f"{DOCKER_API}/containers/{cid}/rename?name={urllib.parse.quote(name + '-old')}",
            )
            _docker_request(
                "POST",
                f"{DOCKER_API}/containers/{new_cid}/rename?name={urllib.parse.quote(name)}",
            )
        _docker_request("POST", f"{DOCKER_API}/containers/{new_cid}/start")
    except Exception:
        logger.warning("Container swap failed; original preserved as backup", exc_info=True)
        return {
            "ok": False,
            "message": "Update partially failed. The original container was preserved as a backup.",
        }

    # Best-effort cleanup of the old container now that the new one is up.
    try:
        _docker_request("DELETE", f"{DOCKER_API}/containers/{cid}?force=true")
    except Exception:
        pass

    return {"ok": True, "message": "Update applied. The app is restarting with the new version."}


# --------------------------------------------------------------------------- #
# Background checker
# --------------------------------------------------------------------------- #
class UpdateChecker:
    """Periodically checks for updates on a background thread."""

    def __init__(self, on_update=None, interval: int = CHECK_INTERVAL):
        self.on_update = on_update
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("UpdateChecker started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("UpdateChecker stopped")

    def check_now(self) -> dict | None:
        try:
            result = check_for_update(force=True)
        except Exception:
            logger.warning("Manual update check failed", exc_info=True)
            return None
        if result.get("update_available") and self.on_update:
            try:
                self.on_update(result)
            except Exception:
                logger.warning("on_update callback failed", exc_info=True)
        return result

    def _run(self) -> None:
        # Stagger the initial check so it doesn't race startup.
        if self._stop.wait(30):
            return
        self._check_once()
        while not self._stop.wait(self.interval):
            self._check_once()

    def _check_once(self) -> None:
        try:
            result = check_for_update()
            if result.get("update_available") and self.on_update:
                self.on_update(result)
        except Exception:
            logger.warning("Background update check failed", exc_info=True)
