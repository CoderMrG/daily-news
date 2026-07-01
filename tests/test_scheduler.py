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
        self.assertEqual(payload["ProgramArguments"][:2], ["/bin/zsh", "-c"])
        launch_command = payload["ProgramArguments"][-1]
        self.assertIn("/Applications/Ghostty.app", launch_command)
        self.assertIn("run_scheduled.sh", launch_command)
        self.assertIn("scheduled.started", launch_command)
        self.assertIn("/bin/sleep 30", launch_command)

    def test_schedule_time_validation(self) -> None:
        validate_time(0, 0)
        validate_time(23, 59)
        with self.assertRaises(ValueError):
            validate_time(24, 0)
        with self.assertRaises(ValueError):
            validate_time(8, 60)

    def test_scheduled_script_sends_health_notification(self) -> None:
        script = (
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "run_scheduled.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("main.py health", script)
        self.assertIn("--notify", script)
        self.assertIn(".venv/bin/python", script)
        self.assertIn("stat -f%z", script)
        self.assertIn("5242880", script)
        self.assertIn("scheduled.started", script)


if __name__ == "__main__":
    unittest.main()
