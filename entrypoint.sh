#!/bin/sh
set -e
mkdir -p /app/data 2>/dev/null || true

KEYWORDS="${KEYWORDS_FILE:-/app/data/keywords.json}"
if [ ! -f "$KEYWORDS" ]; then
    cp /app/src/data/keywords.example.json "$KEYWORDS" 2>/dev/null || true
fi

DB="${DB_PATH:-/app/data/emails.db}"
HOST="0.0.0.0"
if [ -f "$DB" ]; then
    VAL=$(python3 -c "
import sqlite3, os
try:
    db = os.environ.get('DB_PATH', '/app/data/emails.db')
    r = sqlite3.connect(db).execute(\"SELECT value FROM settings WHERE key='network_access'\").fetchone()
    print(r[0] if r else 'true')
except Exception:
    print('true')
" 2>/dev/null || echo "true")
    if [ "$VAL" = "false" ]; then
        HOST="127.0.0.1"
    fi
fi

exec uvicorn src.scripts.web:app --host "$HOST" --port 8000
