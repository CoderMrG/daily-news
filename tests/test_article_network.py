from __future__ import annotations

import datetime as dt
import io
import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from daily_news.app import (
    SafeRedirectHandler,
    fetch_article_text,
    is_public_http_url,
)


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]


class ArticleNetworkTests(unittest.TestCase):
    def test_transient_article_failure_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("daily_news.app.RAW_DIR", Path(directory)),
                patch.dict(os.environ, {"DAILY_NEWS_DATE": "2026-07-01"}),
                patch(
                    "daily_news.app.urllib.request.urlopen",
                    side_effect=urllib.error.URLError("temporary outage"),
                ),
            ):
                first_text, first_error = fetch_article_text(
                    "https://example.com/article"
                )

            with (
                patch("daily_news.app.RAW_DIR", Path(directory)),
                patch.dict(os.environ, {"DAILY_NEWS_DATE": "2026-07-01"}),
                patch(
                    "daily_news.app.urllib.request.urlopen",
                    return_value=FakeResponse(b"Recovered article body"),
                ) as request,
            ):
                second_text, second_error = fetch_article_text(
                    "https://example.com/article"
                )

        self.assertEqual(first_text, "")
        self.assertIn("temporary outage", first_error)
        self.assertEqual(second_text, "Recovered article body")
        self.assertEqual(second_error, "")
        self.assertEqual(request.call_count, 1)

    def test_not_found_cache_expires(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            url = "https://example.com/missing"
            error = urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            with (
                patch("daily_news.app.RAW_DIR", root),
                patch.dict(os.environ, {"DAILY_NEWS_DATE": "2026-07-01"}),
                patch("daily_news.app.urllib.request.urlopen", side_effect=error),
            ):
                fetch_article_text(url)

            with (
                patch("daily_news.app.RAW_DIR", root),
                patch.dict(os.environ, {"DAILY_NEWS_DATE": "2026-07-01"}),
                patch("daily_news.app.urllib.request.urlopen") as request,
            ):
                _text, cached_error = fetch_article_text(url)
                self.assertEqual(request.call_count, 0)
                self.assertIn("404", cached_error)

            cache_path = root / "2026-07-01" / "article-cache.json"
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            row = next(iter(cache.values()))
            row["retry_after"] = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
            ).isoformat()
            cache_path.write_text(json.dumps(cache), encoding="utf-8")

            with (
                patch("daily_news.app.RAW_DIR", root),
                patch.dict(os.environ, {"DAILY_NEWS_DATE": "2026-07-01"}),
                patch(
                    "daily_news.app.urllib.request.urlopen",
                    return_value=FakeResponse(b"Article now exists"),
                ) as request,
            ):
                text, fetch_error = fetch_article_text(url)

        self.assertEqual(text, "Article now exists")
        self.assertEqual(fetch_error, "")
        self.assertEqual(request.call_count, 1)

    def test_private_and_local_urls_are_rejected(self) -> None:
        self.assertFalse(is_public_http_url("http://127.0.0.1/admin"))
        self.assertFalse(is_public_http_url("http://[::1]/admin"))
        self.assertFalse(is_public_http_url("http://localhost/admin"))
        self.assertFalse(is_public_http_url("http://example.com:invalid/admin"))
        with patch(
            "daily_news.app.socket.getaddrinfo",
            return_value=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ("192.168.1.10", 80),
                )
            ],
        ):
            self.assertFalse(is_public_http_url("http://internal.example/admin"))

    def test_redirect_handler_blocks_private_target(self) -> None:
        handler = SafeRedirectHandler()
        request = urllib.request.Request("https://t.co/example")
        with self.assertRaisesRegex(urllib.error.URLError, "unsafe redirect"):
            handler.redirect_request(
                request,
                io.BytesIO(),
                302,
                "Found",
                {},
                "http://127.0.0.1/admin",
            )


if __name__ == "__main__":
    unittest.main()
