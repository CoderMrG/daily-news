import unittest

from daily_news.cli import success_streak


class CliTests(unittest.TestCase):
    def test_success_streak_uses_latest_contiguous_dates(self) -> None:
        self.assertEqual(
            success_streak(
                [
                    "2026-06-30",
                    "2026-06-29",
                    "2026-06-28",
                    "2026-06-25",
                ]
            ),
            3,
        )
        self.assertEqual(success_streak([]), 0)


if __name__ == "__main__":
    unittest.main()
