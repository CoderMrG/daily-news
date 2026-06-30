import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_news.models import CommandResult
from daily_news.runtime import (
    CollectionGuard,
    FileRollback,
    RunAlreadyActive,
    RunBudgetExceeded,
    RunLock,
    cleanup_dated_directories,
    remaining_timeout,
    reset_run_budget,
    start_run_budget,
)


class RuntimeSafetyTests(unittest.TestCase):
    def test_run_lock_rejects_second_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily-news.lock"
            with RunLock(path):
                with self.assertRaises(RunAlreadyActive):
                    with RunLock(path):
                        pass

    def test_collection_guard_opens_after_consecutive_failures(self) -> None:
        calls = 0

        def failing_runner(
            title: str,
            command: list[str],
            timeout: float,
        ) -> CommandResult:
            nonlocal calls
            calls += 1
            return CommandResult(title, command, False, "", "AUTH_REQUIRED")

        guard = CollectionGuard(failure_limit=3)
        results = [
            guard.execute("reddit", f"Reddit {index}", [], failing_runner, 10)
            for index in range(5)
        ]

        self.assertEqual(calls, 3)
        self.assertTrue(results[3].stderr.startswith("Skipped:"))
        self.assertTrue(results[4].stderr.startswith("Skipped:"))

    def test_file_rollback_restores_existing_and_removes_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing.md"
            created = root / "created.md"
            existing.write_text("before", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                with FileRollback([existing, created]):
                    existing.write_text("after", encoding="utf-8")
                    created.write_text("new", encoding="utf-8")
                    raise RuntimeError("publish failed")

            self.assertEqual(existing.read_text(encoding="utf-8"), "before")
            self.assertFalse(created.exists())

    def test_run_budget_stops_new_operations_after_deadline(self) -> None:
        token = start_run_budget(60)
        try:
            with patch("daily_news.runtime.time.monotonic", return_value=10**12):
                with self.assertRaises(RunBudgetExceeded):
                    remaining_timeout(120)
        finally:
            reset_run_budget(token)

    def test_raw_retention_only_removes_expired_dated_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expired = root / "2026-05-01"
            retained = root / "2026-06-15"
            unrelated = root / "cache"
            for path in (expired, retained, unrelated):
                path.mkdir()

            removed = cleanup_dated_directories(
                root,
                30,
                today=dt.date(2026, 6, 30),
            )

            self.assertEqual(removed, [expired])
            self.assertFalse(expired.exists())
            self.assertTrue(retained.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
