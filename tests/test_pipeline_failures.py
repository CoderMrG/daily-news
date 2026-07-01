import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_news.app import run_pipeline


class PipelineFailureTests(unittest.TestCase):
    def test_backup_failure_marks_run_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "db" / "daily_news.sqlite3"
            report = """# 技术社区情报日报 - 2026-06-30

## 今日结论

- 原始候选条目：1
- 入选 Reddit 帖：1
- 重点议题：1
- X 资讯/信号：0
- 短讯/观察：0
"""
            article = """# 高质量文章 - 2026-06-30

- 候选文章：0
- 已读取正文：0
"""
            with (
                patch("daily_news.app.DB_PATH", db_path),
                patch("daily_news.app.REPORT_DIR", root / "reports"),
                patch("daily_news.app.ARTICLE_DIR", root / "articles"),
                patch("daily_news.app.REVIEW_DIR", root / "reviews"),
                patch("daily_news.app.RAW_DIR", root / "raw"),
                patch("daily_news.app.OBSIDIAN_VAULT_DIR", ""),
                patch("daily_news.app.collect", return_value=([], [])),
                patch("daily_news.app.validate_collection_health"),
                patch("daily_news.app.render_report", return_value=report),
                patch("daily_news.app.render_article_report", return_value=article),
                patch(
                    "daily_news.app.DailyNewsStore.backup_database",
                    side_effect=OSError("backup disk unavailable"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "backup disk unavailable"):
                    run_pipeline("2026-06-30")

            with sqlite3.connect(db_path) as connection:
                run_row = connection.execute(
                    """
                    SELECT status, error
                    FROM report_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                metrics_row = connection.execute(
                    """
                    SELECT failed_stage
                    FROM run_metrics
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()

        self.assertEqual(run_row[0], "failed")
        self.assertIn("backup disk unavailable", run_row[1])
        self.assertEqual(metrics_row[0], "数据库备份")

    def test_keyboard_interrupt_marks_run_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "db" / "daily_news.sqlite3"
            with (
                patch("daily_news.app.DB_PATH", db_path),
                patch("daily_news.app.REPORT_DIR", root / "reports"),
                patch("daily_news.app.ARTICLE_DIR", root / "articles"),
                patch("daily_news.app.REVIEW_DIR", root / "reviews"),
                patch("daily_news.app.OBSIDIAN_VAULT_DIR", ""),
                patch("daily_news.app.collect", side_effect=KeyboardInterrupt()),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_pipeline("2026-07-01")

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT r.status, m.failed_stage
                    FROM report_runs r
                    LEFT JOIN run_metrics m ON m.run_id = r.id
                    ORDER BY r.id DESC
                    LIMIT 1
                    """
                ).fetchone()

        self.assertEqual(row[0], "interrupted")
        self.assertEqual(row[1], "采集")


if __name__ == "__main__":
    unittest.main()
