from src.scripts import constants


class TestConstants:
    def test_db_path_default(self):
        assert constants.DB_PATH is not None
        assert isinstance(constants.DB_PATH, str)

    def test_keywords_file_default(self):
        assert constants.KEYWORDS_FILE is not None
        assert isinstance(constants.KEYWORDS_FILE, str)

    def test_secret_key_path_default(self):
        assert constants.SECRET_KEY_PATH is not None
        assert isinstance(constants.SECRET_KEY_PATH, str)
        assert constants.SECRET_KEY_PATH.endswith(".secret.key")

    def test_src_dir_resolves_to_parent_of_scripts(self):
        assert constants._SRC_DIR.name == "src"

    def test_data_dir_is_under_src(self):
        assert constants._DATA_DIR.parent == constants._SRC_DIR
        assert constants._DATA_DIR.name == "data"
