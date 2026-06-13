import importlib
from unittest.mock import patch

from src.scripts import constants


class TestConstants:
    def test_db_path_default(self):
        assert constants.DB_PATH is not None
        assert isinstance(constants.DB_PATH, str)

    def test_keywords_file_default(self):
        assert constants.KEYWORDS_FILE is not None
        assert isinstance(constants.KEYWORDS_FILE, str)

    def test_db_path_env_override(self):
        with patch.dict("os.environ", {"DB_PATH": "/custom/path.db"}):
            importlib.reload(constants)
            assert constants.DB_PATH == "/custom/path.db"
        importlib.reload(constants)

    def test_keywords_file_env_override(self):
        with patch.dict("os.environ", {"KEYWORDS_FILE": "/custom/kw.json"}):
            importlib.reload(constants)
            assert constants.KEYWORDS_FILE == "/custom/kw.json"
        importlib.reload(constants)
