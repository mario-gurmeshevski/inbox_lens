import json


def get_highest_priority(matches):
    highest = 0
    if matches:
        for k in matches:
            try:
                n = int(k)
                if n > highest:
                    highest = n
            except (ValueError, TypeError):
                pass
    return highest


def priority_emoji(level):
    try:
        n = int(level)
    except (ValueError, TypeError):
        return "\u26aa"
    if n >= 9:
        return "\u26ab"
    if n >= 7:
        return "\U0001f534"
    if n >= 4:
        return "\U0001f7e0"
    if n >= 1:
        return "\U0001f7e1"
    return "\u26aa"


def _parse_keyword_matches(kj: str | None) -> dict:
    if kj:
        try:
            return json.loads(kj)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def format_tags(matches, prefix="Tags: "):
    tag_parts = []
    for cat, words in sorted(
        matches.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0, reverse=True
    ):
        e = priority_emoji(cat)
        tag_parts.append(f"{e}{cat}: {', '.join(words)}")
    return prefix + " | ".join(tag_parts)
