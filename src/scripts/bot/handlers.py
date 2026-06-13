import html
import os
import time
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

from src.scripts import email_reader, cache, idle_monitor
from src.scripts.constants import DB_PATH, KEYWORDS_FILE
from src.scripts.bot.formatters import (
    format_email,
    format_email_with_actions,
    format_ordered_summary,
    main_keyboard,
    email_action_keyboard,
    ordered_level_keyboard,
    ordered_list_keyboard,
    ordered_detail_keyboard,
    ORDERED_PAGE_SIZE,
)

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALERT_MIN_PRIORITY = int(os.getenv("ALERT_MIN_PRIORITY", "7"))

ALLOWED_CHAT_IDS = set()
_chat_id = os.getenv("TELEGRAM_CHAT_ID")
if _chat_id:
    ALLOWED_CHAT_IDS.add(int(_chat_id))
for cid in os.getenv("ALLOWED_CHAT_IDS", "").split(","):
    cid = cid.strip()
    if cid:
        ALLOWED_CHAT_IDS.add(int(cid))

DELETE_TIMEOUT = 60
DELETE_AWAITING = {}

LOGIN_ENTER_EMAIL, LOGIN_ENTER_PASSWORD = range(2)

MAX_MSG_LEN = 4000
MAX_DISPLAY_MATCHES = 20

_monitor = None


def _authorized(update: Update) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return update.effective_chat.id in ALLOWED_CHAT_IDS


async def auth_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        if update.message:
            await update.message.reply_text("\u26a0 Unauthorized.")
        raise ApplicationHandlerStop


def _needs_login() -> bool:
    return not cache.has_email_credentials(DB_PATH)


async def _typing(bot, chat_id):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)


async def _send_long(bot, chat_id, text, parse_mode="HTML", reply_markup=None):
    if len(text) <= MAX_MSG_LEN:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
        return
    chunks = []
    while text:
        cut = text[:MAX_MSG_LEN]
        last_nl = cut.rfind("\n")
        if last_nl > MAX_MSG_LEN // 2:
            cut = text[:last_nl]
        chunks.append(cut)
        text = text[len(cut):]
    last = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        rm = reply_markup if i == last else None
        await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode, reply_markup=rm)


async def _send_messages(bot, chat_id, result, done_label, done_icon=""):
    if isinstance(result, list):
        for m in result:
            if isinstance(m, tuple):
                await _send_long(bot, chat_id, m[0], reply_markup=m[1])
            else:
                await _send_long(bot, chat_id, m)
        label = f"{done_icon} {done_label}" if done_icon else done_label
        await _send_long(bot, chat_id, label, reply_markup=main_keyboard())
    else:
        await _send_long(bot, chat_id, result, reply_markup=main_keyboard())


def _cleanup_delete_awaiting():
    now = time.monotonic()
    stale = [uid for uid, entry in DELETE_AWAITING.items() if now - entry["ts"] > DELETE_TIMEOUT]
    for uid in stale:
        DELETE_AWAITING.pop(uid, None)


async def do_status(context: ContextTypes.DEFAULT_TYPE):
    counts = await asyncio.to_thread(cache.get_counts, DB_PATH)

    monitor_status = "\u2705 Active" if (_monitor and _monitor.running) else "\u23f3 Inactive"

    parts = [
        "\U0001f4ca <b>Cache Status</b>\n",
        f"\U0001f4c2 Inbox cache: <b>{counts['fetched']}</b> email(s)",
        f"\u2705 Checked cache: <b>{counts['checked']}</b> email(s)",
    ]
    if counts.get("headers_only", 0) > 0:
        parts.append(f"\u23f3 Headers only (bodies pending): <b>{counts['headers_only']}</b> email(s)")
    parts.append(f"\U0001f509 Auto-monitor: <b>{monitor_status}</b>")

    return "\n".join(parts)


async def do_offline(since_date=None):
    emails = await asyncio.to_thread(
        cache.read_emails,
        DB_PATH,
        None,
        since_date,
    )

    if not emails:
        return "\U0001f4c2 No cached emails found."

    messages = [f"\U0001f4c2 Found <b>{len(emails)}</b> cached email(s):\n"]
    for e in emails:
        file_hash = e.get("_file_hash") or cache._hash_message_id(e.get("message_id", ""))
        text, kb = format_email_with_actions(0, e, file_hash=file_hash)
        messages.append((text, kb))

    return messages


