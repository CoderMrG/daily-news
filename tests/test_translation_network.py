from __future__ import annotations

import http.client
import unittest
from unittest.mock import patch

from daily_news.app import translate_anthropic_batch
from daily_news.models import SourceItem


class TranslationNetworkTests(unittest.TestCase):
    def test_anthropic_remote_disconnect_becomes_item_failure(self) -> None:
        items = [
            SourceItem(
                item_id="reddit-test",
                platform="Reddit",
                kind="post",
                title="AI engineering update",
            )
        ]

        with patch(
            "daily_news.app.urllib.request.urlopen",
            side_effect=http.client.RemoteDisconnected(
                "remote closed connection"
            ),
        ):
            result = translate_anthropic_batch(items, "test-key")

        self.assertEqual(set(result), {"reddit-test"})
        self.assertIn("remote closed connection", result["reddit-test"].error)


if __name__ == "__main__":
    unittest.main()
