import json
import re
import logging
from pathlib import Path

from src.scripts import cache

logger = logging.getLogger(__name__)


def load_keywords(keywords_path):
    path = Path(keywords_path)
    if not path.exists():
        example = path.with_name(path.stem + ".example.json")
        try:
            if example.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
                logger.info("Created %s from %s", path, example)
            else:
                return {}
        except Exception:
            logger.warning("Failed to seed %s from %s", path, example, exc_info=True)
            return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("categories", {})
    except Exception:
        logger.warning("Failed to load keywords from %s", path, exc_info=True)
        return {}


def build_compiled_patterns(categories):
    compiled = {}
    for category, words in categories.items():
        escaped = [re.escape(w.lower()) for w in words]
        pattern = re.compile(r"\b(" + "|".join(escaped) + r")\b")
        compiled[category] = (words, pattern)
    return compiled


def scan_emails(emails, keywords_path, db_path):
    cache.init_db(db_path)
    categories = load_keywords(keywords_path)
    compiled_patterns = build_compiled_patterns(categories)
    return cache.scan_and_update(emails, db_path, compiled_patterns)
