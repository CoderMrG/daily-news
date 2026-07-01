import unittest
import json
from unittest.mock import patch

from daily_news.app import (
    agent_reach_doctor_error,
    discussion_information_score,
    event_family_key,
    group_article_candidates,
    group_key,
    informative_discussion_item,
    is_fresh_article_item,
    is_fresh_item,
    same_x_sequence,
    source_key,
    validate_collection_health,
    validate_output_quality,
    validate_translation_coverage,
)
from daily_news.models import ArticleCandidate, CommandResult, Enrichment, SourceItem


class QualityRuleTests(unittest.TestCase):
    def test_twitter_url_variants_share_source_key(self) -> None:
        first = SourceItem(
            item_id="twitter-123",
            platform="X/Twitter",
            kind="tweet",
            url="https://x.com/OpenAI/status/123",
            raw={"id": "123"},
        )
        second = SourceItem(
            item_id="twitter-123",
            platform="X/Twitter",
            kind="tweet",
            url="https://x.com/i/status/123",
            raw={"id": "123"},
        )
        self.assertEqual(source_key(first), source_key(second))

    def test_model_release_thread_uses_one_event_family(self) -> None:
        root = SourceItem(
            item_id="twitter-1",
            platform="X/Twitter",
            kind="tweet",
            text="Introducing GPT-5.6 Sol, Terra, and Luna.",
            author="OpenAI",
            raw={"id": "1"},
        )
        followup = SourceItem(
            item_id="twitter-2",
            platform="X/Twitter",
            kind="tweet",
            text="GPT-5.6 Sol improves long-horizon cybersecurity tasks.",
            author="OpenAI",
            raw={"id": "2"},
        )
        self.assertEqual(event_family_key(root), event_family_key(followup))

    def test_unicode_hyphen_release_thread_is_deduplicated(self) -> None:
        root = SourceItem(
            item_id="twitter-2070555272230384038",
            platform="X/Twitter",
            kind="tweet",
            text="Introducing GPT-5.6 Sol.",
            author="OpenAI",
            raw={"id": "2070555272230384038"},
        )
        followup = SourceItem(
            item_id="twitter-2070555280052826429",
            platform="X/Twitter",
            kind="tweet",
            text="GPT‑5.6 Sol launches with a stronger safety stack.",
            author="OpenAI",
            raw={"id": "2070555280052826429"},
        )
        self.assertEqual(event_family_key(root), event_family_key(followup))
        self.assertTrue(same_x_sequence(root, followup))

    def test_product_versions_share_one_release_family(self) -> None:
        announcement = SourceItem(
            item_id="twitter-1",
            platform="X/Twitter",
            kind="tweet",
            text="Ornith-1.0 35B is strong for local coding.",
            author="AlexFinn",
            raw={"id": "1"},
        )
        official = SourceItem(
            item_id="twitter-2",
            platform="X/Twitter",
            kind="tweet",
            text="Run Ornith with Ollama.",
            author="ollama",
            raw={"id": "2"},
        )
        self.assertEqual(event_family_key(announcement).rsplit(":", 1)[-1], "ornith")
        self.assertEqual(event_family_key(official).rsplit(":", 1)[-1], "ornith")

    def test_article_links_from_one_event_are_grouped(self) -> None:
        item = SourceItem(
            item_id="twitter-123",
            platform="X/Twitter",
            kind="tweet",
            text="Qwen-AgentWorld release",
            author="Alibaba_Qwen",
            raw={"id": "123"},
        )
        candidates = [
            ArticleCandidate(item, "https://example.com/blog", "Blog", 100, "reason"),
            ArticleCandidate(item, "https://example.com/paper", "Paper", 90, "reason"),
        ]
        grouped = group_article_candidates(candidates)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].related_links, [("Paper", "https://example.com/paper")])

    def test_support_request_is_not_informative(self) -> None:
        item = SourceItem(
            item_id="reply-1",
            platform="X/Twitter",
            kind="x-reply",
            text="Please it is not opening in my browser, can anyone help me out?",
        )
        self.assertLess(discussion_information_score(item), 0)
        self.assertFalse(informative_discussion_item(item))

    def test_acquisition_is_not_classified_as_agent_workflow(self) -> None:
        item = SourceItem(
            item_id="reddit-abc",
            platform="Reddit",
            kind="post",
            title="SpaceX buys AI coding startup Cursor for $60 billion",
            raw={"id": "abc"},
        )
        self.assertEqual(group_key(item), "ai-industry-deals")

    def test_translation_failure_threshold_blocks_output(self) -> None:
        items = [
            SourceItem(item_id=f"item-{index}", platform="Reddit", kind="post")
            for index in range(5)
        ]
        enrichments = {
            item.item_id: Enrichment(zh_translation="完成")
            for item in items[:3]
        }
        with patch("daily_news.app.TRANSLATION_PROVIDER", "anthropic"):
            with self.assertRaises(RuntimeError):
                validate_translation_coverage(items, enrichments, "测试日报")

    def test_agent_reach_doctor_requires_opencli_backends(self) -> None:
        healthy = CommandResult(
            "Agent-Reach doctor",
            [],
            True,
            json.dumps(
                {
                    "reddit": {"status": "ok", "active_backend": "OpenCLI"},
                    "twitter": {"status": "ok", "active_backend": "OpenCLI"},
                }
            ),
            "",
        )
        self.assertEqual(agent_reach_doctor_error(healthy), "")

        wrong_backend = CommandResult(
            "Agent-Reach doctor",
            [],
            True,
            json.dumps(
                {
                    "reddit": {"status": "ok", "active_backend": "rdt-cli"},
                    "twitter": {"status": "ok", "active_backend": "OpenCLI"},
                }
            ),
            "",
        )
        self.assertIn("requires OpenCLI", agent_reach_doctor_error(wrong_backend))

    def test_collection_gate_rejects_total_platform_failure(self) -> None:
        results = [
            CommandResult(
                "Agent-Reach doctor",
                [],
                False,
                "",
                "AUTH_REQUIRED",
            ),
            CommandResult("Reddit search: AI", [], False, "", "AUTH_REQUIRED"),
            CommandResult("Twitter search: AI", [], False, "", "AUTH_REQUIRED"),
        ]
        with self.assertRaises(RuntimeError):
            validate_collection_health(results, [])

    def test_collection_gate_rejects_successful_platform_with_no_items(self) -> None:
        doctor = CommandResult(
            "Agent-Reach doctor",
            [],
            True,
            json.dumps(
                {
                    "reddit": {"status": "ok", "active_backend": "OpenCLI"},
                    "twitter": {"status": "ok", "active_backend": "OpenCLI"},
                }
            ),
            "",
        )
        reddit_yaml = "\n".join(
            [
                "- id: reddit-1",
                "  title: AI agent release",
                "  subreddit: LocalLLaMA",
                "  url: https://reddit.com/r/LocalLLaMA/comments/reddit1",
            ]
        )
        results = [
            doctor,
            CommandResult("Reddit search: AI", [], True, reddit_yaml, ""),
            CommandResult("Twitter search: AI", [], True, "[]", ""),
        ]
        with patch("daily_news.app.COLLECTION_MIN_SOURCE_ITEMS", 1):
            with self.assertRaisesRegex(RuntimeError, "X/Twitter"):
                validate_collection_health(results, [])

    def test_collection_gate_rejects_timestamp_schema_drift(self) -> None:
        doctor = CommandResult(
            "Agent-Reach doctor",
            [],
            True,
            json.dumps(
                {
                    "reddit": {"status": "ok", "active_backend": "OpenCLI"},
                }
            ),
            "",
        )
        reddit_yaml = """- id: reddit-1
  title: AI agent release
  subreddit: LocalLLaMA
  url: https://reddit.com/r/LocalLLaMA/comments/reddit1
"""
        results = [
            doctor,
            CommandResult("Reddit search: AI", [], True, reddit_yaml, ""),
        ]
        with (
            patch("daily_news.app.INCLUDE_TWITTER", False),
            patch("daily_news.app.COLLECTION_MIN_SOURCE_ITEMS", 1),
        ):
            with self.assertRaisesRegex(RuntimeError, "时间戳解析率"):
                validate_collection_health(results, [])

    def test_top_level_unknown_timestamp_is_not_fresh(self) -> None:
        for kind in ("post", "tweet", "article"):
            item = SourceItem(
                item_id=f"unknown-{kind}",
                platform="Reddit" if kind == "post" else "X/Twitter",
                kind=kind,
                title="Old release with missing timestamp",
            )
            self.assertFalse(is_fresh_item(item, "2026-07-01"))
            self.assertFalse(is_fresh_article_item(item, "2026-07-01"))

    def test_discussion_can_inherit_parent_freshness_without_timestamp(self) -> None:
        item = SourceItem(
            item_id="comment-1",
            platform="Reddit",
            kind="discussion-L0",
            text="Detailed technical comment",
            parent_post_id="post-1",
        )
        self.assertTrue(is_fresh_item(item, "2026-07-01"))

    def test_output_gate_rejects_report_without_effective_content(self) -> None:
        report = """# 日报
- 原始候选条目：6
- 入选 Reddit 帖：0
- 重点议题：0
- X 资讯/信号：0
- 短讯/观察：0
"""
        article_report = """# 文章
- 候选文章：0
- 已读取正文：0
"""
        with self.assertRaisesRegex(RuntimeError, "没有任何有效入选内容"):
            validate_output_quality(report, article_report)

    def test_output_gate_accepts_nonempty_report(self) -> None:
        report = """# 日报
- 入选 Reddit 帖：1
- 重点议题：1
- X 资讯/信号：0
- 短讯/观察：0
"""
        article_report = """# 文章
- 候选文章：0
- 已读取正文：0
"""
        validate_output_quality(report, article_report)


if __name__ == "__main__":
    unittest.main()
