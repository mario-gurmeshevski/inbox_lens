#!/bin/sh
set -e
if [ $# -gt 0 ]; then
    exec "$@"
fi
DATA_DIR="/app/src/data"
mkdir -p "$DATA_DIR" 2>/dev/null || true
chown -R appuser:appuser "$DATA_DIR" 2>/dev/null || true
SOCK="/var/run/docker.sock"
if [ -S "$SOCK" ]; then
    python3 - "$SOCK" <<'PY' 2>/dev/null || true
import grp, os, subprocess, sys
sock = sys.argv[1]
try:
    gid = os.stat(sock).st_gid
except OSError:
    sys.exit(0)
try:
    name = grp.getgrgid(gid).gr_name
except KeyError:
    name = "dockerhost"
    r = subprocess.run(["addgroup", "-g", str(gid), "dockerhost"], check=False)
    if r.returncode != 0:
        print("entrypoint: could not create group dockerhost gid=%d" % gid)
        sys.exit(0)
r = subprocess.run(["addgroup", "appuser", name], check=False)
if r.returncode != 0:
    print("entrypoint: could not add appuser to group %s" % name)
PY
fi

DB="$DATA_DIR/emails.db"
HOST="0.0.0.0"
if [ -d /shared ]; then
    exec su-exec appuser uvicorn src.scripts.web:app --host "$HOST" --port 8000
fi

if [ -f "$DB" ]; then
    VAL=$(su-exec appuser python3 -c "
import sqlite3
try:
    c = sqlite3.connect('$DB')
    pwd = c.execute(\"SELECT value FROM settings WHERE key='dashboard_password_hash'\").fetchone()
    net = c.execute(\"SELECT value FROM settings WHERE key='network_access'\").fetchone()
    if not pwd or not pwd[0]:
        print('localhost')
    elif net and net[0] == 'false':
        print('localhost')
    else:
        print('open')
except Exception:
    print('localhost')
" 2>/dev/null || echo "localhost")
    if [ "$VAL" = "localhost" ]; then
        HOST="127.0.0.1"
    fi
fi

exec su-exec appuser uvicorn src.scripts.web:app --host "$HOST" --port 8000
