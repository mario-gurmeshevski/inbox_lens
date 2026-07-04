import asyncio
import json
import os
import socket
import threading
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from markupsafe import Markup, escape
import markdown
import nh3
from datetime import datetime
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from src.scripts import email_reader, cache, idle_monitor, event_bus, updater
from src.scripts import auth
from src.scripts.constants import DB_PATH
from src.scripts.utils import _priority_bucket

load_dotenv()
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_MAX_AGE = int(os.getenv("SESSION_COOKIE_MAX_AGE", "2592000"))

_monitor: idle_monitor.IdleMonitor | None = None
_update_checker: updater.UpdateChecker | None = None


def _on_update_available(result: dict) -> None:
    try:
        event_bus.bus.publish("update_available", result)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor, _update_checker
    cache._ensure_secret_key()
    cache._ensure_session_key()
    cache.init_db(DB_PATH)
    email_reader.load_keywords(DB_PATH)

    if cache.has_email_credentials(DB_PATH):
        _monitor = idle_monitor.IdleMonitor(
            db_path=DB_PATH,
            on_refresh=lambda: event_bus.bus.publish("refresh"),
        )
        t = threading.Thread(
            target=idle_monitor.run_initial_fetch,
            kwargs={"db_path": DB_PATH, "on_refresh": lambda: event_bus.bus.publish("refresh")},
            daemon=True,
        )
        t.start()
        _monitor.start()

    if updater.is_docker_environment():
        if updater.cleanup_stale_containers():
            cache.save_setting(updater.LAST_UPDATE_ROLLED_BACK_KEY, "1", DB_PATH)
        _update_checker = updater.UpdateChecker(on_update=_on_update_available)
        _update_checker.start()

    yield

    if _update_checker:
        _update_checker.stop()
    if _monitor:
        _monitor.stop()


app = FastAPI(title="Email Reader Dashboard", lifespan=lifespan)

MANUAL_CHECK_COOLDOWN = 30

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
templates.env.filters["format_date"] = lambda d: _format_date(d)
templates.env.filters["format_sender"] = lambda raw: _format_sender(raw)
templates.env.filters["priority_bucket"] = lambda lvl: _priority_bucket(lvl) or "none"
templates.env.filters["markdown"] = lambda t: _render_markdown(t)
templates.env.globals["current_theme"] = lambda: _current_theme()
app.mount("/static/js", StaticFiles(directory=str(_WEB_DIR / "js")), name="js")
app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")


class AuthMiddleware(BaseHTTPMiddleware):
    _PUBLIC_PATHS = ("/login", "/static", "/health")
    _UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self._PUBLIC_PATHS):
            return await call_next(request)

        if not auth.is_auth_configured(DB_PATH):
            return await call_next(request)

        if auth.is_logged_in(request):
            if request.method in self._UNSAFE_METHODS and not auth.origin_ok(request):
                return JSONResponse({"detail": "Origin verification failed."}, status_code=403)
            if path == "/setup-dashboard":
                return RedirectResponse("/", status_code=303)
            return await call_next(request)

        api_key = auth.get_api_key_from_request(request)
        if api_key and auth.is_valid_api_key(api_key, DB_PATH):
            return await call_next(request)

        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            target = path + (("?" + request.url.query) if request.url.query else "")
            return RedirectResponse(f"/login?next={target}", status_code=303)
        return JSONResponse({"detail": "Not authenticated."}, status_code=401)


class SetupGuardMiddleware(BaseHTTPMiddleware):
    _EXEMPT_PATHS = {
        "/setup",
        "/setup-dashboard",
        "/login",
        "/static",
        "/health",
        "/api/events",
        "/partials/tailscale-status",
    }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self._EXEMPT_PATHS):
            return await call_next(request)
        if not cache.has_email_credentials(DB_PATH):
            target = "/setup-dashboard" if not auth.is_auth_configured(DB_PATH) else "/setup"
            return RedirectResponse(target, status_code=303)
        return await call_next(request)


app.add_middleware(SetupGuardMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=cache._ensure_session_key(),
    session_cookie="inbox_lens_session",
    same_site="lax",
    https_only=SESSION_COOKIE_SECURE,
    max_age=SESSION_COOKIE_MAX_AGE,
)


_GEOGRAPHIC_AREAS = frozenset(
    {
        "Africa",
        "America",
        "Antarctica",
        "Arctic",
        "Asia",
        "Atlantic",
        "Australia",
        "Europe",
        "Indian",
        "Pacific",
    }
)


def _is_geographic_zone(tz_id: str) -> bool:
    return tz_id.split("/", 1)[0] in _GEOGRAPHIC_AREAS


