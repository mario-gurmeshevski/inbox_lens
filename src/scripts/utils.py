import json


def _priority_bucket(level) -> str | None:
    try:
        n = int(level)
    except (ValueError, TypeError):
        return None
    if n >= 9:
        return "critical"
    if n >= 7:
        return "high"
    if n >= 4:
        return "medium"
    if n >= 1:
        return "low"
    return None


def _parse_keyword_matches(kj: str | None) -> dict:
    if kj:
        try:
            return json.loads(kj)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
