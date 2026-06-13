import time
from unittest.mock import MagicMock

from src.scripts import bot


class TestPriorityEmoji:
    def test_level_9_plus_red(self):
        assert bot.priority_emoji("9") == "\U0001f534"
        assert bot.priority_emoji("10") == "\U0001f534"

    def test_level_7_8_orange(self):
        assert bot.priority_emoji("7") == "\U0001f7e0"
        assert bot.priority_emoji("8") == "\U0001f7e0"

    def test_level_5_6_yellow(self):
        assert bot.priority_emoji("5") == "\U0001f7e1"
        assert bot.priority_emoji("6") == "\U0001f7e1"

    def test_level_1_4_blue(self):
        assert bot.priority_emoji("1") == "\U0001f535"
        assert bot.priority_emoji("4") == "\U0001f535"

    def test_level_0_white(self):
        assert bot.priority_emoji("0") == "\u26aa"

    def test_non_numeric(self):
        assert bot.priority_emoji("abc") == "\u26aa"

    def test_none(self):
        assert bot.priority_emoji(None) == "\u26aa"


class TestGetHighestPriority:
    def test_returns_highest_numeric_key(self):
        matches = {"3": ["a"], "10": ["b"], "7": ["c"]}
        assert bot.get_highest_priority(matches) == 10

    def test_empty_matches_returns_zero(self):
        assert bot.get_highest_priority({}) == 0

    def test_none_matches(self):
        assert bot.get_highest_priority(None) == 0

    def test_ignores_non_numeric_keys(self):
        matches = {"high": ["a"], "3": ["b"]}
        assert bot.get_highest_priority(matches) == 3


class TestFormatEmail:
    def test_includes_subject_from_date(self):
        email_data = {
            "subject": "Test Subject",
            "from": "alice@example.com",
            "date": "Mon, 01 Jan 2024",
            "body": "Hello",
            "message_id": "<id@example.com>",
            "keyword_matches": {},
        }
        result = bot.format_email(1, email_data)
        assert "Test Subject" in result
        assert "alice@example.com" in result
        assert "Mon, 01 Jan 2024" in result

    def test_includes_keyword_tags(self):
        email_data = {
            "subject": "Sub",
            "from": "a@b.com",
            "date": "date",
            "body": "body",
            "message_id": "<id>",
            "keyword_matches": {"10": ["итно"], "7": ["проблем"]},
        }
        result = bot.format_email(1, email_data)
        assert "Tags:" in result
        assert "10" in result
        assert "7" in result

    def test_shows_snippet_by_default(self):
        long_body = "x" * 600
        email_data = {
            "subject": "Sub", "from": "a@b.com", "date": "d",
            "body": long_body, "message_id": "<id>", "keyword_matches": {},
        }
        result = bot.format_email(1, email_data)
        assert "..." in result

    def test_shows_full_body_when_requested(self):
        long_body = "x" * 600
        email_data = {
            "subject": "Sub", "from": "a@b.com", "date": "d",
            "body": long_body, "message_id": "<id>", "keyword_matches": {},
        }
        result = bot.format_email(1, email_data, full_body=True)
        assert "..." not in result
        assert long_body in result

    def test_no_body_message(self):
        email_data = {
            "subject": "Sub", "from": "a@b.com", "date": "d",
            "body": "", "message_id": "<id>", "keyword_matches": {},
        }
        result = bot.format_email(1, email_data)
        assert "no plain text body" in result

    def test_html_escaping(self):
        email_data = {
            "subject": "<script>alert(1)</script>",
            "from": "a&b@c.com",
            "date": "d",
            "body": "<b>bold</b>",
            "message_id": "<id>",
            "keyword_matches": {},
        }
        result = bot.format_email(1, email_data)
        assert "<script>" not in result
        assert "&lt;" in result

    def test_includes_message_id(self):
        email_data = {
            "subject": "Sub", "from": "a@b.com", "date": "d",
            "body": "b", "message_id": "<msg123@test.com>", "keyword_matches": {},
        }
        result = bot.format_email(1, email_data)
        assert "msg123@test.com" in result

    def test_no_subject_shows_default(self):
        email_data = {
            "subject": "", "from": "a@b.com", "date": "d",
            "body": "b", "message_id": "<id>", "keyword_matches": {},
        }
        result = bot.format_email(1, email_data)
        assert "(no subject)" in result or result.strip().startswith("\u26aa")


