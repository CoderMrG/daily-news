import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_news.settings import (
    PROJECT_ROOT,
    env_flag,
    env_int,
    load_config,
    load_env_file,
)


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

    def test_paths_are_stable_outside_project_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(PROJECT_ROOT)
            environment["DAILY_NEWS_DATA_DIR"] = "data"
            environment.pop("DAILY_NEWS_DB_PATH", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from daily_news.settings import DB_PATH, REPORT_DIR, RUN_LOCK_PATH;"
                        "print(DB_PATH); print(REPORT_DIR); print(RUN_LOCK_PATH)"
                    ),
                ],
                cwd=directory,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
        paths = completed.stdout.splitlines()
        self.assertEqual(paths[0], str(PROJECT_ROOT / "data/db/daily_news.sqlite3"))
        self.assertEqual(paths[1], str(PROJECT_ROOT / "data/reports"))
        self.assertEqual(paths[2], str(PROJECT_ROOT / "data/run/daily-news.lock"))

    def test_invalid_typed_environment_values_fail_fast(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TEST_DAILY_NEWS_INTEGER": "not-a-number",
                "TEST_DAILY_NEWS_BOOLEAN": "sometimes",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                env_int("TEST_DAILY_NEWS_INTEGER", 1)
            with self.assertRaises(RuntimeError):
                env_flag("TEST_DAILY_NEWS_BOOLEAN", True)

    def test_invalid_translation_provider_fails_during_startup(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PROJECT_ROOT)
        environment["DAILY_NEWS_TRANSLATION_PROVIDER"] = "misspelled-provider"
        completed = subprocess.run(
            [sys.executable, "-c", "import daily_news.settings"],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Invalid DAILY_NEWS_TRANSLATION_PROVIDER", completed.stderr)


if __name__ == "__main__":
    unittest.main()
