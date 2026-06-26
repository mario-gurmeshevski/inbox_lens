#!/bin/sh
set -e
DATA_DIR="/app/src/data"
mkdir -p "$DATA_DIR" 2>/dev/null || true
chown -R appuser:appuser "$DATA_DIR" 2>/dev/null || true
SOCK="/var/run/docker.sock"
if [ -S "$SOCK" ]; then
    SOCK_GID=$(stat -c '%g' "$SOCK" 2>/dev/null || echo "")
    if [ -n "$SOCK_GID" ]; then
        if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
            groupadd -g "$SOCK_GID" dockerhost 2>/dev/null || true
        fi
        usermod -aG "$SOCK_GID" appuser 2>/dev/null || true
    fi
fi

DB="$DATA_DIR/emails.db"
HOST="0.0.0.0"
if [ -d /shared ]; then
    exec gosu appuser uvicorn src.scripts.web:app --host "$HOST" --port 8000
fi

if [ -f "$DB" ]; then
    VAL=$(gosu appuser python3 -c "
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

exec gosu appuser uvicorn src.scripts.web:app --host "$HOST" --port 8000
