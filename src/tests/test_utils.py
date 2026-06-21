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


class TestPriorityBucket:
    def test_critical(self):
        assert utils._priority_bucket("10") == "critical"

    def test_low(self):
        assert utils._priority_bucket("1") == "low"

    def test_non_numeric_returns_none(self):
        assert utils._priority_bucket("abc") is None
