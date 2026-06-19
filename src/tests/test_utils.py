from src.scripts import utils


class TestParseKeywordMatches:
    def test_parses_valid_json(self):
        result = utils._parse_keyword_matches('{"10": ["important"]}')
        assert result == {"10": ["important"]}

    def test_returns_empty_for_invalid_json(self):
        assert utils._parse_keyword_matches("not json {{{") == {}

    def test_returns_empty_for_none(self):
        assert utils._parse_keyword_matches(None) == {}

    def test_returns_empty_for_empty_string(self):
        assert utils._parse_keyword_matches("") == {}

    def test_returns_empty_for_non_dict_json(self):
        assert utils._parse_keyword_matches("[1, 2, 3]") == [1, 2, 3]


class TestFormatTags:
    def test_returns_default_prefix(self):
        result = utils.format_tags({"10": ["important"]})
        assert result.startswith("Tags: ")

    def test_uses_custom_prefix(self):
        result = utils.format_tags({"10": ["important"]}, prefix="   Tags: ")
        assert result.startswith("   Tags: ")

    def test_orders_categories_descending_by_digit(self):
        result = utils.format_tags({"3": ["a"], "10": ["b"], "7": ["c"]})
        assert result.index("10") < result.index("7") < result.index("3")

    def test_joins_categories_with_separator(self):
        result = utils.format_tags({"10": ["x"], "7": ["y"]})
        assert " | " in result

    def test_joins_words_in_category_with_comma(self):
        result = utils.format_tags({"10": ["alpha", "beta"]})
        assert "alpha, beta" in result

    def test_handles_non_digit_category_key(self):
        result = utils.format_tags({"high": ["z"], "5": ["a"]})
        assert result.index("5") < result.index("high")

    def test_empty_matches_returns_just_prefix(self):
        assert utils.format_tags({}) == "Tags: "

    def test_includes_priority_emoji_per_category(self):
        result = utils.format_tags({"10": ["x"]})
        assert "\u26ab" in result

    def test_multiple_categories_get_distinct_emoji(self):
        result = utils.format_tags({"10": ["x"], "1": ["y"]})
        assert "\u26ab" in result
        assert "\U0001f7e1" in result


class TestGetHighestPriorityDirect:
    def test_returns_zero_for_empty(self):
        assert utils.get_highest_priority({}) == 0

    def test_returns_zero_for_none(self):
        assert utils.get_highest_priority(None) == 0

    def test_returns_max_numeric_key(self):
        assert utils.get_highest_priority({"3": ["a"], "10": ["b"]}) == 10

    def test_ignores_non_numeric_keys(self):
        assert utils.get_highest_priority({"high": ["a"], "3": ["b"]}) == 3


class TestPriorityEmojiDirect:
    def test_invalid_level_returns_white_circle(self):
        assert utils.priority_emoji("abc") == "\u26aa"

    def test_none_returns_white_circle(self):
        assert utils.priority_emoji(None) == "\u26aa"