def _build_timezone_groups():
    zones = []
    for tz_id in sorted(available_timezones()):
        if not _is_geographic_zone(tz_id):
            continue
        try:
            now = datetime.now(ZoneInfo(tz_id))
            offset = now.utcoffset()
            if offset is None:
                continue
            total_minutes = int(offset.total_seconds() / 60)
            sign = "+" if total_minutes >= 0 else "-"
            hours, mins = divmod(abs(total_minutes), 60)
            offset_str = f"UTC{sign}{hours:02d}:{mins:02d}"
            display = f"{tz_id} ({offset_str})"
            zones.append((total_minutes, offset_str, tz_id, display))
        except Exception:
            continue

    zones.sort(key=lambda z: (z[0], z[2]))

    groups: list[tuple[str, list[tuple[str, str]]]] = []
    current_offset: str | None = None
    current_members: list[tuple[str, str]] = []
    for _minutes, offset_str, tz_id, display in zones:
        if offset_str != current_offset:
            if current_members:
                groups.append((current_offset, current_members))
            current_offset = offset_str
            current_members = []
        current_members.append((tz_id, display))
    if current_members:
        groups.append((current_offset, current_members))
    return groups


_TIMEZONE_GROUPS = _build_timezone_groups()


def _flat_timezone_ids():
    for _offset_label, members in _TIMEZONE_GROUPS:
        for tz_id, _display in members:
            yield tz_id


def _detect_local_timezone() -> str:
    candidates: list[str] = []
    try:
        with open("/etc/timezone", encoding="utf-8") as fh:
            candidates.append(fh.read().strip())
    except OSError:
        pass
    try:
        resolved = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        idx = resolved.find(marker)
        if idx != -1:
            candidates.append(resolved[idx + len(marker) :])
    except OSError:
        pass
    for candidate in candidates:
        if not candidate:
            continue
        try:
            ZoneInfo(candidate)
            return candidate
        except Exception:
            continue
    return "UTC"


_LOCAL_TIMEZONE = _detect_local_timezone()
_DATE_FORMATS = {
    "default": "%a, %d %b %Y %H:%M",
    "iso": "%Y-%m-%d %H:%M",
    "us_12hr": "%b %d, %Y %I:%M %p",
}
_SAMPLE_DT = datetime(2026, 6, 29, 14, 30)
_DATE_FORMAT_OPTIONS = [(key, _SAMPLE_DT.strftime(pattern)) for key, pattern in _DATE_FORMATS.items()]
_SENDER_MODES = {"name", "email", "both"}
_PAGE_SIZE_VALUES = (10, 25, 50, 100)
_DEFAULT_PAGE_SIZE = 25


def _get_page_size(db_path: str) -> int:
    raw = cache.get_setting("page_size", db_path)
    try:
        val = int(raw) if raw else _DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        val = _DEFAULT_PAGE_SIZE
    return val if val in _PAGE_SIZE_VALUES else _DEFAULT_PAGE_SIZE


_THEMES = {"system", "light", "dark"}


def _current_theme() -> str:
    theme = cache.get_setting("theme", DB_PATH)
    return theme if theme in _THEMES else "system"


def _format_date(date_str):
    if not date_str:
        return ""
    try:
        dt = parsedate_to_datetime(date_str)
        tz_name = cache.get_setting("timezone", DB_PATH) or _LOCAL_TIMEZONE
        dt = dt.astimezone(ZoneInfo(tz_name))
        fmt_key = cache.get_setting("date_format", DB_PATH) or "default"
        fmt = _DATE_FORMATS.get(fmt_key, _DATE_FORMATS["default"])
        return dt.strftime(fmt)
    except Exception:
        return date_str


def _format_sender(raw, mode=None):
    if mode is None:
        mode = cache.get_setting("sender_display", DB_PATH) or "both"
    if mode not in _SENDER_MODES:
        mode = "both"
    if not raw:
        return Markup("")
    name, email_addr = parseaddr(str(raw))
    name = (name or "").strip()
    email_addr = (email_addr or "").strip()
    if mode == "name":
        return escape(name or email_addr or str(raw))
    if mode == "email":
        return escape(email_addr or name or str(raw))
    parts = []
    if name:
        parts.append(f'<span class="sender-name">{escape(name)}</span>')
    if email_addr:
        parts.append(f'<span class="sender-email">{escape(email_addr)}</span>')
    if not parts:
        return escape(str(raw))
    return Markup("".join(parts))


_MD_EXTENSIONS = ["fenced_code", "tables", "nl2br", "sane_lists"]

_MD_ALLOWED_TAGS = {
    "p",
    "br",
    "hr",
    "span",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "strong",
    "b",
    "em",
    "i",
    "del",
    "s",
    "sub",
    "sup",
    "mark",
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    "blockquote",
    "code",
    "pre",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}

_MD_ALLOWED_ATTRS = {
    "*": {"class"},
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"align"},
    "td": {"align"},
}

