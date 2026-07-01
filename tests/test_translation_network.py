from __future__ import annotations

import http.client
import json
import unittest
import urllib.error
from unittest.mock import patch

from daily_news.app import (
    request_anthropic_translation,
    translate_anthropic_batch,
    translate_with_anthropic,
    wait_for_anthropic_request_slot,
)
from daily_news.models import Enrichment, SourceItem


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]


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
        ) as request, patch(
            "daily_news.app.ANTHROPIC_REQUEST_RETRY_LIMIT",
            2,
        ), patch(
            "daily_news.app.ANTHROPIC_MIN_REQUEST_INTERVAL_SECONDS",
            0,
        ), patch(
            "daily_news.app.time.sleep",
        ), patch(
            "daily_news.app.random.uniform",
            return_value=0,
        ):
            result = translate_anthropic_batch(items, "test-key")

        self.assertEqual(set(result), {"reddit-test"})
        self.assertIn("remote closed connection", result["reddit-test"].error)
        self.assertEqual(request.call_count, 2)

    def test_rate_limit_uses_retry_after_then_succeeds(self) -> None:
        request = object()
        rate_limit = urllib.error.HTTPError(
            "https://example.test",
            429,
            "Too Many Requests",
            {"Retry-After": "0"},
            None,
        )
        response = FakeResponse(b'{"ok": true}')

        with patch(
            "daily_news.app.urllib.request.urlopen",
            side_effect=[rate_limit, response],
        ) as urlopen, patch(
            "daily_news.app.ANTHROPIC_REQUEST_RETRY_LIMIT",
            2,
        ), patch(
            "daily_news.app.ANTHROPIC_MIN_REQUEST_INTERVAL_SECONDS",
            0,
        ), patch(
            "daily_news.app.time.sleep",
        ) as sleep:
            raw, error = request_anthropic_translation(request)

        self.assertEqual(json.loads(raw), {"ok": True})
        self.assertEqual(error, "")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_not_called()

    def test_rate_limit_exhaustion_stops_after_request_limit(self) -> None:
        rate_limit = urllib.error.HTTPError(
            "https://example.test",
            429,
            "Too Many Requests",
            {},
            None,
        )

        with patch(
            "daily_news.app.urllib.request.urlopen",
            side_effect=rate_limit,
        ) as urlopen, patch(
            "daily_news.app.ANTHROPIC_REQUEST_RETRY_LIMIT",
            3,
        ), patch(
            "daily_news.app.ANTHROPIC_MIN_REQUEST_INTERVAL_SECONDS",
            0,
        ), patch(
            "daily_news.app.ANTHROPIC_RETRY_BASE_SECONDS",
            1,
        ), patch(
            "daily_news.app.ANTHROPIC_RETRY_MAX_SECONDS",
            4,
        ), patch(
            "daily_news.app.random.uniform",
            return_value=0,
        ), patch(
            "daily_news.app.time.sleep",
        ) as sleep:
            raw, error = request_anthropic_translation(object())

        self.assertEqual(raw, "")
        self.assertIn("HTTP Error 429", error)
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_request_slot_enforces_minimum_interval(self) -> None:
        with patch(
            "daily_news.app._anthropic_last_request_started",
            10,
        ), patch(
            "daily_news.app.ANTHROPIC_MIN_REQUEST_INTERVAL_SECONDS",
            5,
        ), patch(
            "daily_news.app.time.monotonic",
            side_effect=[12, 15],
        ), patch(
            "daily_news.app.sleep_for_anthropic",
        ) as sleep:
            wait_for_anthropic_request_slot()

        sleep.assert_called_once_with(3)

    def test_rate_limit_exhaustion_skips_remaining_batches(self) -> None:
        items = [
            SourceItem(
                item_id=f"discussion-{index}",
                platform="Reddit",
                kind="discussion-L0",
                text="Detailed discussion",
            )
            for index in range(3)
        ]

        def rate_limited(batch: list[SourceItem], _api_key: str) -> dict[str, Enrichment]:
            return {
                item.item_id: Enrichment(
                    error=(
                        "Anthropic-compatible translation request failed: "
                        "HTTPError: HTTP Error 429: Too Many Requests"
                    )
                )
                for item in batch
            }

        with patch(
            "daily_news.app.anthropic_api_key",
            return_value="test-key",
        ), patch(
            "daily_news.app.ANTHROPIC_BASE_URL",
            "https://example.test",
        ), patch(
            "daily_news.app.load_anthropic_cache",
            return_value={},
        ), patch(
            "daily_news.app.translate_anthropic_batch",
            side_effect=rate_limited,
        ) as translate:
            result = translate_with_anthropic(items)

        self.assertEqual(translate.call_count, 1)
        self.assertIn("HTTP Error 429", result["discussion-0"].error)
        self.assertIn("skipped", result["discussion-1"].error)
        self.assertIn("skipped", result["discussion-2"].error)


if __name__ == "__main__":
    unittest.main()