async def do_offline_scan(context=None, chat_id=None):
    has_progress = context is not None and chat_id is not None

    unscanned = await asyncio.to_thread(cache.count_unscanned, DB_PATH)

    if has_progress:
        if unscanned == 0:
            return []

    emails = await asyncio.to_thread(
        cache.read_emails,
        DB_PATH,
        None,
    )

    if not emails:
        return "\U0001f4c2 No cached emails found."

    scan_result = await asyncio.to_thread(
        email_reader.scan_emails,
        emails,
        KEYWORDS_FILE,
        DB_PATH,
    )

    messages = []

    matched = len(scan_result["emails_with_matches"])
    scanned = scan_result["scanned"]
    already = scan_result["already_checked"]

    parts = []
    if scanned:
        parts.append(f"{scanned} scanned")
    if already:
        parts.append(f"{already} already checked")
    parts.append(f"{matched} matched")
    messages.append(f"\U0001f50d {', '.join(parts)}")

    matched_emails = [e for e in emails if e.get("keyword_matches")]
    if len(matched_emails) > MAX_DISPLAY_MATCHES:
        messages.append(f"\u26a0 {len(matched_emails)} matches found \u2014 too many to display. Use /ordered to browse by priority.")
    else:
        for e in matched_emails:
            file_hash = e.get("_file_hash") or cache._hash_message_id(e.get("message_id", ""))
            text, kb = format_email_with_actions(0, e, file_hash=file_hash)
            messages.append((text, kb))

    return messages


async def do_ordered_menu(chat_id, bot):
    levels = cache.get_ordered_levels(DB_PATH)
    if not levels:
        await _send_long(bot, chat_id, "\U0001f4cb No ordered emails found. Wait for auto-scan to process emails.", reply_markup=main_keyboard())
        return
    keyboard = ordered_level_keyboard(levels)
    await _send_long(bot, chat_id, "\U0001f4cb <b>Ordered Emails</b>\n\nSelect a priority level:", reply_markup=keyboard)


async def do_ordered_list(chat_id, bot, level, page):
    if level == "all":
        emails = await asyncio.to_thread(cache.list_ordered_emails, DB_PATH)
    else:
        emails = await asyncio.to_thread(cache.list_ordered_emails, DB_PATH, priority_level=level)

    if not emails:
        await _send_long(bot, chat_id, f"\U0001f4cb No ordered emails at level {level}.", reply_markup=main_keyboard())
        return

    total_pages = max(1, -(-len(emails) // ORDERED_PAGE_SIZE))
    start = page * ORDERED_PAGE_SIZE
    end = min(start + ORDERED_PAGE_SIZE, len(emails))

    level_label = "All Levels" if level == "all" else f"Level {level}"
    header = f"\U0001f4cb <b>{level_label}</b> \u2014 {len(emails)} email(s) | Page {page + 1}/{total_pages}\n"

    parts = []
    for i in range(start, end):
        parts.append(format_ordered_summary(i + 1, emails[i]))
    body = "\n\n".join(parts)

    keyboard = ordered_list_keyboard(emails, level, page)
    await _send_long(bot, chat_id, header + "\n" + body, reply_markup=keyboard)


async def do_ordered_detail(chat_id, bot, file_hash, level, page):
    email_data = await asyncio.to_thread(cache.get_email_by_hash, DB_PATH, file_hash)

    if not email_data:
        await _send_long(bot, chat_id, "\u274c Email not found in ordered cache.", reply_markup=main_keyboard())
        return

    if not email_data.get("body"):
        body_note = "\n\n<code>Body not yet downloaded. Click Full Body to fetch.</code>"
        email_data["body"] = body_note

    msg = format_email(0, email_data)
    keyboard = ordered_detail_keyboard(file_hash, level, page)
    await _send_long(bot, chat_id, msg, reply_markup=keyboard)


async def _ensure_body(email_data, bot=None, chat_id=None):
    if email_data.get("body"):
        return None
    msg_id = email_data.get("message_id", "")
    if not msg_id:
        return "No message ID"
    if bot and chat_id:
        await _typing(bot, chat_id)
    result = await asyncio.to_thread(
        email_reader.fetch_single_body,
        msg_id,
        db_path=DB_PATH,
    )
    if "body" in result:
        email_data["body"] = result["body"]
        return None
    return result.get("error", "unknown error")


async def do_full_body(chat_id, bot, file_hash, level, page):
    await _typing(bot, chat_id)
    email_data = await asyncio.to_thread(cache.get_email_by_hash, DB_PATH, file_hash)

    if not email_data:
        await _send_long(bot, chat_id, "\u274c Email not found.", reply_markup=main_keyboard())
        return

    err = await _ensure_body(email_data, bot, chat_id)
    if err:
        await _send_long(
            bot, chat_id,
            f"\u26a0 Body not yet available: {html.escape(err)}",
            reply_markup=main_keyboard(),
        )
        return

    msg = format_email(0, email_data, full_body=True)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f5d1 Delete", callback_data=f"ea_del:{file_hash}:{level}:{page}")],
        [InlineKeyboardButton("\u2b05 Back to Detail", callback_data=f"ord_detail:{file_hash}:{level}:{page}")],
        [InlineKeyboardButton("\U0001f3e0 Main Menu", callback_data="ord_back_menu")],
    ])
    await _send_long(bot, chat_id, msg, reply_markup=keyboard)


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if cache.has_email_credentials(DB_PATH):
        user, _ = cache.get_email_credentials(DB_PATH)
        await update.message.reply_text(
            f"\u2705 Already connected as <code>{html.escape(user)}</code>.\n"
            "Use /logout to disconnect first.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "\U0001f510 <b>Email Login</b>\n\n"
        "Send your email address (e.g. you@gmail.com).\n"
        "Send /cancel to abort.",
        parse_mode="HTML",
    )
    return LOGIN_ENTER_EMAIL


