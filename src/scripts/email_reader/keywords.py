import json
import re
import logging
from pathlib import Path

from src.scripts import cache
from src.scripts.constants import KEYWORDS_EXAMPLE_FILE, KEYWORDS_FILE

logger = logging.getLogger(__name__)

_KEYWORDS_SETTING_KEY = "keywords"


def _load_json_categories(path: str, source: str) -> dict:
    try:
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            cats = data.get("categories", {})
            if isinstance(cats, dict):
                return cats
            logger.warning("%s has no valid 'categories' object; ignoring", source)
    except Exception:
        logger.warning("Failed to load keywords from %s", source, exc_info=True)
    return {}


def _seed_categories() -> dict:
    cats = _load_json_categories(KEYWORDS_FILE, "keywords.json")
    if cats:
        return cats
    return _load_json_categories(KEYWORDS_EXAMPLE_FILE, "keywords.example.json")


def _validate_categories(categories) -> dict:
    if not isinstance(categories, dict):
        raise ValueError("categories must be an object mapping priority levels to word lists")

    cleaned: dict[str, list[str]] = {}
    for level, words in categories.items():
        try:
            n = int(level)
        except (ValueError, TypeError):
            raise ValueError(f"priority level {level!r} is not an integer")
        if n < 1:
            raise ValueError(f"priority level {n} must be >= 1")
        if not isinstance(words, (list, tuple)):
            raise ValueError(f"words for level {n} must be a list")
        seen: set[str] = set()
        out: list[str] = []
        for w in words:
            if not isinstance(w, str):
                raise ValueError(f"keyword words for level {n} must be strings")
            s = w.strip()
            if not s:
                continue
            key = s.lower()
            if key not in seen:
                seen.add(key)
                out.append(s)
        cleaned[str(n)] = out
    return cleaned


def load_keywords(db_path: str) -> dict:
    cache.init_db(db_path)
    raw = cache.get_setting(_KEYWORDS_SETTING_KEY, db_path)
    if raw:
        try:
            return json.loads(raw).get("categories", {})
        except Exception:
            logger.warning("Stored keywords setting was corrupt; reseeding", exc_info=True)
    categories = _seed_categories()
    try:
        save_keywords(categories, db_path)
    except Exception:
        logger.warning("Failed to seed keywords into the DB", exc_info=True)
    return categories


def save_keywords(categories, db_path: str) -> dict:
    cleaned = _validate_categories(categories)
    cache.save_setting(_KEYWORDS_SETTING_KEY, json.dumps({"categories": cleaned}, ensure_ascii=False), db_path)
    return cleaned


def build_compiled_patterns(categories: dict) -> dict:
    compiled = {}
    for category, words in categories.items():
        escaped = [re.escape(w.lower()) for w in words]
        pattern = re.compile(r"\b(" + "|".join(escaped) + r")\b")
        compiled[category] = (words, pattern)
    return compiled


def scan_emails(emails, db_path: str) -> dict:
    cache.init_db(db_path)
    categories = load_keywords(db_path)
    compiled_patterns = build_compiled_patterns(categories)
    return cache.scan_and_update(emails, db_path, compiled_patterns)
