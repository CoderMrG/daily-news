import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_news.app import run_pipeline


class PipelineFailureTests(unittest.TestCase):
    def test_backup_failure_marks_run_degraded(self) -> None:
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
                run_pipeline("2026-06-30")

            with sqlite3.connect(db_path) as connection:
                run_row = connection.execute(
                    """
                    SELECT status, error, warnings_json, report_path, article_path
                    FROM report_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                report_exists = Path(run_row[3]).is_file()
                article_exists = Path(run_row[4]).is_file()

        self.assertEqual(run_row[0], "degraded")
        self.assertIn("backup disk unavailable", run_row[1])
        self.assertIn("数据库备份失败", json.loads(run_row[2])[0])
        self.assertTrue(report_exists)
        self.assertTrue(article_exists)

    def test_cleanup_failure_marks_run_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "db" / "daily_news.sqlite3"
            report = """# 技术社区情报日报 - 2026-07-01
- 原始候选条目：1
- 入选 Reddit 帖：1
- 重点议题：1
- X 资讯/信号：0
- 短讯/观察：0
"""
            article = """# 高质量文章 - 2026-07-01
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
                    "daily_news.app.cleanup_dated_directories",
                    side_effect=OSError("permission denied"),
                ),
            ):
                run_pipeline("2026-07-01")

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT status, warnings_json
                    FROM report_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()

        self.assertEqual(row[0], "degraded")
        self.assertIn("运行清理失败", json.loads(row[1])[0])

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
