import io
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
