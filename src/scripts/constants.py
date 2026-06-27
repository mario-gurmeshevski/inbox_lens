import os
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR = Path(os.environ.get("INBOX_LENS_DATA_DIR") or _DEFAULT_DATA_DIR)

DB_PATH = str(_DATA_DIR / "emails.db")
KEYWORDS_FILE = str(_DATA_DIR / "keywords.json")
KEYWORDS_EXAMPLE_FILE = str(_DATA_DIR / "keywords.example.json")
SECRET_KEY_PATH = str(_DATA_DIR / ".secret.key")
SESSION_SECRET_PATH = str(_DATA_DIR / ".session.key")
