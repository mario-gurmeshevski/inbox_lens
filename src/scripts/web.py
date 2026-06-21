import asyncio
import json
import os
import socket
import threading
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from email.utils import parsedate_to_datetime
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from src.scripts import email_reader, cache, idle_monitor, event_bus
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor
    cache._ensure_secret_key()
    cache._ensure_session_key()
    cache.init_db(DB_PATH)

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

    yield

    if _monitor:
        _monitor.stop()


app = FastAPI(title="Email Reader Dashboard", lifespan=lifespan)

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
templates.env.filters["format_date"] = lambda d: _format_date(d)
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
        "/setup", "/setup-dashboard", "/login", "/static", "/health",
        "/api/events", "/partials/tailscale-status",
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


def _format_date(date_str):
    if not date_str:
        return ""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%a, %d %b %Y %H:%M")
    except Exception:
        return date_str


def _is_docker() -> bool:
    return Path("/.dockerenv").exists()


def _get_local_ips() -> list[str]:
    if _is_docker():
        return []
    ips: set[str] = set()

    # Primary outbound IP via a UDP socket. No packets are actually sent
    # (SOCK_DGRAM + connect only populates the routing table entry), but this
    # reliably returns the interface used to reach the network on macOS,
    # Linux, and Windows — unlike gethostbyname_ex, which fails on macOS
    # hostnames ending in ".local" and frequently misses non-primary ifs.
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
    # on hosts where it works (and is harmless when it doesn't).
    try:
        _, _, resolved = socket.gethostbyname_ex(socket.gethostname())
        ips.update(resolved)
    except OSError:
        pass

    return sorted(
        ip for ip in ips
        if not ip.startswith("127.") and not ip.startswith("169.254.")
    )


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
    return templates.TemplateResponse(request, "setup.html", {
        "error": None,
        "imap_server": IMAP_SERVER,
        "email_user": "",
    })


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
        return templates.TemplateResponse(request, "setup.html", {
            "error": "Email and password are required.",
            "imap_server": imap_server,
            "email_user": email_user,
        })

    result = email_reader.test_connection(imap_server, email_user, email_pass)
    if not result["success"]:
        return templates.TemplateResponse(request, "setup.html", {
            "error": result["error"],
            "imap_server": imap_server,
            "email_user": email_user,
        })

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
    return templates.TemplateResponse(request, "setup_dashboard.html", {
        "error": None,
        "generated_api_key": None,
    })


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
        return templates.TemplateResponse(request, "setup_dashboard.html", {
            "error": error,
            "generated_api_key": None,
        })

    auth.set_password(password, DB_PATH)
    auth.mark_logged_in(request)

    generated_api_key = None
    if gen_key:
        generated_api_key = auth.generate_api_key()
        auth.save_api_key(generated_api_key, DB_PATH)

    if generated_api_key:
        return templates.TemplateResponse(request, "setup_dashboard.html", {
            "error": None,
            "generated_api_key": generated_api_key,
        })
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if auth.is_auth_configured(DB_PATH) and auth.is_logged_in(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {
        "error": None,
        "next": _safe_next(request.query_params.get("next", "/")),
    })


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    if not auth.is_auth_configured(DB_PATH):
        return RedirectResponse("/setup-dashboard", status_code=303)

    form = await request.form()
    password = str(form.get("password", ""))
    next_url = _safe_next(str(form.get("next", "/")))
    ip = auth._client_ip(request)

    if auth.login_rate_limiter.is_limited(ip):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Too many login attempts. Please try again later.",
            "next": next_url,
        })

    if auth.verify_password(password, DB_PATH):
        auth.login_rate_limiter.reset(ip)
        auth.mark_logged_in(request)
        return RedirectResponse(next_url, status_code=303)

    auth.login_rate_limiter.record_failure(ip)
    return templates.TemplateResponse(request, "login.html", {
        "error": "Incorrect password.",
        "next": next_url,
    })


