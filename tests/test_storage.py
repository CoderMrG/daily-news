import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from daily_news.app import historical_source_event_keys
from daily_news.models import ArticleCandidate, Enrichment, SourceItem
from daily_news.observability import RunMetrics
from daily_news.storage import MIGRATIONS, DailyNewsStore


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = DailyNewsStore(self.root / "daily-news.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_migrations_and_run_lifecycle(self) -> None:
        run_id = self.store.start_run("2026-06-30", "raw")
        self.store.finish_run(run_id, "success", source_count=3)

        row = self.store.connection.execute(
            "SELECT status, source_count FROM report_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual((row["status"], row["source_count"]), ("success", 3))
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0],
            len(MIGRATIONS),
        )

    def test_source_article_and_translation_upserts(self) -> None:
        run_id = self.store.start_run("2026-06-30", "raw")
        item = SourceItem(
            item_id="reddit-abc123",
            platform="Reddit",
            kind="post",
            title="Initial title",
            text="Technical body",
            author="builder",
            url="https://www.reddit.com/r/LocalLLaMA/comments/abc123/test/",
            score=10,
            created_at=dt.datetime(2026, 6, 30, tzinfo=dt.timezone.utc),
            raw={"id": "abc123"},
        )
        self.store.upsert_source_items(run_id, "2026-06-30", [item])
        item.score = 42
        self.store.upsert_source_items(run_id, "2026-06-30", [item])

        candidate = ArticleCandidate(
            item=item,
            article_url="https://example.com/report",
            title="Example report",
            score=100,
            reason="research",
            article_text="Full report text",
        )
        self.store.record_articles("2026-06-30", [candidate])
        self.store.record_translations(
            [item],
            {
                item.item_id: Enrichment(
                    zh_title="示例报告",
                    zh_translation="技术正文",
                    translated_by="glm-5.2",
                )
            },
            "anthropic",
            "glm-5.2",
        )

        counts = self.store.table_counts()
        self.assertEqual(counts["source_items"], 1)
        self.assertEqual(counts["articles"], 1)
        self.assertEqual(counts["translations"], 1)
        self.assertEqual(
            self.store.connection.execute(
                "SELECT score FROM source_items"
            ).fetchone()[0],
            42,
        )

    def test_publication_history_is_queryable(self) -> None:
        run_id = self.store.start_run("2026-06-29", "import")
        markdown = """
# 技术社区情报日报 - 2026-06-29

## 重点主题

### 1. Example

- 来源：[Reddit u/test](https://www.reddit.com/r/LocalLLaMA/comments/abc123/example/)
- 来源：[X @OpenAI](https://x.com/OpenAI/status/1234567890123456789)
"""
        self.store.record_publication(
            run_id,
            "2026-06-29",
            "daily-report",
            "report.md",
            markdown,
        )
        self.store.finish_run(run_id, "imported")

        history = self.store.historical_documents(
            "2026-06-30",
            7,
            ("daily-report",),
        )
        self.assertEqual(history, [markdown])
        entries = self.store.connection.execute(
            "SELECT event_key FROM report_entries ORDER BY position"
        ).fetchall()
        self.assertEqual(
            [row["event_key"] for row in entries],
            ["reddit:abc123", "twitter:1234567890123456789"],
        )
        self.assertEqual(
            historical_source_event_keys("2026-06-30", self.store),
            {"reddit:abc123", "twitter:1234567890123456789"},
        )

        failed_run = self.store.start_run("2026-06-29", "raw")
        self.store.record_publication(
            failed_run,
            "2026-06-29",
            "daily-report",
            "failed.md",
            "# Failed replacement",
        )
        self.store.finish_run(failed_run, "failed", error="publish failed")
        self.assertEqual(
            self.store.historical_documents(
                "2026-06-30",
                7,
                ("daily-report",),
            ),
            [markdown],
        )
        replacement_run = self.store.start_run("2026-06-29", "raw")
        replacement = "# Replacement report"
        self.store.record_publication(
            replacement_run,
            "2026-06-29",
            "daily-report",
            "replacement.md",
            replacement,
        )
        self.store.finish_run(replacement_run, "success")
        self.assertEqual(
            self.store.historical_documents(
                "2026-06-30",
                7,
                ("daily-report",),
            ),
            [replacement],
        )

    def test_markdown_backfill_runs_once(self) -> None:
        report_dir = self.root / "reports"
        article_dir = self.root / "articles"
        report_dir.mkdir()
        article_dir.mkdir()
        (report_dir / "2026-06-29.md").write_text("# Report", encoding="utf-8")
        (article_dir / "2026-06-29.md").write_text("# Articles", encoding="utf-8")

        self.assertEqual(
            self.store.backfill_markdown_history(report_dir, article_dir),
            2,
        )
        self.assertEqual(
            self.store.backfill_markdown_history(report_dir, article_dir),
            0,
        )
        self.assertEqual(self.store.table_counts()["report_documents"], 2)

    def test_quality_snapshot_metrics(self) -> None:
        run_id = self.store.start_run("2026-06-30", "raw")
        report = """
- 原始候选条目：100
- 入选 Reddit 帖：3
- 重点议题：2
- X 资讯/信号：5
- 短讯/观察：1
- 历史去重：过滤 4 个重复主题，保留 0 个延续讨论
- 讨论门槛：过滤 6 个无热门评论主题
- 代表性讨论线程：7
- 线程内精选回复：8
"""
        articles = """
- 候选文章：5
- 已读取正文：3
"""
        self.store.record_quality_snapshot(
            run_id,
            "2026-06-30",
            report,
            articles,
        )
        self.store.finish_run(run_id, "success")

        snapshot = self.store.recent_quality_snapshots(7)[0]
        self.assertEqual(snapshot["focus_topics"], 2)
        self.assertEqual(snapshot["duplicate_filtered"], 4)
        self.assertEqual(snapshot["articles_fetched"], 3)

    def test_run_metrics_are_queryable_for_health(self) -> None:
        run_id = self.store.start_run("2026-06-30", "collect")
        metrics = RunMetrics(
            "2026-06-30",
            collection_seconds=12.5,
            reddit_seconds=8.0,
            x_seconds=4.0,
            translation_seconds=2.0,
            total_seconds=18.0,
            command_total=10,
            command_succeeded=9,
            reddit_total=6,
            reddit_succeeded=6,
            x_total=3,
            x_succeeded=2,
            translation_total=5,
            translation_succeeded=4,
            article_candidates=3,
            articles_fetched=2,
        )
        self.store.record_run_metrics(run_id, metrics)
        self.store.finish_run(run_id, "success", source_count=20)

        row = self.store.recent_run_health(1)[0]
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["source_count"], 20)
        self.assertEqual(row["reddit_succeeded"], 6)
        self.assertEqual(row["translation_succeeded"], 4)
        self.assertEqual(self.store.table_counts()["run_metrics"], 1)

    def test_stale_runs_are_failed_and_database_is_backed_up(self) -> None:
        run_id = self.store.start_run("2026-06-30", "collect")
        self.store.connection.execute(
            """
            UPDATE report_runs
            SET started_at = '2020-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (run_id,),
        )
        self.store.connection.commit()

        self.assertEqual(self.store.fail_stale_runs(60), 1)
        row = self.store.connection.execute(
            "SELECT status, error FROM report_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("stale", row["error"].lower())

        backup = self.store.backup_database(
            self.root / "backups",
            "2026-06-30",
            retention_days=14,
        )
        self.assertTrue(backup.is_file())
        with DailyNewsStore(backup) as restored:
            self.assertEqual(
                restored.connection.execute("PRAGMA integrity_check").fetchone()[0],
                "ok",
            )

    def test_successful_run_requires_nonempty_output_files(self) -> None:
        run_id = self.store.start_run("2026-06-30", "collect")
        report = self.root / "report.md"
        article = self.root / "article.md"
        report.write_text("# Report", encoding="utf-8")
        article.write_text("# Article", encoding="utf-8")
        self.store.finish_run(
            run_id,
            "success",
            report_path=str(report),
            article_path=str(article),
        )
        self.assertTrue(self.store.has_successful_run("2026-06-30"))

        article.unlink()
        self.assertFalse(self.store.has_successful_run("2026-06-30"))

    def test_degraded_run_remains_queryable_as_valid_history(self) -> None:
        run_id = self.store.start_run("2026-06-30", "collect")
        report_path = self.root / "report.md"
        article_path = self.root / "article.md"
        report_path.write_text("# Report", encoding="utf-8")
        article_path.write_text("# Article", encoding="utf-8")
        markdown = """
# Report

## 重点主题

### 1. Example
- 来源：[Reddit](https://www.reddit.com/r/test/comments/abc123/example/)
"""
        self.store.record_publication(
            run_id,
            "2026-06-30",
            "daily-report",
            report_path,
            markdown,
        )
        self.store.record_quality_snapshot(
            run_id,
            "2026-06-30",
            "- 重点议题：1",
            "- 候选文章：0\n- 已读取正文：0",
        )
        self.store.finish_run(
            run_id,
            "degraded",
            report_path=str(report_path),
            article_path=str(article_path),
            error="backup failed",
            warnings=["数据库备份失败"],
        )

        self.assertTrue(self.store.has_successful_run("2026-06-30"))
        self.assertEqual(
            self.store.successful_run_dates(),
            ["2026-06-30"],
        )
        self.assertEqual(
            self.store.historical_documents(
                "2026-07-01",
                7,
                ("daily-report",),
            ),
            [markdown],
        )
        self.assertEqual(
            self.store.recent_quality_snapshots(1)[0]["focus_topics"],
            1,
        )
        self.assertEqual(len(self.store.review_entries("2026-06-30")), 1)
        health = self.store.recent_run_health(1)[0]
        self.assertEqual(health["status"], "degraded")
        self.assertIn("数据库备份失败", health["warnings_json"])

    def test_pending_migration_creates_database_backup(self) -> None:
        legacy_path = self.root / "legacy.sqlite3"
        self.store.backup_database(
            self.root,
            "legacy",
            retention_days=14,
        )
        self.assertEqual(legacy_path, self.root / "legacy.sqlite3")
        with sqlite3.connect(legacy_path) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE version = 4")
            connection.execute("ALTER TABLE report_runs DROP COLUMN warnings_json")

        with DailyNewsStore(legacy_path):
            pass

        backups = list((self.root / "backups").glob("pre-migration-v4-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as connection:
            versions = {
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                )
            }
        self.assertEqual(versions, {1, 2, 3})


if __name__ == "__main__":
    unittest.main()
