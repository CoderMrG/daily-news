import tempfile
import unittest
from pathlib import Path

from daily_news.reviews import (
    build_review_markdown,
    merge_review_markdown,
    parse_review_feedback,
    sync_review_feedback,
    write_review_outputs,
)
from daily_news.storage import DailyNewsStore


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = DailyNewsStore(self.root / "daily-news.sqlite3")
        self.entries = [
            {
                "entry_key": "a" * 20,
                "document_type": "daily-report",
                "section": "重点主题",
                "title": "本地模型推理",
                "source_url": "https://example.com/topic",
            },
            {
                "entry_key": "b" * 20,
                "document_type": "article-digest",
                "section": "必读",
                "title": "Agent 评测报告",
                "source_url": "https://example.com/article",
            },
        ]

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_review_round_trip_and_feedback_sync(self) -> None:
        markdown = build_review_markdown("2026-06-30", self.entries)
        edited = markdown.replace(
            "评价：待评价",
            "评价：有用",
            1,
        ).replace(
            "备注：",
            "备注：重点跟进",
            1,
        )
        rows = parse_review_feedback(edited)
        self.assertEqual(
            rows,
            [
                {
                    "entry_key": "a" * 20,
                    "document_type": "daily-report",
                    "rating": "useful",
                    "note": "重点跟进",
                }
            ],
        )

        review_path = self.root / "reviews" / "2026-06-30.md"
        review_path.parent.mkdir()
        review_path.write_text(edited, encoding="utf-8")
        self.assertEqual(sync_review_feedback(self.store, [review_path]), 1)
        self.assertEqual(self.store.feedback_summary(), {"useful": 1})

    def test_existing_obsidian_review_is_not_overwritten(self) -> None:
        markdown = build_review_markdown("2026-06-30", self.entries)
        local_dir = self.root / "local-reviews"
        vault = self.root / "vault"
        obsidian_path = vault / "Daily News" / "reviews" / "2026-06-30.md"
        obsidian_path.parent.mkdir(parents=True)
        obsidian_path.write_text("user feedback", encoding="utf-8")

        paths = write_review_outputs(
            "2026-06-30",
            markdown,
            local_dir,
            str(vault),
            "Daily News",
        )
        self.assertEqual(obsidian_path.read_text(encoding="utf-8"), "user feedback")
        self.assertEqual(
            (local_dir / "2026-06-30.md").read_text(encoding="utf-8"),
            "user feedback",
        )
        self.assertEqual(paths[-1], obsidian_path)

    def test_rerun_updates_entries_and_preserves_feedback(self) -> None:
        existing = build_review_markdown("2026-06-30", self.entries).replace(
            "评价：待评价",
            "评价：跟进",
            1,
        ).replace(
            "备注：",
            "备注：下周复核",
            1,
        )
        updated_entries = [self.entries[0]]
        generated = build_review_markdown("2026-06-30", updated_entries)
        merged = merge_review_markdown(generated, existing)

        self.assertIn("评价：跟进", merged)
        self.assertIn("备注：下周复核", merged)
        self.assertNotIn("Agent 评测报告", merged)


if __name__ == "__main__":
    unittest.main()
