import json
import tempfile
import unittest
from pathlib import Path

from daily_news.full_test import FullTestRunner, latest_raw_date, tail_text


class FullTestTests(unittest.TestCase):
    def test_latest_raw_date_uses_commands_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            (raw_dir / "2026-06-28").mkdir()
            (raw_dir / "2026-06-28" / "commands.json").write_text("[]")
            (raw_dir / "2026-06-30").mkdir()
            (raw_dir / "2026-06-30" / "commands.json").write_text("[]")
            (raw_dir / "not-a-date").mkdir()
            (raw_dir / "not-a-date" / "commands.json").write_text("[]")

            self.assertEqual(latest_raw_date(raw_dir), "2026-06-30")

    def test_sanitized_config_removes_local_paths_and_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "daily_news.json").write_text(
                json.dumps(
                    {
                        "topics": ["AI"],
                        "obsidian_vault_dir": "/private/vault",
                        "api_key": "secret",
                    }
                )
            )
            runner = FullTestRunner(root, root / "reports")

            self.assertEqual(runner.sanitized_config(), {"topics": ["AI"]})

    def test_tail_text_compacts_and_limits_output(self) -> None:
        self.assertEqual(tail_text("a\n b", limit=3), "a b")
        self.assertEqual(tail_text("123456", limit=4), "3456")


if __name__ == "__main__":
    unittest.main()
