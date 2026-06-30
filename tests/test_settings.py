import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_news.settings import load_config, load_env_file


class SettingsTests(unittest.TestCase):
    def test_env_file_loads_values_without_overriding_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "TEST_DAILY_NEWS_NEW='from-file'\n"
                "TEST_DAILY_NEWS_EXISTING=from-file\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"TEST_DAILY_NEWS_EXISTING": "from-environment"},
                clear=False,
            ):
                os.environ.pop("TEST_DAILY_NEWS_NEW", None)
                load_env_file(path)
                self.assertEqual(os.environ["TEST_DAILY_NEWS_NEW"], "from-file")
                self.assertEqual(
                    os.environ["TEST_DAILY_NEWS_EXISTING"],
                    "from-environment",
                )
                os.environ.pop("TEST_DAILY_NEWS_NEW", None)

    def test_invalid_env_line_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("NOT_VALID\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_env_file(path)

    def test_invalid_json_config_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily_news.json"
            path.write_text("{invalid", encoding="utf-8")
            with patch("daily_news.settings.CONFIG_PATH", path):
                with self.assertRaises(RuntimeError):
                    load_config()


if __name__ == "__main__":
    unittest.main()
