import io
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_news.app import read_limited_response, run


class IOLimitTests(unittest.TestCase):
    def test_http_response_limit_rejects_oversized_payload(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exceeded 4 bytes"):
            read_limited_response(io.BytesIO(b"12345"), limit=4)

    def test_command_output_limit_marks_command_failed(self) -> None:
        with patch("daily_news.app.MAX_COMMAND_OUTPUT_BYTES", 64):
            result = run(
                "large output",
                [sys.executable, "-c", "print('x' * 256)"],
                timeout_seconds=5,
            )

        self.assertFalse(result.ok)
        self.assertEqual(len(result.stdout), 64)
        self.assertIn("Output exceeded 64 bytes", result.stderr)

    def test_timeout_terminates_descendant_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "child.pid"
            code = (
                "import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c',"
                "'import time; time.sleep(30)']); "
                f"open({str(pid_path)!r},'w').write(str(child.pid)); "
                "time.sleep(30)"
            )
            result = run(
                "timeout process group",
                [sys.executable, "-c", code],
                timeout_seconds=0.5,
            )
            child_pid = int(pid_path.read_text(encoding="utf-8"))

            alive = True
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    alive = False
                    break
                time.sleep(0.05)
            if alive:
                os.kill(child_pid, signal.SIGKILL)

        self.assertFalse(result.ok)
        self.assertFalse(alive, "timed-out command left a descendant process alive")


if __name__ == "__main__":
    unittest.main()