class TestEmailActionKeyboard:
    def test_contains_body_and_delete_buttons(self):
        kb = bot.email_action_keyboard("hash123", "all", 0)
        buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert any("ea_body:" in d for d in buttons)
        assert any("ea_del:" in d for d in buttons)

    def test_callback_data_format(self):
        kb = bot.email_action_keyboard("hash123", "7", 2)
        buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert any("hash123:7:2" in d for d in buttons)


class TestFormatEmailWithActions:
    def test_returns_text_and_keyboard(self):
        email_data = {
            "subject": "Sub", "from": "a@b.com", "date": "d",
            "body": "b", "message_id": "<id>", "keyword_matches": {},
        }
        text, kb = bot.format_email_with_actions(1, email_data, file_hash="h1")
        assert "Sub" in text
        assert kb is not None

    def test_no_keyboard_without_hash(self):
        email_data = {
            "subject": "Sub", "from": "a@b.com", "date": "d",
            "body": "b", "message_id": "<id>", "keyword_matches": {},
        }
        text, kb = bot.format_email_with_actions(1, email_data)
        assert kb is None


class TestFormatOrderedSummary:
    def test_includes_index_subject_from_date(self):
        email_data = {
            "subject": "My Subject",
            "from": "alice@e.com",
            "date": "Mon, 01 Jan 2024",
            "keyword_matches": {},
        }
        result = bot.format_ordered_summary(3, email_data)
        assert "3." in result
        assert "My Subject" in result
        assert "alice@e.com" in result

    def test_includes_tags(self):
        email_data = {
            "subject": "Sub", "from": "a@b.com", "date": "d",
            "keyword_matches": {"10": ["итно"]},
        }
        result = bot.format_ordered_summary(1, email_data)
        assert "Tags:" in result
        assert "10" in result


class TestCleanupDeleteAwaiting:
    def test_removes_stale_entries(self):
        bot.DELETE_AWAITING.clear()
        bot.DELETE_AWAITING["uid1"] = {"step": "awaiting_id", "ts": time.monotonic() - 100, "message_id": None}
        bot.DELETE_AWAITING["uid2"] = {"step": "awaiting_id", "ts": time.monotonic(), "message_id": None}
        bot._cleanup_delete_awaiting()
        assert "uid1" not in bot.DELETE_AWAITING
        assert "uid2" in bot.DELETE_AWAITING
        bot.DELETE_AWAITING.clear()

    def test_keeps_fresh_entries(self):
        bot.DELETE_AWAITING.clear()
        bot.DELETE_AWAITING["uid"] = {"step": "awaiting_id", "ts": time.monotonic(), "message_id": None}
        bot._cleanup_delete_awaiting()
        assert "uid" in bot.DELETE_AWAITING
        bot.DELETE_AWAITING.clear()


class TestAuthorized:
    def test_allows_when_no_allowed_ids(self):
        bot.ALLOWED_CHAT_IDS.clear()
        update = MagicMock()
        update.effective_chat.id = 12345
        assert bot._authorized(update) is True

    def test_allows_when_chat_id_in_set(self):
        original = bot.ALLOWED_CHAT_IDS.copy()
        bot.ALLOWED_CHAT_IDS.clear()
        bot.ALLOWED_CHAT_IDS.add(12345)
        update = MagicMock()
        update.effective_chat.id = 12345
        assert bot._authorized(update) is True
        bot.ALLOWED_CHAT_IDS.clear()
        bot.ALLOWED_CHAT_IDS.update(original)

    def test_denies_when_chat_id_not_in_set(self):
        original = bot.ALLOWED_CHAT_IDS.copy()
        bot.ALLOWED_CHAT_IDS.clear()
        bot.ALLOWED_CHAT_IDS.add(99999)
        update = MagicMock()
        update.effective_chat.id = 12345
        assert bot._authorized(update) is False
        bot.ALLOWED_CHAT_IDS.clear()
        bot.ALLOWED_CHAT_IDS.update(original)


class TestMainKeyboard:
    def test_has_required_buttons(self):
        kb = bot.main_keyboard()
        buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "status" in buttons
        assert "offline" in buttons
        assert "ord_menu" in buttons
        assert "delete" in buttons

    def test_no_manual_fetch_buttons(self):
        kb = bot.main_keyboard()
        buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "fetch" not in buttons
        assert "scan" not in buttons
        assert "fetch_scan" not in buttons
