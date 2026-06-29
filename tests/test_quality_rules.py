import unittest
from unittest.mock import patch

from daily_news.app import (
    discussion_information_score,
    event_family_key,
    group_article_candidates,
    group_key,
    informative_discussion_item,
    same_x_sequence,
    source_key,
    validate_translation_coverage,
)
from daily_news.models import ArticleCandidate, Enrichment, SourceItem


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


if __name__ == "__main__":
    unittest.main()