_MD_URL_SCHEMES = {"http", "https", "mailto", "tel"}


def _render_markdown(text):
    if not text:
        return Markup("")
    html_body = markdown.markdown(text, extensions=_MD_EXTENSIONS, output_format="html")
    cleaned = nh3.clean(
        html_body,
        tags=_MD_ALLOWED_TAGS,
        attributes=_MD_ALLOWED_ATTRS,
        url_schemes=_MD_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        set_tag_attribute_values={"a": {"target": "_blank"}},
    )
    return Markup(cleaned)


def _is_docker() -> bool:
    return Path("/.dockerenv").exists()


def _get_local_ips() -> list[str]:
    if _is_docker():
        return []
    ips: set[str] = set()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ips.add(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass

    # Secondary source: hostname resolution surfaces additional interfaces
    try:
        _, _, resolved = socket.gethostbyname_ex(socket.gethostname())
        ips.update(resolved)
    except OSError:
        pass

    return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))


TS_STATUS_FILE = "/shared/status.json"
TS_LOGIN_URL_FILE = "/shared/login_url.txt"
TS_SERVE_DONE_FILE = "/shared/serve_done"


def _is_tailscale_mode() -> bool:
    return Path("/shared").is_dir()


def _tailscale_info():
    if not _is_tailscale_mode():
        return None
    try:
        with open(TS_STATUS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _get_tailscale_ip() -> str:
    info = _tailscale_info()
    if not info or info.get("BackendState") != "Running":
        return ""
    self_node = info.get("Self") or {}
    return next((ip for ip in self_node.get("TailscaleIPs", []) if ip.startswith("100.")), "")


def _get_tailscale_dns_name() -> str:
    info = _tailscale_info()
    if not info or info.get("BackendState") != "Running":
        return ""
    self_node = info.get("Self") or {}
    return self_node.get("DNSName", "").rstrip(".")


def _get_tailscale_login_url() -> str:
    if not _is_tailscale_mode():
        return ""
    try:
        return Path(TS_LOGIN_URL_FILE).read_text().strip()
    except (FileNotFoundError, OSError):
        return ""


def _get_tailscale_serve_url() -> str:
    if not _is_tailscale_mode():
        return ""
    dns_name = _get_tailscale_dns_name()
    if not dns_name or not Path(TS_SERVE_DONE_FILE).exists():
        return ""
    return f"https://{dns_name}"


def _tailscale_state(tailscale_ip: str, tailscale_login_url: str) -> str:
    if tailscale_ip:
        return "running"
    if tailscale_login_url:
        return "needs_login"
    if _is_tailscale_mode():
        return "starting"
    return "none"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if cache.has_email_credentials(DB_PATH):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "error": None,
            "imap_server": IMAP_SERVER,
            "email_user": "",
        },
    )


@app.post("/setup", response_class=HTMLResponse)
async def setup_submit(request: Request):
    global _monitor

    if cache.has_email_credentials(DB_PATH):
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    imap_server = str(form.get("imap_server", "")).strip() or IMAP_SERVER
    email_user = str(form.get("email_user", "")).strip()
    email_pass = str(form.get("email_pass", "")).strip()

    if not email_user or not email_pass:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "error": "Email and password are required.",
                "imap_server": imap_server,
                "email_user": email_user,
            },
        )

    result = email_reader.test_connection(imap_server, email_user, email_pass)
    if not result["success"]:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "error": result["error"],
                "imap_server": imap_server,
                "email_user": email_user,
            },
        )

    cache.init_db(DB_PATH)
    cache.save_setting("imap_server", imap_server, DB_PATH)
    cache.save_email_credentials(email_user, email_pass, DB_PATH)

    t = threading.Thread(
        target=idle_monitor.run_initial_fetch,
        kwargs={"db_path": DB_PATH, "on_refresh": lambda: event_bus.bus.publish("refresh")},
        daemon=True,
    )
    t.start()

    _monitor = idle_monitor.IdleMonitor(
        db_path=DB_PATH,
        on_refresh=lambda: event_bus.bus.publish("refresh"),
    )
    _monitor.start()

    return RedirectResponse("/", status_code=303)


def _safe_next(next_url: str) -> str:
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


@app.get("/setup-dashboard", response_class=HTMLResponse)
async def setup_dashboard_page(request: Request):
    if auth.is_auth_configured(DB_PATH):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "setup_dashboard.html",
        {
            "error": None,
            "generated_api_key": None,
        },
    )