async def login_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if "@" not in email:
        await update.message.reply_text(
            "\u26a0 That doesn't look like an email address. Try again or /cancel."
        )
        return LOGIN_ENTER_EMAIL

    context.user_data["login_email"] = email
    await update.message.reply_text(
        f"\U0001f511 Email: <code>{html.escape(email)}</code>\n\n"
        "Now send your App Password.\n"
        "\u26a0 The message will be deleted after processing for security.\n"
        "Send /cancel to abort.",
        parse_mode="HTML",
    )
    return LOGIN_ENTER_PASSWORD


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _monitor

    password = update.message.text.strip()
    email = context.user_data.get("login_email", "")

    try:
        await update.message.delete()
    except Exception:
        pass

    if not password:
        await update.message.reply_text("\u26a0 Password cannot be empty. Try again or /cancel.")
        return LOGIN_ENTER_PASSWORD

    msg = await update.message.reply_text("\U0001f504 Testing connection...")

    imap_server = os.getenv("IMAP_SERVER", "imap.gmail.com")
    result = await asyncio.to_thread(
        email_reader.test_connection,
        imap_server,
        email,
        password,
    )

    if not result["success"]:
        await msg.edit_text(
            f"\u274c Connection failed: {html.escape(result['error'])}\n\n"
            "Try /login again.",
            parse_mode="HTML",
        )
        context.user_data.pop("login_email", None)
        return ConversationHandler.END

    cache.init_db(DB_PATH)
    cache.save_setting("imap_server", imap_server, DB_PATH)
    cache.save_email_credentials(email, password, DB_PATH)

    count = result.get("inbox_count", "?")

    loop = asyncio.get_running_loop()
    chat_id = update.effective_chat.id
    _on_bot_new_emails = _make_alert_callback(context.bot, chat_id, loop)

    await msg.edit_text(
        "\u2705 Connected! Fetching emails...",
        parse_mode="HTML",
    )

    def _initial_fetch():
        idle_monitor.run_initial_fetch(DB_PATH)

    await asyncio.to_thread(_initial_fetch)

    _monitor = idle_monitor.IdleMonitor(db_path=DB_PATH, on_new_emails=_on_bot_new_emails)
    _monitor.start()

    await msg.edit_text(
        f"\u2705 Connected successfully!\n\n"
        f"\U0001f4e7 Email: <code>{html.escape(email)}</code>\n"
        f"\U0001f4c5 Inbox: {count} email(s)\n\n"
        "Emails fetched and auto-monitoring started.\n"
        "You'll receive alerts for high-priority emails.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )
    context.user_data.pop("login_email", None)
    return ConversationHandler.END


async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("login_email", None)
    await update.message.reply_text("\u274c Login cancelled.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _monitor
    if not cache.has_email_credentials(DB_PATH):
        await update.message.reply_text("\u26a0 No email account connected.")
        return
    if _monitor:
        _monitor.stop()
        _monitor = None
    cache.delete_email_credentials(DB_PATH)
    cache.delete_setting("imap_server", DB_PATH)
    await update.message.reply_text(
        "\u2705 Email account disconnected. Use /login to connect again.",
        reply_markup=main_keyboard(),
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _needs_login():
        text = (
            "\U0001f4e7 <b>Email Reader Bot</b>\n\n"
            "\u26a0 No email account connected.\n"
            "Use /login to connect your email account first."
        )
        await update.message.reply_text(text, parse_mode="HTML")
        return

    monitor_status = "\u2705 Active" if (_monitor and _monitor.running) else "\u23f3 Inactive"

    text = (
        "\U0001f4e7 <b>Email Reader Bot</b>\n\n"
        "Emails are fetched automatically when new mail arrives.\n"
        "Use the buttons below or type commands.\n\n"
        f"\U0001f4ca <b>Status</b> \u2014 show cache stats & monitor status\n"
        f"\U0001f4c2 <b>Offline</b> \u2014 list cached emails (no IMAP)\n"
        f"\U0001f4cb <b>Ordered</b> \u2014 browse scanned emails by priority\n"
        f"\U0001f5d1 <b>Delete</b> \u2014 delete an email by Message-ID\n\n"
        f"\U0001f509 Auto-monitor: {monitor_status}\n"
        f"High-priority alerts (>= {ALERT_MIN_PRIORITY}) are sent automatically."
    )
    await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start \u2014 Show main menu\n"
        "/login \u2014 Connect your email account\n"
        "/logout \u2014 Disconnect email account\n"
        "/offline \u2014 List cached emails (no IMAP)\n"
        "/offlinescan \u2014 Scan cached emails offline\n"
        "/ordered \u2014 Browse ordered emails by priority\n"
        "/status \u2014 Cache stats & monitor status\n"
        "/delete \u2014 Delete an email\n\n"
        "Emails are fetched automatically via IMAP IDLE.\n"
        "No manual fetch needed!",
        parse_mode="HTML",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _typing(context.bot, update.effective_chat.id)
    msg = await do_status(context)
    await _send_long(context.bot, update.effective_chat.id, msg, reply_markup=main_keyboard())


async def cmd_offline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _typing(context.bot, update.effective_chat.id)
    since_date = email_reader.parse_since("today")
    result = await do_offline(since_date)
    await _send_messages(context.bot, update.effective_chat.id, result, "Offline list complete.", "\U0001f4c2")


async def cmd_offlinescan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = await do_offline_scan(context=context, chat_id=update.effective_chat.id)
    await _send_messages(context.bot, update.effective_chat.id, result, "Offline scan complete.", "\U0001f4c2\U0001f50d")


async def cmd_ordered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _typing(context.bot, update.effective_chat.id)
    await do_ordered_menu(update.effective_chat.id, context.bot)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cleanup_delete_awaiting()
    DELETE_AWAITING[update.effective_user.id] = {"step": "awaiting_id", "ts": time.monotonic(), "message_id": None}
    await update.message.reply_text(
        "\U0001f5d1 Send me the <code>Message-ID</code> of the email to delete.\n"
        "Send /cancel to abort.",
        parse_mode="HTML",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    DELETE_AWAITING.pop(update.effective_user.id, None)
    await update.message.reply_text("\u274c Cancelled.", reply_markup=main_keyboard())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _cleanup_delete_awaiting()
    entry = DELETE_AWAITING.get(user_id)
    if not entry:
        return

    if time.monotonic() - entry["ts"] > DELETE_TIMEOUT:
        DELETE_AWAITING.pop(user_id, None)
        return

    if entry["step"] == "awaiting_id":
        message_id = update.message.text.strip()
        DELETE_AWAITING[user_id] = {"step": "confirming", "ts": time.monotonic(), "message_id": message_id}
        confirm_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("\u2705 Confirm Delete", callback_data="confirm_delete"),
                InlineKeyboardButton("\u274c Cancel", callback_data="cancel_delete"),
            ],
        ])
        escaped = html.escape(message_id)
        await update.message.reply_text(
            f"\u26a0\u26a0\u26a0 Confirm deletion of:\n<code>{escaped}</code>\n\n"
            "This will move the email to trash. Are you sure?",
            parse_mode="HTML",
            reply_markup=confirm_kb,
        )


async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = update.effective_chat.id

    if data == "delete":
        _cleanup_delete_awaiting()
        DELETE_AWAITING[update.effective_user.id] = {"step": "awaiting_id", "ts": time.monotonic(), "message_id": None}
        await _send_long(
            context.bot, chat_id,
            "\U0001f5d1 Send me the <code>Message-ID</code> of the email to delete.\n"
            "Send /cancel to abort.",
            reply_markup=main_keyboard(),
        )

    elif data == "status":
        await _typing(context.bot, chat_id)
        msg = await do_status(context)
        await _send_long(context.bot, chat_id, msg, reply_markup=main_keyboard())

    elif data == "offline":
        await _typing(context.bot, chat_id)
        since_date = email_reader.parse_since("today")
        result = await do_offline(since_date)
        await _send_messages(context.bot, chat_id, result, "Offline list complete.", "\U0001f4c2")

    elif data == "offline_scan":
        result = await do_offline_scan(context=context, chat_id=chat_id)
        await _send_messages(context.bot, chat_id, result, "Offline scan complete.", "\U0001f4c2\U0001f50d")

    elif data == "ord_menu":
        await _typing(context.bot, chat_id)
        await do_ordered_menu(chat_id, context.bot)

    elif data == "ord_back_menu":
        await _send_long(context.bot, chat_id, "\U0001f3e0 Main Menu", reply_markup=main_keyboard())

    elif data.startswith("ord:"):
        parts = data.split(":")
        level = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
        await _typing(context.bot, chat_id)
        await do_ordered_list(chat_id, context.bot, level, page)

    elif data.startswith("ord_detail:"):
        parts = data.split(":")
        file_hash = parts[1]
        level = parts[2] if len(parts) > 2 else "all"
        page = int(parts[3]) if len(parts) > 3 else 0
        await _typing(context.bot, chat_id)
        await do_ordered_detail(chat_id, context.bot, file_hash, level, page)

    elif data.startswith("full_body:"):
        parts = data.split(":")
        file_hash = parts[1]
        level = parts[2] if len(parts) > 2 else "all"
        page = int(parts[3]) if len(parts) > 3 else 0
        await do_full_body(chat_id, context.bot, file_hash, level, page)

    elif data.startswith("ea_body:"):
        parts = data.split(":")
        file_hash = parts[1]
        level = parts[2] if len(parts) > 2 else "all"
        page = int(parts[3]) if len(parts) > 3 else 0
        await _typing(context.bot, chat_id)
        email_data = await asyncio.to_thread(cache.get_email_by_hash, DB_PATH, file_hash)
        if not email_data:
            try:
                await query.edit_message_text("\u274c Email not found.")
            except Exception:
                pass
            return
        err = await _ensure_body(email_data)
        if err:
            try:
                await query.edit_message_text(
                    f"\u26a0 Body not available: {html.escape(err)}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return
        text = format_email(0, email_data, full_body=True)
        keyboard = email_action_keyboard(file_hash, level, page)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await _send_long(context.bot, chat_id, text, reply_markup=keyboard)

    elif data.startswith("ea_del_confirm:"):
        parts = data.split(":")
        file_hash = parts[1]
        level = parts[2] if len(parts) > 2 else "all"
        page = int(parts[3]) if len(parts) > 3 else 0
        email_data = await asyncio.to_thread(cache.get_email_by_hash, DB_PATH, file_hash)
        if not email_data:
            try:
                await query.edit_message_text("\u274c Email not found.")
            except Exception:
                pass
            return
        message_id = email_data.get("message_id", "")
        result = await asyncio.to_thread(
            email_reader.delete_email,
            message_id,
            db_path=DB_PATH,
        )
        if "error" in result:
            try:
                await query.edit_message_text(
                    f"\u274c Error: {html.escape(result['error'])}",
                    parse_mode="HTML",
                    reply_markup=email_action_keyboard(file_hash, level, page),
                )
            except Exception:
                pass
        elif result.get("deleted"):
            try:
                await query.edit_message_text(
                    "\u2705 Email deleted successfully.",
                    reply_markup=main_keyboard(),
                )
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text(
                    f"\u274c {html.escape(result.get('error', 'Failed to delete'))}",
                    parse_mode="HTML",
                    reply_markup=email_action_keyboard(file_hash, level, page),
                )
            except Exception:
                pass

    elif data.startswith("ea_del_cancel:"):
        parts = data.split(":")
        file_hash = parts[1]
        level = parts[2] if len(parts) > 2 else "all"
        page = int(parts[3]) if len(parts) > 3 else 0
        email_data = await asyncio.to_thread(cache.get_email_by_hash, DB_PATH, file_hash)
        if not email_data:
            try:
                await query.edit_message_text("\u274c Email not found.")
            except Exception:
                pass
            return
        text = format_email(0, email_data)
        keyboard = email_action_keyboard(file_hash, level, page)
        try:
            await query.edit_message_text(
                text + "\n\n\u274c Delete cancelled.",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            pass

    elif data.startswith("ea_del:"):
        parts = data.split(":")
        file_hash = parts[1]
        level = parts[2] if len(parts) > 2 else "all"
        page = int(parts[3]) if len(parts) > 3 else 0
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("\u2705 Confirm Delete", callback_data=f"ea_del_confirm:{file_hash}:{level}:{page}"),
                InlineKeyboardButton("\u274c Cancel", callback_data=f"ea_del_cancel:{file_hash}:{level}:{page}"),
            ],
        ])
        current_text = query.message.text or ""
        try:
            await query.edit_message_text(
                current_text + "\n\n\u26a0\u26a0\u26a0 Confirm deletion of this email?",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            pass

    elif data == "confirm_delete":
        await _typing(context.bot, chat_id)
        entry = DELETE_AWAITING.get(update.effective_user.id)
        if not entry or entry["step"] != "confirming":
            await _send_long(context.bot, chat_id, "\u274c Session expired. Try /delete again.", reply_markup=main_keyboard())
            return
        message_id = entry["message_id"]
        DELETE_AWAITING.pop(update.effective_user.id, None)
        result = await asyncio.to_thread(
            email_reader.delete_email,
            message_id,
            db_path=DB_PATH,
        )
        if "error" in result:
            await _send_long(context.bot, chat_id, f"\u274c Error: {result['error']}", reply_markup=main_keyboard())
        elif result.get("deleted"):
            escaped = html.escape(message_id)
            await _send_long(context.bot, chat_id, f"\u2705 Deleted: <code>{escaped}</code>", reply_markup=main_keyboard())
        else:
            await _send_long(context.bot, chat_id, f"\u274c {result.get('error', 'Failed to delete')}", reply_markup=main_keyboard())

    elif data == "cancel_delete":
        DELETE_AWAITING.pop(update.effective_user.id, None)
        await _send_long(context.bot, chat_id, "\u274c Delete cancelled.", reply_markup=main_keyboard())


def _make_alert_callback(bot_or_app, chat_id, loop):
    def _on_bot_new_emails(fetch_result, all_emails):
        if not chat_id:
            return
        try:
            alert_emails = []
            for e in all_emails:
                matches = e.get("keyword_matches", {})
                for cat in matches:
                    try:
                        if int(cat) >= ALERT_MIN_PRIORITY:
                            alert_emails.append(e)
                            break
                    except (ValueError, TypeError):
                        pass

            if not alert_emails:
                return

            async def _send_alerts():
                await _send_long(
                    bot_or_app.bot if hasattr(bot_or_app, 'bot') else bot_or_app,
                    int(chat_id),
                    f"\U0001f6a8 <b>{len(alert_emails)} high-priority email(s) detected!</b>",
                    reply_markup=main_keyboard(),
                )
                for e in alert_emails:
                    file_hash = e.get("_file_hash") or cache._hash_message_id(e.get("message_id", ""))
                    text, kb = format_email_with_actions(0, e, file_hash=file_hash)
                    await _send_long(
                        bot_or_app.bot if hasattr(bot_or_app, 'bot') else bot_or_app,
                        int(chat_id),
                        text,
                        reply_markup=kb,
                    )

            asyncio.run_coroutine_threadsafe(_send_alerts(), loop)
        except Exception:
            logger.warning("Bot on_new_emails callback error", exc_info=True)

    return _on_bot_new_emails


async def post_init(application: Application):
    global _monitor

    if cache.has_email_credentials(DB_PATH):
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        loop = asyncio.get_running_loop()
        _on_bot_new_emails = _make_alert_callback(application, chat_id, loop)

        _monitor = idle_monitor.IdleMonitor(db_path=DB_PATH, on_new_emails=_on_bot_new_emails)
        _monitor.start()
        logger.info("IDLE monitor started for bot")
    else:
        logger.info("No email credentials. Use /login to connect.")


def main():
    if not BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN must be set in .env")
        return

    cache._ensure_secret_key()
    cache.init_db(DB_PATH)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(MessageHandler(filters.ALL, auth_guard), group=-1)

    login_handler = ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={
            LOGIN_ENTER_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_email)],
            LOGIN_ENTER_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )
    app.add_handler(login_handler)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("offline", cmd_offline))
    app.add_handler(CommandHandler("offlinescan", cmd_offlinescan))
    app.add_handler(CommandHandler("ordered", cmd_ordered))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
