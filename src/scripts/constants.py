import os
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = _SRC_DIR / "data"

DB_PATH = os.getenv("DB_PATH", str(_DATA_DIR / "emails.db"))
KEYWORDS_FILE = os.getenv("KEYWORDS_FILE", str(_DATA_DIR / "keywords.json"))
SECRET_KEY_PATH = os.getenv("SECRET_KEY_PATH", str(_DATA_DIR / ".secret.key"))
DEFAULT_NETWORK_ACCESS = "true"