@app.post("/setup-dashboard", response_class=HTMLResponse)
async def setup_dashboard_submit(request: Request):
    if auth.is_auth_configured(DB_PATH):
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm", ""))
    gen_key = str(form.get("generate_api_key", "")).lower() in ("on", "true", "1")

    error = auth.validate_password(password)
    if not error and password != confirm:
        error = "Passwords do not match."
    if error:
        return templates.TemplateResponse(
            request,
            "setup_dashboard.html",
            {
                "error": error,
                "generated_api_key": None,
            },
        )

    auth.set_password(password, DB_PATH)
    auth.mark_logged_in(request)

    generated_api_key = None
    if gen_key:
        generated_api_key = auth.generate_api_key()
        auth.save_api_key(generated_api_key, DB_PATH)

    if generated_api_key:
        return templates.TemplateResponse(
            request,
            "setup_dashboard.html",
            {
                "error": None,
                "generated_api_key": generated_api_key,
            },
        )
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if auth.is_auth_configured(DB_PATH) and auth.is_logged_in(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": None,
            "next": _safe_next(request.query_params.get("next", "/")),
        },
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    if not auth.is_auth_configured(DB_PATH):
        return RedirectResponse("/setup-dashboard", status_code=303)

    form = await request.form()
    password = str(form.get("password", ""))
    next_url = _safe_next(str(form.get("next", "/")))
    ip = auth._client_ip(request)

    if auth.login_rate_limiter.is_limited(ip):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Too many login attempts. Please try again later.",
                "next": next_url,
            },
        )

    if auth.verify_password(password, DB_PATH):
        auth.login_rate_limiter.reset(ip)
        auth.mark_logged_in(request)
        return RedirectResponse(next_url, status_code=303)

    auth.login_rate_limiter.record_failure(ip)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": "Incorrect password.",
            "next": next_url,
        },
    )


@app.post("/logout")
async def logout_route(request: Request):
    auth.logout(request)
    return RedirectResponse("/login", status_code=303)


