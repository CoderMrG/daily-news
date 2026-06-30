import tempfile
import unittest
from pathlib import Path

from daily_news.scheduler import build_launch_agent, validate_time


class SchedulerTests(unittest.TestCase):
    def test_launch_agent_uses_login_shell_and_calendar_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = build_launch_agent(root, 8, 30)

        self.assertEqual(payload["Label"], "com.codermrg.daily-news")
        self.assertEqual(
            payload["StartCalendarInterval"],
            {"Hour": 8, "Minute": 30},
        )
        self.assertEqual(payload["ProgramArguments"][:3], ["/usr/bin/open", "-na", "/Applications/Ghostty.app"])
        self.assertIn("run_scheduled.sh", payload["ProgramArguments"][-1])

    def test_schedule_time_validation(self) -> None:
        validate_time(0, 0)
        validate_time(23, 59)
        with self.assertRaises(ValueError):
            validate_time(24, 0)
        with self.assertRaises(ValueError):
            validate_time(8, 60)


if __name__ == "__main__":
    unittest.main()