@app.post("/logout")
async def logout_route(request: Request):
    auth.logout(request)
    return RedirectResponse("/login", status_code=303)


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    email_user, email_pass = cache.get_email_credentials(DB_PATH)
    if not email_user:
        return RedirectResponse("/setup", status_code=303)
    saved_imap = cache.get_setting("imap_server", DB_PATH) or IMAP_SERVER
    masked_pass = (email_pass[:4] + "*" * (len(email_pass) - 4)) if email_pass and len(email_pass) > 4 else "****"
    return templates.TemplateResponse(request, "account.html", {
        "email_user": email_user,
        "imap_server": saved_imap,
        "masked_pass": masked_pass,
    })


@app.post("/account/disconnect")
async def account_disconnect(request: Request):
    global _monitor
    if _monitor:
        _monitor.stop()
        _monitor = None
    cache.delete_email_credentials(DB_PATH)
    cache.delete_setting("imap_server", DB_PATH)
    return RedirectResponse("/setup", status_code=303)


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
        _settings_context(DB_PATH, api_key_msg="API key regenerated. Copy it now — it won't be shown again.", new_api_key=token),
    )


@app.post("/settings/api-key/revoke", response_class=HTMLResponse)
async def settings_api_key_revoke(request: Request):
    auth.revoke_api_key(DB_PATH)
    return templates.TemplateResponse(
        request, "settings.html", _settings_context(DB_PATH, api_key_msg="API key revoked.")
    )


def _dashboard_context(db_path: str) -> dict:
    counts = cache.get_counts(db_path)
    total = counts["headers_only"] + counts["fetched"] + counts["checked"]
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


def _email_list_context(db_path, status, priority, search, page, page_size=25) -> dict:
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
    return templates.TemplateResponse(request, "emails.html", _email_list_context(DB_PATH, status, priority, search, page))


@app.get("/emails/{email_hash}", response_class=HTMLResponse)
async def email_detail(request: Request, email_hash: str):
    email_data = cache.get_email_by_hash(DB_PATH, email_hash)
    if not email_data:
        return HTMLResponse("<h1>Email not found</h1>", status_code=404)

    return templates.TemplateResponse(request, "email_detail.html", {
        "email": email_data,
        "email_hash": email_hash,
    })


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
    return templates.TemplateResponse(request, "partials/email_table.html", _email_list_context(DB_PATH, status, priority, search, page))


@app.get("/partials/email-detail/{email_hash}", response_class=HTMLResponse)
async def partial_email_detail(request: Request, email_hash: str):
    email_data = cache.get_email_by_hash(DB_PATH, email_hash)
    if not email_data:
        return HTMLResponse("<p>Email not found</p>", status_code=404)

    return templates.TemplateResponse(request, "partials/email_detail_content.html", {
        "email": email_data,
        "email_hash": email_hash,
    })


@app.get("/partials/tailscale-status", response_class=HTMLResponse)
async def partial_tailscale_status(request: Request):
    tailscale_ip = _get_tailscale_ip()
    tailscale_login_url = _get_tailscale_login_url()
    ts_state = _tailscale_state(tailscale_ip, tailscale_login_url)
    port = int(os.getenv("WEB_PORT", "8000"))
    return templates.TemplateResponse(request, "partials/tailscale_status.html", {
        "ts_state": ts_state,
        "tailscale_ip": tailscale_ip,
        "tailscale_dns_name": _get_tailscale_dns_name(),
        "tailscale_serve_url": _get_tailscale_serve_url(),
        "tailscale_login_url": tailscale_login_url,
        "port": port,
    })


def _resolve_bind_host(requested: str) -> str:
    loopback = {"127.0.0.1", "localhost", "::1"}
    if requested in loopback:
        return requested
    try:
        cache.init_db(DB_PATH)
        if not auth.is_auth_configured(DB_PATH):
            print(f"[auth] No dashboard password configured — binding to 127.0.0.1 (requested {requested}).")
            print("[auth] Set a password at http://127.0.0.1:8000/setup-dashboard, then restart to expose the dashboard.")
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
