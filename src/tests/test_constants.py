from src.scripts import constants


class TestConstants:
    def test_db_path_default(self):
        assert constants.DB_PATH is not None
        assert isinstance(constants.DB_PATH, str)

    def test_keywords_file_default(self):
        assert constants.KEYWORDS_FILE is not None
        assert isinstance(constants.KEYWORDS_FILE, str)
        assert constants.KEYWORDS_FILE.endswith("keywords.json")

    def test_keywords_example_file_default(self):
        assert constants.KEYWORDS_EXAMPLE_FILE is not None
        assert isinstance(constants.KEYWORDS_EXAMPLE_FILE, str)
        assert constants.KEYWORDS_EXAMPLE_FILE.endswith("keywords.example.json")

    def test_secret_key_path_default(self):
        assert constants.SECRET_KEY_PATH is not None
        assert isinstance(constants.SECRET_KEY_PATH, str)
        assert constants.SECRET_KEY_PATH.endswith(".secret.key")

    def test_src_dir_resolves_to_parent_of_scripts(self):
        assert constants._DEFAULT_DATA_DIR.parent.name == "src"

    def test_data_dir_is_under_src(self):
        assert constants._DEFAULT_DATA_DIR.parent.name == "src"
        assert constants._DEFAULT_DATA_DIR.name == "data"

    def test_data_dir_env_override(self, monkeypatch, tmp_path):
        import importlib

        monkeypatch.setenv("INBOX_LENS_DATA_DIR", str(tmp_path))
        importlib.reload(constants)
        try:
            assert constants.DB_PATH == str(tmp_path / "emails.db")
            assert constants.SESSION_SECRET_PATH == str(tmp_path / ".session.key")
        finally:
            monkeypatch.delenv("INBOX_LENS_DATA_DIR", raising=False)
            importlib.reload(constants)
