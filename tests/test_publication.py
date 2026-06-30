import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_news.app import publish_run_outputs
from daily_news.observability import RunMetrics
from daily_news.storage import DailyNewsStore


class PublicationSafetyTests(unittest.TestCase):
    def test_publication_failure_restores_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = root / "reports"
            article_dir = root / "articles"
            review_dir = root / "reviews"
            report_dir.mkdir()
            article_dir.mkdir()
            report_path = report_dir / "2026-06-30.md"
            article_path = article_dir / "2026-06-30.md"
            report_path.write_text("old report", encoding="utf-8")
            article_path.write_text("old article", encoding="utf-8")

            with DailyNewsStore(root / "daily-news.sqlite3") as store:
                run_id = store.start_run("2026-06-30", "raw")
                metrics = RunMetrics("2026-06-30")
                with (
                    patch("daily_news.app.REPORT_DIR", report_dir),
                    patch("daily_news.app.ARTICLE_DIR", article_dir),
                    patch("daily_news.app.REVIEW_DIR", review_dir),
                    patch("daily_news.app.OBSIDIAN_VAULT_DIR", ""),
                    patch(
                        "daily_news.app.write_review_outputs",
                        side_effect=OSError("disk full"),
                    ),
                ):
                    with self.assertRaises(OSError):
                        publish_run_outputs(
                            store,
                            run_id,
                            "2026-06-30",
                            "# new report",
                            "# new article",
                            metrics,
                            time.monotonic(),
                        )
                document_count = store.connection.execute(
                    "SELECT COUNT(*) FROM report_documents WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                quality_count = store.connection.execute(
                    "SELECT COUNT(*) FROM quality_snapshots WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                metrics_count = store.connection.execute(
                    "SELECT COUNT(*) FROM run_metrics WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]

            self.assertEqual(report_path.read_text(encoding="utf-8"), "old report")
            self.assertEqual(article_path.read_text(encoding="utf-8"), "old article")
            self.assertFalse((review_dir / "2026-06-30.md").exists())
            self.assertEqual(document_count, 0)
            self.assertEqual(quality_count, 0)
            self.assertEqual(metrics_count, 0)

    def test_review_entries_ignore_failed_publications(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with DailyNewsStore(root / "daily-news.sqlite3") as store:
                run_id = store.start_run("2026-06-30", "raw")
                markdown = """# 日报

## 重点主题

### 1. Failed item
- 来源：[source](https://example.com/failed)
"""
                store.record_publication(
                    run_id,
                    "2026-06-30",
                    "daily-report",
                    root / "failed.md",
                    markdown,
                )
                store.finish_run(run_id, "failed", error="publication failed")

                self.assertEqual(store.review_entries("2026-06-30"), [])


if __name__ == "__main__":
    unittest.main()
