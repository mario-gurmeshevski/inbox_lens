from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = _SRC_DIR / "data"

DB_PATH = str(_DATA_DIR / "emails.db")
KEYWORDS_FILE = str(_DATA_DIR / "keywords.json")
KEYWORDS_EXAMPLE_FILE = str(_DATA_DIR / "keywords.example.json")
SECRET_KEY_PATH = str(_DATA_DIR / ".secret.key")
SESSION_SECRET_PATH = str(_DATA_DIR / ".session.key")
