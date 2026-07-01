import unittest
from pathlib import Path

from daily_news.app import parse_source_items
from daily_news.models import CommandResult
from daily_news.utils import parse_simple_yaml_list


FIXTURES = Path(__file__).parent / "fixtures"


class ParserContractTests(unittest.TestCase):
    def test_opencli_reddit_fixture(self) -> None:
        text = (FIXTURES / "opencli_reddit.yaml").read_text(encoding="utf-8")
        rows = parse_simple_yaml_list(text)
        self.assertEqual(rows[0]["id"], "abc123")
        self.assertIn("tool use", rows[0]["selftext"])

        items = parse_source_items(
            [CommandResult("Reddit search: AI", [], True, text, "")],
            [],
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].platform, "Reddit")
        self.assertEqual(items[0].comments, 240)
        self.assertIsNotNone(items[0].created_at)

    def test_opencli_twitter_fixture(self) -> None:
        text = (FIXTURES / "opencli_twitter.yaml").read_text(encoding="utf-8")
        rows = parse_simple_yaml_list(text)
        self.assertEqual(rows[0]["card.url"], "https://example.com/release")

        items = parse_source_items(
            [CommandResult("Twitter search: AI", [], True, text, "")],
            [],
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].platform, "X/Twitter")
        self.assertEqual(items[0].score, 1500)
        self.assertIsNotNone(items[0].created_at)


if __name__ == "__main__":
    unittest.main()
