import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.scripts.utils import get_highest_priority, priority_emoji, format_tags


def format_email(idx, email_data, full_body=False):
    matches = email_data.get("keyword_matches", {})
    highest = get_highest_priority(matches)

    emoji = priority_emoji(highest)
    subject = html.escape(email_data.get("subject", "(no subject)"))
    from_addr = html.escape(email_data.get("from", ""))
    date = html.escape(email_data.get("date", ""))
    body = html.escape(email_data.get("body", ""))

    lines = [f"{emoji} <b>[{highest or '-'}] {subject}</b>"]
    lines.append(f"From: {from_addr}")
    lines.append(f"Date: {date}")

    if matches:
        lines.append(format_tags(matches))

    lines.append("")

    if body:
        if full_body:
            lines.append(body)
        else:
            snippet = body[:500]
            if len(body) > 500:
                snippet += "..."
            lines.append(snippet)
    else:
        lines.append("(no plain text body)")

    msg_id = html.escape(email_data.get("message_id", ""))
    if msg_id:
        lines.append(f"\n<code>Message-ID: {msg_id}</code>")

    return "\n".join(lines)


def email_action_keyboard(file_hash, level="all", page=0):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f4d6 Full Body", callback_data=f"ea_body:{file_hash}:{level}:{page}"),
            InlineKeyboardButton("\U0001f5d1 Delete", callback_data=f"ea_del:{file_hash}:{level}:{page}"),
        ],
    ])


def format_email_with_actions(idx, email_data, file_hash=None, level="all", page=0, full_body=False):
    text = format_email(idx, email_data, full_body)
    if file_hash:
        reply_markup = email_action_keyboard(file_hash, level, page)
    else:
        reply_markup = None
    return (text, reply_markup)


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f4ca Status", callback_data="status"),
            InlineKeyboardButton("\U0001f4c2 Offline", callback_data="offline"),
        ],
        [
            InlineKeyboardButton("\U0001f4cb Ordered", callback_data="ord_menu"),
            InlineKeyboardButton("\U0001f5d1 Delete", callback_data="delete"),
        ],
    ])


ORDERED_PAGE_SIZE = 5


def format_ordered_summary(idx, email_data):
    matches = email_data.get("keyword_matches", {})
    highest = get_highest_priority(matches)

    emoji = priority_emoji(highest)
    subject = html.escape(email_data.get("subject", "(no subject)"))
    from_addr = html.escape(email_data.get("from", ""))
    date = html.escape(email_data.get("date", ""))

    lines = [f"{idx}. {emoji} <b>{subject}</b>"]
    lines.append(f"   From: {from_addr}")
    lines.append(f"   Date: {date}")

    if matches:
        lines.append(format_tags(matches, prefix="   Tags: "))

    return "\n".join(lines)


def ordered_level_keyboard(levels):
    if not levels:
        return None

    rows = []
    row = []
    for level in levels:
        emoji = priority_emoji(level)
        row.append(InlineKeyboardButton(f"{emoji} Level {level}", callback_data=f"ord:{level}:0"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("\U0001f4cb All Levels", callback_data="ord:all:0")])
    rows.append([InlineKeyboardButton("\u2b05 Back", callback_data="ord_back_menu")])
    return InlineKeyboardMarkup(rows)


def ordered_list_keyboard(emails, level, page):
    from src.scripts.utils import get_highest_priority as _ghp
    total_pages = max(1, -(-len(emails) // ORDERED_PAGE_SIZE))
    rows = []
    start = page * ORDERED_PAGE_SIZE
    end = min(start + ORDERED_PAGE_SIZE, len(emails))
    for i in range(start, end):
        e = emails[i]
        fh = e.get("_file_hash", "")
        subject = e.get("subject", "(no subject)")[:30]
        matches = e.get("keyword_matches", {})
        highest = _ghp(matches)
        emoji = priority_emoji(highest)
        rows.append([InlineKeyboardButton(f"{emoji} {subject}", callback_data=f"ord_detail:{fh}:{level}:{page}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("\u25c0 Prev", callback_data=f"ord:{level}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next \u25b6", callback_data=f"ord:{level}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("\u2b05 Back to Levels", callback_data="ord_menu")])
    rows.append([InlineKeyboardButton("\U0001f3e0 Main Menu", callback_data="ord_back_menu")])
    return InlineKeyboardMarkup(rows)


def ordered_detail_keyboard(file_hash, level, page):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f4d6 Full Body", callback_data=f"full_body:{file_hash}:{level}:{page}")],
        [InlineKeyboardButton("\U0001f5d1 Delete", callback_data=f"ea_del:{file_hash}:{level}:{page}")],
        [InlineKeyboardButton("\u2b05 Back to List", callback_data=f"ord:{level}:{page}")],
        [InlineKeyboardButton("\U0001f3e0 Main Menu", callback_data="ord_back_menu")],
    ])
