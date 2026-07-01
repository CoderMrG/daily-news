import json
import unittest

from daily_news.models import CommandResult, Enrichment, SourceItem
from daily_news.observability import (
    RunMetrics,
    applescript_string,
    format_health_row,
    health_notification,
)


class ObservabilityTests(unittest.TestCase):
    def test_command_and_translation_metrics(self) -> None:
        metrics = RunMetrics("2026-06-30")
        metrics.record_commands(
            [
                CommandResult("Reddit search: AI", [], True, "", "", 2.5),
                CommandResult("Reddit read: abc", [], False, "", "", 1.5),
                CommandResult("Twitter search: AI", [], True, "", "", 3.0),
                CommandResult("Agent-Reach doctor", [], True, "", "", 1.0),
            ]
        )
        items = [
            SourceItem("one", "Reddit", "post"),
            SourceItem("two", "X/Twitter", "tweet"),
        ]
        metrics.record_translations(
            items,
            {
                "one": Enrichment(zh_translation="成功"),
                "two": Enrichment(error="timeout"),
            },
            4.0,
        )

        self.assertEqual(metrics.command_succeeded, 3)
        self.assertEqual(metrics.reddit_succeeded, 1)
        self.assertEqual(metrics.reddit_seconds, 4.0)
        self.assertEqual(metrics.x_seconds, 3.0)
        self.assertEqual(metrics.translation_succeeded, 1)

    def test_health_summary_and_failure_notification(self) -> None:
        row = {
            "report_date": "2026-06-30",
            "status": "success",
            "total_seconds": 125,
            "reddit_succeeded": 6,
            "reddit_total": 7,
            "x_succeeded": 4,
            "x_total": 4,
            "translation_succeeded": 9,
            "translation_total": 10,
            "articles_fetched": 3,
            "article_candidates": 5,
            "collection_seconds": 80,
            "reddit_seconds": 50,
            "x_seconds": 25,
            "article_seconds": 10,
            "translation_seconds": 20,
            "render_seconds": 40,
            "publish_seconds": 5,
            "failed_stage": "",
        }
        summary = format_health_row(row)
        self.assertIn("2分05秒", summary)
        self.assertIn("Reddit 6/7", summary)
        self.assertIn("采集 1分20秒", summary)

        title, message = health_notification(
            {
                "status": "failed",
                "failed_stage": "文章渲染",
                "error": "RuntimeError: translation failed",
            },
            "2026-06-30",
        )
        self.assertIn("失败", title)
        self.assertIn("文章渲染", message)

    def test_applescript_string_escapes_content(self) -> None:
        self.assertEqual(
            applescript_string('a "quote"\\path\nnext'),
            '"a \\"quote\\"\\\\path next"',
        )

    def test_degraded_run_uses_warning_notification(self) -> None:
        row = {
            "status": "degraded",
            "warnings_json": json.dumps(["数据库备份失败：disk unavailable"]),
            "error": "",
        }
        title, message = health_notification(row, "2026-07-01")
        self.assertIn("有警告", title)
        self.assertIn("数据库备份失败", message)


if __name__ == "__main__":
    unittest.main()
