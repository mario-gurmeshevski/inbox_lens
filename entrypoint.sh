#!/bin/sh
set -e
DATA_DIR="/app/src/data"
mkdir -p "$DATA_DIR" 2>/dev/null || true

KEYWORDS="$DATA_DIR/keywords.json"
if [ ! -f "$KEYWORDS" ]; then
    cp /app/src/data/keywords.example.json "$KEYWORDS" 2>/dev/null || true
fi

DB="$DATA_DIR/emails.db"
HOST="0.0.0.0"
if [ -f "$DB" ]; then
    VAL=$(python3 -c "
import sqlite3
try:
    r = sqlite3.connect('$DB').execute(\"SELECT value FROM settings WHERE key='network_access'\").fetchone()
    print(r[0] if r else 'true')
except Exception:
    print('true')
" 2>/dev/null || echo "true")
    if [ "$VAL" = "false" ]; then
        HOST="127.0.0.1"
    fi
fi

exec uvicorn src.scripts.web:app --host "$HOST" --port 8000