def _account_context(db_path: str) -> dict:
    email_user, email_pass = cache.get_email_credentials(db_path)
    saved_imap = cache.get_setting("imap_server", db_path) or IMAP_SERVER
    masked_pass = (email_pass[:4] + "*" * (len(email_pass) - 4)) if email_pass and len(email_pass) > 4 else "****"
    return {
        "email_user": email_user,
        "imap_server": saved_imap,
        "masked_pass": masked_pass,
    }


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    if not cache.has_email_credentials(DB_PATH):
        return RedirectResponse("/setup-dashboard", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/partials/account", response_class=HTMLResponse)
async def partial_account(request: Request):
    if not cache.has_email_credentials(DB_PATH):
        return RedirectResponse("/setup-dashboard", status_code=303)
    return templates.TemplateResponse(request, "partials/account.html", _account_context(DB_PATH))


@app.post("/account/disconnect")
async def account_disconnect(request: Request):
    global _monitor
    if _monitor:
        _monitor.stop()
        _monitor = None
    cache.delete_email_credentials(DB_PATH)
    cache.delete_setting("imap_server", DB_PATH)
    cache.clear_emails(DB_PATH)
    return RedirectResponse("/setup", status_code=303)


def _update_info(db_path: str = DB_PATH) -> dict:
    check = updater.check_for_update()
    latest = check.get("latest")
    dismissed = cache.get_setting(updater.DISMISSED_VERSION_KEY, db_path)
    banner_dismissed = bool(latest and dismissed == latest)
    return {
        "current_version": check.get("current") or "Unknown",
        "latest_version": latest,
        "update_available": check.get("update_available", False),
        "update_error": check.get("error", False),
        "update_message": check.get("message", ""),
        "docker_managed": updater.is_docker_managed(),
        "update_state": updater.update_state(),
        "update_rolled_back": bool(cache.get_setting(updater.LAST_UPDATE_ROLLED_BACK_KEY, db_path)),
        "banner_dismissed": banner_dismissed,
    }


def _settings_context(
    db_path: str,
    restart_notice: bool = False,
    password_msg: str | None = None,
    password_ok: bool = False,
    api_key_msg: str | None = None,
    new_api_key: str | None = None,
) -> dict:
    network_access = cache.get_setting("network_access", db_path) or "true"
    network_on = network_access == "true"
    timezone_setting = cache.get_setting("timezone", db_path) or _LOCAL_TIMEZONE
    date_format = cache.get_setting("date_format", db_path) or "default"
    if date_format not in _DATE_FORMATS:
        date_format = "default"
    sender_display = cache.get_setting("sender_display", db_path) or "both"
    if sender_display not in _SENDER_MODES:
        sender_display = "both"
    page_size = _get_page_size(db_path)
    theme = _current_theme()
    local_ips = _get_local_ips() if network_on else []
    host_ip = os.getenv("HOST_IP", "")
    tailscale_ip = _get_tailscale_ip()
    tailscale_login_url = _get_tailscale_login_url()
    ts_state = _tailscale_state(tailscale_ip, tailscale_login_url)
    port = int(os.getenv("WEB_PORT", "8000"))
    return {
        "network_access": network_on,
        "local_ips": local_ips,
        "host_ip": host_ip,
        "tailscale_ip": tailscale_ip,
        "tailscale_dns_name": _get_tailscale_dns_name(),
        "tailscale_serve_url": _get_tailscale_serve_url(),
        "tailscale_login_url": tailscale_login_url,
        "ts_state": ts_state,
        "is_docker": _is_docker(),
        "port": port,
        "restart_notice": restart_notice,
        "auth_enabled": auth.is_auth_configured(db_path),
        "api_key_created_at": auth.get_api_key_created_at(db_path),
        "password_msg": password_msg,
        "password_ok": password_ok,
        "api_key_msg": api_key_msg,
        "new_api_key": new_api_key,
        "update": _update_info(db_path),
        "timezone": timezone_setting,
        "timezone_groups": _TIMEZONE_GROUPS,
        "date_format": date_format,
        "date_format_options": _DATE_FORMAT_OPTIONS,
        "sender_display": sender_display,
        "page_size": page_size,
        "page_size_options": _PAGE_SIZE_VALUES,
        "theme": theme,
    }


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", _settings_context(DB_PATH))


@app.post("/settings/network-access", response_class=HTMLResponse)
async def settings_network_access(request: Request):
    form = await request.form()
    enabled = str(form.get("enabled", "false")).strip()
    cache.save_setting("network_access", enabled, DB_PATH)
    return templates.TemplateResponse(request, "settings.html", _settings_context(DB_PATH, restart_notice=True))


@app.post("/settings/password", response_class=HTMLResponse)
async def settings_password(request: Request):
    form = await request.form()
    old = str(form.get("old_password", ""))
    new = str(form.get("new_password", ""))
    confirm = str(form.get("confirm_password", ""))
    msg: str | None = None
    ok = False
    verr = auth.validate_password(new)
    if verr:
        msg = verr
    elif new != confirm:
        msg = "New passwords do not match."
    elif not auth.change_password(old, new, DB_PATH):
        msg = "Current password is incorrect."
    else:
        ok = True
        msg = "Password updated."
    return templates.TemplateResponse(
        request, "settings.html", _settings_context(DB_PATH, password_msg=msg, password_ok=ok)
    )


@app.post("/settings/api-key/regenerate", response_class=HTMLResponse)
async def settings_api_key_regenerate(request: Request):
    token = auth.generate_api_key()
    auth.save_api_key(token, DB_PATH)
    return templates.TemplateResponse(
        request,
        "settings.html",
        _settings_context(
            DB_PATH, api_key_msg="API key regenerated. Copy it now — it won't be shown again.", new_api_key=token
        ),
    )


@app.post("/settings/api-key/revoke", response_class=HTMLResponse)
async def settings_api_key_revoke(request: Request):
    auth.revoke_api_key(DB_PATH)
    return templates.TemplateResponse(
        request, "settings.html", _settings_context(DB_PATH, api_key_msg="API key revoked.")
    )


@app.post("/settings/timezone", response_class=HTMLResponse)
async def settings_timezone(request: Request):
    form = await request.form()
    tz = str(form.get("timezone", "UTC")).strip()
    valid_tzs = {tz_id for tz_id in _flat_timezone_ids()}
    if tz not in valid_tzs:
        tz = _LOCAL_TIMEZONE
    cache.save_setting("timezone", tz, DB_PATH)
    return Response(status_code=204, headers={"X-Toast": "Updated"})


@app.post("/settings/preferences", response_class=HTMLResponse)
async def settings_preferences(request: Request):
    form = await request.form()
    date_format = str(form.get("date_format", "default")).strip()
    sender_display = str(form.get("sender_display", "both")).strip()
    if date_format not in _DATE_FORMATS:
        date_format = "default"
    if sender_display not in _SENDER_MODES:
        sender_display = "both"
    cache.save_setting("date_format", date_format, DB_PATH)
    cache.save_setting("sender_display", sender_display, DB_PATH)
    return Response(status_code=204, headers={"X-Toast": "Updated"})


@app.post("/settings/page-size", response_class=HTMLResponse)
async def settings_page_size(request: Request):
    form = await request.form()
    raw = str(form.get("page_size", str(_DEFAULT_PAGE_SIZE))).strip()
    try:
        val = int(raw)
    except ValueError:
        val = _DEFAULT_PAGE_SIZE
    if val not in _PAGE_SIZE_VALUES:
        val = _DEFAULT_PAGE_SIZE
    cache.save_setting("page_size", str(val), DB_PATH)
    return Response(status_code=204, headers={"X-Toast": "Updated"})


@app.post("/settings/theme", response_class=HTMLResponse)
async def settings_theme(request: Request):
    form = await request.form()
    theme = str(form.get("theme", "system")).strip()
    if theme not in _THEMES:
        theme = "system"
    cache.save_setting("theme", theme, DB_PATH)
    return Response(status_code=204, headers={"X-Toast": "Updated"})


def _level_sort_key(level):
    try:
        return (0, int(level))
    except (ValueError, TypeError):
        return (1, str(level))


def _keywords_context(db_path: str, msg: str | None = None, msg_ok: bool = False) -> dict:
    categories = email_reader.load_keywords(db_path)
    levels = sorted(categories.keys(), key=_level_sort_key, reverse=True)
    return {
        "categories": categories,
        "levels": levels,
        "kw_msg": msg,
        "kw_msg_ok": msg_ok,
    }


def _keywords_partial(request: Request, db_path: str, msg: str | None = None, msg_ok: bool = False):
    return templates.TemplateResponse(
        request, "partials/keywords.html", _keywords_context(db_path, msg=msg, msg_ok=msg_ok)
    )


@app.get("/keywords", response_class=HTMLResponse)
async def keywords_page(request: Request):
    import_status = request.query_params.get("import")
    msg: str | None = None
    msg_ok = False
    if import_status == "ok":
        msg = "Keywords imported successfully."
        msg_ok = True
    elif import_status == "invalid":
        msg = 'Import failed: file must be valid JSON shaped as {"categories": {"level": ["words"]}}.'
    elif import_status == "error":
        msg = "Import failed: no file selected."
    return templates.TemplateResponse(request, "keywords.html", _keywords_context(DB_PATH, msg=msg, msg_ok=msg_ok))


@app.post("/keywords/word/add", response_class=HTMLResponse)
async def keywords_word_add(request: Request):
    form = await request.form()
    level = str(form.get("level", "")).strip()
    word = str(form.get("word", "")).strip()
    categories = email_reader.load_keywords(DB_PATH)
    msg, ok = None, False
    if not word:
        msg = "Word cannot be empty."
    elif word.lower() in {w.lower() for w in categories.get(level, [])}:
        msg = f"“{word}” already exists in priority {level}."
    else:
        categories.setdefault(level, []).append(word)
        try:
            email_reader.save_keywords(categories, DB_PATH)
            ok = True
        except ValueError as e:
            msg = str(e)
    return _keywords_partial(request, DB_PATH, msg=msg, msg_ok=ok)


@app.post("/keywords/word/edit", response_class=HTMLResponse)
async def keywords_word_edit(request: Request):
    form = await request.form()
    level = str(form.get("level", "")).strip()
    old_word = str(form.get("old_word", "")).strip()
    new_word = str(form.get("new_word", "")).strip()
    categories = email_reader.load_keywords(DB_PATH)
    msg, ok = None, False
    words = categories.get(level, [])
    if not new_word:
        msg = "Word cannot be empty."
    elif old_word not in words:
        msg = "That word no longer exists."
    elif new_word.lower() in {w.lower() for w in words if w != old_word}:
        msg = f"“{new_word}” already exists in priority {level}."
    else:
        categories[level] = [new_word if w == old_word else w for w in words]
        try:
            email_reader.save_keywords(categories, DB_PATH)
            ok = True
        except ValueError as e:
            msg = str(e)
    return _keywords_partial(request, DB_PATH, msg=msg, msg_ok=ok)


@app.post("/keywords/word/remove", response_class=HTMLResponse)
async def keywords_word_remove(request: Request):
    form = await request.form()
    level = str(form.get("level", "")).strip()
    word = str(form.get("word", "")).strip()
    categories = email_reader.load_keywords(DB_PATH)
    words = categories.get(level, [])
    msg, ok = None, False
    if word in words:
        categories[level] = [w for w in words if w != word]
        try:
            email_reader.save_keywords(categories, DB_PATH)
            ok = True
        except ValueError as e:
            msg = str(e)
    return _keywords_partial(request, DB_PATH, msg=msg, msg_ok=ok)


@app.post("/keywords/category/add", response_class=HTMLResponse)
async def keywords_category_add(request: Request):
    form = await request.form()
    level = str(form.get("level", "")).strip()
    categories = email_reader.load_keywords(DB_PATH)
    msg, ok = None, False
    try:
        n = int(level)
    except ValueError:
        msg = "Priority level must be an integer."
        return _keywords_partial(request, DB_PATH, msg=msg, msg_ok=ok)
    if n < 1:
        msg = "Priority level must be >= 1."
    elif str(n) in categories:
        msg = f"Priority {n} already exists."
    else:
        categories[str(n)] = []
        try:
            email_reader.save_keywords(categories, DB_PATH)
            ok = True
        except ValueError as e:
            msg = str(e)
    return _keywords_partial(request, DB_PATH, msg=msg, msg_ok=ok)


@app.post("/keywords/category/remove", response_class=HTMLResponse)
async def keywords_category_remove(request: Request):
    form = await request.form()
    level = str(form.get("level", "")).strip()
    categories = email_reader.load_keywords(DB_PATH)
    msg, ok = None, False
    if level in categories:
        del categories[level]
        try:
            email_reader.save_keywords(categories, DB_PATH)
            ok = True
        except ValueError as e:
            msg = str(e)
    return _keywords_partial(request, DB_PATH, msg=msg, msg_ok=ok)


@app.get("/keywords/export")
async def keywords_export(request: Request):
    categories = email_reader.load_keywords(DB_PATH)
    payload = json.dumps({"categories": categories}, ensure_ascii=False, indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="keywords.json"'},
    )


@app.post("/keywords/import")
async def keywords_import(request: Request):
    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return RedirectResponse("/keywords?import=error", status_code=303)
    try:
        data = json.loads(await upload.read())
    except Exception:
        return RedirectResponse("/keywords?import=invalid", status_code=303)
    categories = data.get("categories", data) if isinstance(data, dict) else None
    try:
        email_reader.save_keywords(categories, DB_PATH)
    except (ValueError, TypeError):
        return RedirectResponse("/keywords?import=invalid", status_code=303)
    return RedirectResponse("/keywords?import=ok", status_code=303)


@app.post("/keywords/rescan", response_class=HTMLResponse)
async def keywords_rescan(request: Request):
    categories = email_reader.load_keywords(DB_PATH)
    compiled = email_reader.build_compiled_patterns(categories)
    result = cache.rescan_all(DB_PATH, compiled)
    scanned = result["scanned"]
    skipped = result.get("skipped", 0)
    msg = (
        f"Re-scanned {scanned} cached email(s). {skipped} without body skipped."
        if skipped
        else f"Re-scanned {scanned} cached email(s)."
    )
    return _keywords_partial(request, DB_PATH, msg=msg, msg_ok=True)


@app.get("/partials/update-banner", response_class=HTMLResponse)
async def partial_update_banner(request: Request):
    return templates.TemplateResponse(
        request, "partials/update_banner.html", {"is_docker": _is_docker(), "update": _update_info(DB_PATH)}
    )


@app.post("/api/update/dismiss", response_class=HTMLResponse)
async def api_update_dismiss(request: Request):
    latest = updater.fetch_latest_version()
    if latest:
        cache.save_setting(updater.DISMISSED_VERSION_KEY, latest, DB_PATH)
    return templates.TemplateResponse(
        request, "partials/update_banner.html", {"is_docker": _is_docker(), "update": _update_info(DB_PATH)}
    )


@app.get("/partials/update-panel", response_class=HTMLResponse)
async def partial_update_panel(request: Request):
    return templates.TemplateResponse(request, "partials/update_panel.html", {"update": _update_info(DB_PATH)})


@app.post("/api/update/check", response_class=HTMLResponse)
async def api_update_check(request: Request):
    now = time.time()
    last = request.session.get("last_update_check_at", 0.0)
    if now - last >= MANUAL_CHECK_COOLDOWN:
        updater.fetch_latest_version(force=True)
        request.session["last_update_check_at"] = now
    cache.delete_setting(updater.LAST_UPDATE_ROLLED_BACK_KEY, DB_PATH)
    return templates.TemplateResponse(request, "partials/update_panel.html", {"update": _update_info(DB_PATH)})


@app.post("/api/update/run", response_class=HTMLResponse)
async def api_update_run(request: Request):
    cache.delete_setting(updater.LAST_UPDATE_ROLLED_BACK_KEY, DB_PATH)
    if updater.is_docker_managed():
        updater.trigger_update()
    return templates.TemplateResponse(request, "partials/update_panel.html", {"update": _update_info(DB_PATH)})


@app.get("/api/update/status")
async def api_update_status():
    return _update_info(DB_PATH)


def _dashboard_context(db_path: str) -> dict:
    counts = cache.get_counts(db_path)
    total = counts["headers_only"] + counts["fetched"] + counts["checked"] + counts["fetched_no_body"]
    raw_priority = cache.get_priority_counts(db_path)
    priority_dist = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for level_str, count in raw_priority.items():
        bucket = _priority_bucket(level_str)
        if bucket:
            priority_dist[bucket] += count
    priority_dist["unclassified"] = total - sum(priority_dist.values())
    return {
        "counts": counts,
        "total": total,
        "unscanned": total - counts["checked"],
        "priority_dist": priority_dist,
        "recent_emails": cache.get_recent_emails(db_path, limit=10),
    }


def _email_list_context(db_path, status, priority, search, page, page_size=None) -> dict:
    if page_size is None:
        page_size = _get_page_size(db_path)
    emails, total_rows, total_pages = cache.search_emails(
        db_path,
        status=status or None,
        priority=priority or None,
        search=search or None,
        page=page,
        page_size=page_size,
    )
    return {
        "emails": emails,
        "status": status,
        "priority": priority,
        "search": search,
        "page": page,
        "total_pages": total_pages,
        "total_rows": total_rows,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ctx = _dashboard_context(DB_PATH)
    ctx["monitor_active"] = _monitor is not None and _monitor.running
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/emails", response_class=HTMLResponse)
async def email_list(
    request: Request,
    status: str = Query("", alias="status"),
    priority: str = Query("", alias="priority"),
    search: str = Query("", alias="search"),
    page: int = Query(1, alias="page"),
):
    return templates.TemplateResponse(
        request, "emails.html", _email_list_context(DB_PATH, status, priority, search, page)
    )


@app.get("/emails/{email_hash}", response_class=HTMLResponse)
async def email_detail(request: Request, email_hash: str):
    email_data = cache.get_email_by_hash(DB_PATH, email_hash)
    if not email_data:
        return HTMLResponse("<h1>Email not found</h1>", status_code=404)

    return templates.TemplateResponse(
        request,
        "email_detail.html",
        {
            "email": email_data,
            "email_hash": email_hash,
        },
    )


@app.post("/emails/{email_hash}/delete")
async def delete_email(request: Request, email_hash: str):
    email_data = cache.get_email_by_hash(DB_PATH, email_hash)

    if not email_data:
        return RedirectResponse("/emails", status_code=303)

    message_id = email_data.get("message_id", "")
    if message_id:
        email_reader.delete_email(message_id, db_path=DB_PATH)
    return RedirectResponse("/emails", status_code=303)


@app.get("/api/events")
async def sse_events(request: Request):
    q = event_bus.bus.subscribe()

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            event_bus.bus.unsubscribe(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/partials/dashboard", response_class=HTMLResponse)
async def partial_dashboard(request: Request):
    return templates.TemplateResponse(request, "partials/dashboard_content.html", _dashboard_context(DB_PATH))


@app.get("/partials/emails", response_class=HTMLResponse)
async def partial_emails(
    request: Request,
    status: str = Query("", alias="status"),
    priority: str = Query("", alias="priority"),
    search: str = Query("", alias="search"),
    page: int = Query(1, alias="page"),
):
    return templates.TemplateResponse(
        request, "partials/email_table.html", _email_list_context(DB_PATH, status, priority, search, page)
    )


@app.get("/partials/email-detail/{email_hash}", response_class=HTMLResponse)
async def partial_email_detail(request: Request, email_hash: str):
    email_data = cache.get_email_by_hash(DB_PATH, email_hash)
    if not email_data:
        return HTMLResponse("<p>Email not found</p>", status_code=404)

    return templates.TemplateResponse(
        request,
        "partials/email_detail_content.html",
        {
            "email": email_data,
            "email_hash": email_hash,
        },
    )


@app.get("/partials/tailscale-status", response_class=HTMLResponse)
async def partial_tailscale_status(request: Request):
    tailscale_ip = _get_tailscale_ip()
    tailscale_login_url = _get_tailscale_login_url()
    ts_state = _tailscale_state(tailscale_ip, tailscale_login_url)
    port = int(os.getenv("WEB_PORT", "8000"))
    return templates.TemplateResponse(
        request,
        "partials/tailscale_status.html",
        {
            "ts_state": ts_state,
            "tailscale_ip": tailscale_ip,
            "tailscale_dns_name": _get_tailscale_dns_name(),
            "tailscale_serve_url": _get_tailscale_serve_url(),
            "tailscale_login_url": tailscale_login_url,
            "port": port,
        },
    )


def _resolve_bind_host(requested: str) -> str:
    loopback = {"127.0.0.1", "localhost", "::1"}
    if requested in loopback:
        return requested
    try:
        cache.init_db(DB_PATH)
        if not auth.is_auth_configured(DB_PATH):
            print(f"[auth] No dashboard password configured — binding to 127.0.0.1 (requested {requested}).")
            print(
                "[auth] Set a password at http://127.0.0.1:8000/setup-dashboard, then restart to expose the dashboard."
            )
            return "127.0.0.1"
    except Exception as exc:
        print(f"[auth] Could not verify auth configuration ({exc!r}); using requested host {requested}.")
    return requested


if __name__ == "__main__":
    import uvicorn

    cache._ensure_secret_key()
    cache._ensure_session_key()
    host = _resolve_bind_host(WEB_HOST)
    uvicorn.run("src.scripts.web:app", host=host, port=WEB_PORT, reload=True)
