#!/usr/bin/env python3
"""Generate a V1 daily technical community report via Agent-Reach tools."""

from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import json
import math
import os
import re
import socket
import subprocess
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


from daily_news.models import (
    ArticleCandidate,
    CommandResult,
    DiscussionThread,
    Enrichment,
    HistoryFilter,
    SourceItem,
    TopicGroup,
)
from daily_news.settings import (
    ANTHROPIC_BASE_URL,
    ANTHROPIC_BATCH_SIZE,
    ANTHROPIC_CACHE_VERSION,
    ANTHROPIC_DISABLE_THINKING,
    ANTHROPIC_MODEL,
    ANTHROPIC_RETRY_LIMIT,
    ARTICLE_BLOCKED_DOMAINS,
    ARTICLE_CANDIDATE_LIMIT,
    ARTICLE_AUTHOR_LIMIT,
    ARTICLE_DIR,
    ARTICLE_FETCH_ENABLED,
    ARTICLE_FETCH_LIMIT,
    ARTICLE_FRESHNESS_DAYS,
    ARTICLE_LIMIT,
    ARTICLE_MIN_SCORE,
    ARTICLE_QUALITY_DOMAINS,
    DISCUSSION_RAW_WIDTH,
    DISCUSSION_TEXT_WIDTH,
    DISCUSSION_TRANSLATION_WIDTH,
    ENTITY_TERMS,
    EXCLUDE_SUBREDDITS,
    EXCLUDE_TERMS,
    FOCUS_GROUP_LIMIT,
    FRESHNESS_FUTURE_GRACE_DAYS,
    FROM_RAW,
    GOOGLE_TIMEOUT_SECONDS,
    HIGH_RELEVANCE_SUBREDDITS,
    HIGH_RELEVANCE_SUBREDDIT_COMMENT_MIN,
    HISTORY_CONTINUATION_COMMENT_MIN,
    HISTORY_CONTINUATION_LIMIT,
    HISTORY_CONTINUATION_SCORE_MIN,
    HISTORY_DEDUP_DAYS,
    HOT_ITEM_COMMENT_MIN,
    HOT_ITEM_SCORE_MIN,
    HYBRID_GLM_ITEM_LIMIT,
    INCLUDE_TERMS,
    INCLUDE_TWITTER,
    LIMIT,
    LOW_SCORE_THREAD_LIMIT,
    LOW_SCORE_THREAD_MIN_LENGTH,
    OBSIDIAN_SUBDIR,
    OBSIDIAN_VAULT_DIR,
    OPENAI_MODEL,
    RAW_DIR,
    READ_FETCH_LIMIT,
    READ_LIMIT,
    READ_REPLIES,
    REDDIT_FRESHNESS_DAYS,
    REDDIT_SUBREDDITS,
    REPLY_TEXT_WIDTH,
    REPORT_DIR,
    REPORT_ITEM_LIMIT,
    REQUIRE_HOT_DISCUSSION,
    SHORT_ITEM_LIMIT,
    SOURCE_SUMMARY_WIDTH,
    SUBREDDIT_COUNT,
    THREAD_LIMIT,
    THREAD_MIN_SCORE,
    THREAD_REPLY_LIMIT,
    TIMEOUT_SECONDS,
    TOPICS,
    TOPIC_COUNT,
    TRANSLATION_PROVIDER,
    TRANSLATION_MAX_FAILURE_PERCENT,
    USE_LLM,
    X_ACCOUNTS,
    X_ACCOUNT_COUNT,
    X_FRESHNESS_DAYS,
    X_LIMIT,
    X_QUALITY_AUTHORS,
    X_SIGNAL_LIMIT,
    X_THREAD_READ_LIMIT,
    X_THREAD_REPLY_LIMIT,
    X_TOPICS,
    X_TOPIC_COUNT,
)
from daily_news.utils import (
    as_int,
    compact,
    first_value,
    parse_epoch_datetime,
    parse_simple_yaml_list,
    parse_twitter_datetime,
)

EVENT_FAMILY_PATTERNS = [
    r"\bgpt-\d+(?:\.\d+)?\b",
    r"\bqwen[-\s]?agentworld\b",
    r"\bqwen[-\s]?robot(?:nav|manip|world|\s+suite)?\b",
    r"\bclaude\s+[a-z]+\s+\d+(?:\.\d+)?\b",
    r"\blfm\d+(?:\.\d+)?(?:-\d+[mb])?\b",
    r"\bornith(?:-1\.0)?\b",
]


def redact(text: str) -> str:
    patterns = [
        r"(?i)(auth[_-]?token\s*[:=]\s*)[^\s,;&]+",
        r"(?i)(ct0\s*[:=]\s*)[^\s,;&]+",
        r"(?i)(cookie\s*[:=]\s*)[^\n]+",
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"(?i)(token\s*[:=]\s*)[^\s,;&]+",
        r"(?i)(password\s*[:=]\s*)[^\s,;&]+",
    ]
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted.strip()


def run(title: str, command: list[str]) -> CommandResult:
    print(f"[daily-news] {title}", flush=True)
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        return CommandResult(
            title=title,
            command=command,
            ok=completed.returncode == 0,
            stdout=redact(completed.stdout),
            stderr=redact(completed.stderr),
        )
    except FileNotFoundError as exc:
        return CommandResult(title, command, False, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            title,
            command,
            False,
            redact(stdout),
            redact(f"Timed out after {TIMEOUT_SECONDS}s\n{stderr}"),
        )


def collect() -> tuple[list[CommandResult], list[CommandResult]]:
    results: list[CommandResult] = []

    results.append(run("Agent-Reach doctor", ["agent-reach", "doctor", "--json"]))

    for topic in TOPICS[:TOPIC_COUNT]:
        results.append(
            run(
                f"Reddit search: {topic}",
                [
                    "opencli",
                    "reddit",
                    "search",
                    topic,
                    "--limit",
                    str(LIMIT),
                    "--window",
                    "foreground",
                    "-f",
                    "yaml",
                ],
            )
        )

    if INCLUDE_TWITTER:
        for topic in X_TOPICS[:X_TOPIC_COUNT]:
            results.append(
                run(
                    f"Twitter search: {topic}",
                    ["opencli", "twitter", "search", topic, "--limit", str(X_LIMIT), "-f", "yaml"],
                )
            )
        for account in X_ACCOUNTS[:X_ACCOUNT_COUNT]:
            results.append(
                run(
                    f"Twitter tweets: @{account}",
                    ["opencli", "twitter", "tweets", f"@{account}", "--limit", str(X_LIMIT), "-f", "yaml"],
                )
            )

    results.append(
        run(
            "Reddit popular",
            [
                "opencli",
                "reddit",
                "popular",
                "--limit",
                str(LIMIT),
                "--window",
                "foreground",
                "-f",
                "yaml",
            ],
        )
    )

    for subreddit in REDDIT_SUBREDDITS[:SUBREDDIT_COUNT]:
        results.append(
            run(
                f"Reddit subreddit: r/{subreddit}",
                [
                    "opencli",
                    "reddit",
                    "subreddit",
                    subreddit,
                    "--limit",
                    str(LIMIT),
                    "--window",
                    "foreground",
                    "-f",
                    "yaml",
                ],
            )
        )

    read_results = []
    read_post_ids = select_reddit_posts_to_read(results)
    for post_id in read_post_ids[:READ_LIMIT]:
        read_results.append(
            run(
                f"Reddit read: {post_id}",
                [
                    "opencli",
                    "reddit",
                    "read",
                    post_id,
                    "--window",
                    "foreground",
                    "--limit",
                    str(READ_FETCH_LIMIT),
                    "--depth",
                    "2",
                    "--replies",
                    str(READ_REPLIES),
                    "-f",
                    "json",
                ],
            )
        )

    if INCLUDE_TWITTER:
        for tweet_id in select_tweets_to_read(results)[:X_THREAD_READ_LIMIT]:
            read_results.append(
                run(
                    f"Twitter thread: {tweet_id}",
                    [
                        "opencli",
                        "twitter",
                        "thread",
                        tweet_id,
                        "--limit",
                        str(X_THREAD_REPLY_LIMIT + 1),
                        "-f",
                        "yaml",
                    ],
                )
            )

    return results, read_results


def extract_reddit_post_ids(results: Iterable[CommandResult]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    patterns = [
        re.compile(r"(?im)^\s*(?:id|post_id)\s*:\s*['\"]?([a-z0-9]{5,12})"),
        re.compile(r"/comments/([a-z0-9]{5,12})", re.IGNORECASE),
    ]

    for result in results:
        if not result.ok or not result.title.startswith("Reddit"):
            continue
        for pattern in patterns:
            for match in pattern.finditer(result.stdout):
                post_id = match.group(1).lower()
                if post_id not in seen:
                    seen.add(post_id)
                    ids.append(post_id)
    return ids


def select_reddit_posts_to_read(results: list[CommandResult]) -> list[str]:
    items = topic_items(parse_source_items(results, []))
    selected_ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.platform != "Reddit" or item.kind != "post":
            continue
        post_id = reddit_post_id(item)
        if post_id and post_id not in seen:
            seen.add(post_id)
            selected_ids.append(post_id)
    for post_id in extract_reddit_post_ids(results):
        if post_id not in seen:
            seen.add(post_id)
            selected_ids.append(post_id)
    return selected_ids


def select_tweets_to_read(results: list[CommandResult]) -> list[str]:
    selected_ids: list[str] = []
    seen: set[str] = set()
    for item in x_signal_items(parse_source_items(results, [])):
        tweet_id = twitter_tweet_id(item)
        if tweet_id and tweet_id not in seen:
            seen.add(tweet_id)
            selected_ids.append(tweet_id)
        if len(selected_ids) >= X_THREAD_READ_LIMIT:
            break
    return selected_ids


def reddit_post_id(item: SourceItem) -> str:
    raw_id = str(item.raw.get("id", "")).strip().lower()
    if raw_id:
        return raw_id
    match = re.search(r"/comments/([a-z0-9]{5,12})", item.url or "", re.IGNORECASE)
    if match:
        return match.group(1).lower()
    if item.item_id.startswith("reddit-"):
        return item.item_id.removeprefix("reddit-").lower()
    if "-" in item.item_id:
        return item.item_id.split("-", 1)[0].lower()
    return ""


def twitter_tweet_id(item: SourceItem) -> str:
    raw_id = str(item.raw.get("id", "")).strip()
    if raw_id:
        return raw_id
    match = re.search(r"/status/(\d+)", item.url or "")
    if match:
        return match.group(1)
    if item.item_id.startswith("twitter-"):
        return item.item_id.removeprefix("twitter-")
    return ""


def source_event_key(item: SourceItem) -> str:
    tweet_id = twitter_tweet_id(item)
    if item.platform == "X/Twitter" and tweet_id:
        return f"twitter:{tweet_id}"
    post_id = reddit_post_id(item)
    if item.platform == "Reddit" and post_id:
        return f"reddit:{post_id}"
    return ""


def event_family_key(item: SourceItem) -> str:
    token = event_family_token(item_text(item))
    if token:
        return f"{item.platform}:{x_author_key(item)}:{token}"
    return source_event_key(item)


def event_family_token(text: str) -> str:
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text.lower())
    for pattern in EVENT_FAMILY_PATTERNS:
        match = re.search(pattern, text)
        if match:
            token = re.sub(r"\s+", "-", match.group(0).lower())
            if token.startswith("ornith"):
                return "ornith"
            if token.startswith("qwen-robot"):
                return "qwen-robot"
            return token
    return ""


def source_key(item: SourceItem) -> str:
    if item.kind.startswith("discussion"):
        return f"{item.platform}:{item.kind}:{item.item_id}:{item.text[:120]}"
    event_key = source_event_key(item)
    if event_key:
        return event_key
    return item.url or f"{item.platform}:{item.kind}:{item.title}:{item.text[:80]}"


def parse_source_items(results: list[CommandResult], read_results: list[CommandResult]) -> list[SourceItem]:
    parsed: list[SourceItem] = []
    seen: set[str] = set()
    reddit_post_url_by_id: dict[str, str] = {}

    for result in results:
        if not result.ok or not result.stdout:
            continue
        if result.title.startswith("Twitter search") or result.title.startswith("Twitter tweets"):
            platform = "X/Twitter"
            for raw in parse_simple_yaml_list(result.stdout):
                item = SourceItem(
                    item_id=f"twitter-{raw.get('id', len(parsed))}",
                    platform=platform,
                    kind="tweet",
                    title=compact(first_value(raw, ["card.title", "text"]), 160),
                    text=compact(raw.get("text", ""), 1200),
                    author=compact(raw.get("author", ""), 80),
                    url=compact(raw.get("url", ""), 300),
                    source_url=compact(raw.get("card.url", ""), 300),
                    score=as_int(raw.get("likes")) + as_int(raw.get("retweets")),
                    comments=as_int(raw.get("replies")),
                    created_at=parse_twitter_datetime(raw.get("created_at")),
                    source_command=" ".join(result.command),
                    raw=raw,
                )
                add_item(parsed, seen, item)

        if result.title.startswith("Reddit"):
            platform = "Reddit"
            for raw in parse_simple_yaml_list(result.stdout):
                reddit_id = str(raw.get("id", "")).strip().lower()
                reddit_url = compact(raw.get("url", ""), 300)
                if reddit_id and reddit_url:
                    reddit_post_url_by_id[reddit_id] = reddit_url
                item = SourceItem(
                    item_id=f"reddit-{raw.get('id', len(parsed))}",
                    platform=platform,
                    kind="post",
                    title=compact(raw.get("title", ""), 220),
                    text=compact(raw.get("selftext", ""), 1600),
                    author=compact(raw.get("author", ""), 80),
                    url=reddit_url,
                    source_url=compact(raw.get("url_overridden_by_dest", ""), 300),
                    score=as_int(first_value(raw, ["score", "upvotes"])),
                    comments=as_int(raw.get("comments")),
                    created_at=parse_epoch_datetime(raw.get("created_utc")),
                    parent_post_id=reddit_id,
                    source_command=" ".join(result.command),
                    raw=raw,
            )
            add_item(parsed, seen, item)

    twitter_url_by_id = {
        twitter_tweet_id(item): item.url
        for item in parsed
        if item.platform == "X/Twitter" and twitter_tweet_id(item) and item.url
    }

    for result in read_results:
        if not result.ok or not result.stdout:
            continue
        if result.title.startswith("Twitter thread"):
            post_id = result.title.split(":")[-1].strip()
            raw_items = parse_simple_yaml_list(result.stdout)
            root_author = compact(raw_items[0].get("author", ""), 80) if raw_items else ""
            for idx, raw in enumerate(raw_items):
                tweet_id = str(raw.get("id", f"{post_id}-{idx}")).strip()
                author = compact(raw.get("author", ""), 80)
                text = compact(raw.get("text", ""), DISCUSSION_RAW_WIDTH)
                if not text:
                    continue
                if idx == 0 or tweet_id == post_id:
                    kind = "x-thread-root"
                elif root_author and author.lower() == root_author.lower():
                    kind = "x-author-reply"
                else:
                    kind = "x-reply"
                item = SourceItem(
                    item_id=f"twitter-thread-{tweet_id}",
                    platform="X/Twitter",
                    kind=kind,
                    title=compact(text, 160),
                    text=text,
                    author=author,
                    url=compact(raw.get("url", ""), 300) or twitter_url_by_id.get(post_id, ""),
                    source_url=compact(raw.get("card.url", ""), 300),
                    score=as_int(raw.get("likes")) + as_int(raw.get("retweets")),
                    comments=as_int(raw.get("replies")),
                    created_at=parse_twitter_datetime(raw.get("created_at")),
                    parent_post_id=post_id,
                    source_command=" ".join(result.command),
                    raw=raw,
                )
                add_item(parsed, seen, item)
            continue

        try:
            raw_items = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_items, list):
            continue
        post_id = result.title.split(":")[-1].strip().lower()
        discussion_url = reddit_post_url_by_id.get(post_id, "")
        for idx, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            item_type = compact(first_value(raw, ["type", "kind"]), 20) or "ITEM"
            text = compact(first_value(raw, ["body", "text", "selftext", "content", "comment"]), DISCUSSION_RAW_WIDTH)
            title = compact(first_value(raw, ["title", "name"]), 220)
            if not text and not title:
                continue
            item = SourceItem(
                item_id=f"{result.title.split(':')[-1].strip()}-{idx}",
                platform="Reddit",
                kind=f"discussion-{item_type}",
                title=title,
                text=text,
                author=compact(first_value(raw, ["author", "user", "username"]), 80),
                url=compact(first_value(raw, ["url", "permalink"]), 300) or discussion_url,
                score=as_int(first_value(raw, ["score", "upvotes", "ups"])),
                parent_post_id=post_id,
                source_command=" ".join(result.command),
                raw=raw,
            )
            add_item(parsed, seen, item)

    return parsed


def add_item(items: list[SourceItem], seen: set[str], item: SourceItem) -> None:
    key = source_key(item)
    if key in seen:
        return
    seen.add(key)
    items.append(item)


def skip_by_quality_filter(text: str) -> bool:
    lower = text.lower()
    if any(term in lower for term in EXCLUDE_TERMS):
        return True
    return low_value_invite_text(lower)


def low_information_item(item: SourceItem) -> bool:
    text = f"{item.title}\n{item.text}".strip()
    lower = text.lower()
    if item.author.lower() == "automoderator":
        return True
    if item_subreddit(item) in EXCLUDE_SUBREDDITS:
        return True
    if re.match(r"^\s*(>+\s*)?\[\+\d+\s+more", text, re.IGNORECASE):
        return True
    if lower in {"meirl", "peter?", "[removed]", "[deleted]"}:
        return True
    if "i am a bot" in lower and "action was performed automatically" in lower:
        return True
    if item.kind.startswith("discussion-L") and len(text) < 25:
        return True
    if item.kind.startswith("discussion-L") and low_value_discussion_text(lower):
        return True
    return False


def low_value_discussion_text(lower: str) -> bool:
    patterns = [
        r"\bps2\b.*\bnpc\b",
        r"\bnpc\b.*\bphoto\b",
        r"\bboar\s+pelts?\b",
        r"\bclowns?\b",
        r"\bsexual\s+abuse\b",
        r"\blooks?\s+like\b.*\bnpc\b",
        r"\ball\s+dressed\s+up\b.*\bnerds?\b",
        r"\b(top\s+is\s+in|life\s+savings|regards)\b",
        r"\bcracked\s+tts\b",
        r"\btelegram\s+group\b",
        r"\blegality\s+is\s+questionable\b",
        r"\byandex\s+search\b",
        r"^\s*(hello|hi|hey)\s+(friends?|guys|everyone)\W*$",
        r"\b(please|pls)\s+(help|fix)\b",
        r"\b(can.?t|cannot|won.?t)\s+(open|launch|login|sign in)\b",
        r"\b(captcha|verification code|puzzle piece)\b",
        r"^\s*@?(openai|anthropicai|cursor_ai)\s+(keep|bring back|help)\b",
        r"\bplease\b.*\b(not opening|browser|help)\b",
        r"\bcan\s+anyone\s+help\b",
        r"\b(cold war|foreign nationals?|passport|us citizens?)\b",
    ]
    return low_value_invite_text(lower) or any(re.search(pattern, lower) for pattern in patterns)


def low_value_invite_text(lower: str) -> bool:
    patterns = [
        r"\bsora[-\s]?invite[-\s]?codes?\b",
        r"\binvite\s+codes?\b",
        r"\bneed\s+(?:a\s+)?code\b",
        r"\bcode\s+dm\b",
        r"\banyone\s+(?:have|has)\s+(?:a\s+)?code\b",
        r"\bcan\s+i\s+get\s+(?:an?\s+)?invite\b",
    ]
    return any(re.search(pattern, lower) for pattern in patterns)


def keyword_hits(item: SourceItem) -> int:
    text = " ".join(
        [
            item.title,
            item.text,
            item.source_url,
            str(item.raw.get("subreddit", "")),
        ]
    ).lower()
    return sum(1 for term in INCLUDE_TERMS if term_matches(term, text))


def entity_hits(item: SourceItem) -> int:
    text = item_text(item)
    return sum(1 for term in ENTITY_TERMS if term_matches(term, text))


def item_subreddit(item: SourceItem) -> str:
    value = str(item.raw.get("subreddit", "")).strip().lower()
    if value and not value.startswith("r/"):
        return f"r/{value}"
    return value


def from_high_relevance_subreddit(item: SourceItem) -> bool:
    return item_subreddit(item) in HIGH_RELEVANCE_SUBREDDITS


def very_hot_item(item: SourceItem) -> bool:
    return item.score >= HOT_ITEM_SCORE_MIN or item.comments >= HOT_ITEM_COMMENT_MIN


def high_relevance_subreddit_discussion(item: SourceItem) -> bool:
    return from_high_relevance_subreddit(item) and item.comments >= HIGH_RELEVANCE_SUBREDDIT_COMMENT_MIN


def item_age_days(item: SourceItem, today: str | None = None) -> int | None:
    if item.created_at is None:
        return None
    current_date = parse_report_date(today or report_date())
    if current_date is None:
        return None
    return (current_date - item.created_at.date()).days


def is_fresh_item(item: SourceItem, today: str | None = None) -> bool:
    age_days = item_age_days(item, today)
    if age_days is None:
        return True
    if age_days < -FRESHNESS_FUTURE_GRACE_DAYS:
        return False
    if age_days < 0:
        return True
    if item.platform == "X/Twitter":
        return age_days <= X_FRESHNESS_DAYS
    if item.platform == "Reddit" and item.kind == "post":
        return age_days <= REDDIT_FRESHNESS_DAYS
    return True


def is_fresh_article_item(item: SourceItem, today: str | None = None) -> bool:
    age_days = item_age_days(item, today)
    if age_days is None:
        return True
    if age_days < -FRESHNESS_FUTURE_GRACE_DAYS:
        return False
    return age_days < 0 or age_days <= ARTICLE_FRESHNESS_DAYS


def x_author_key(item: SourceItem) -> str:
    return item.author.strip().lstrip("@").lower()


def from_quality_x_author(item: SourceItem) -> bool:
    return x_author_key(item) in X_QUALITY_AUTHORS


def x_has_link_or_media(item: SourceItem) -> bool:
    text = f"{item.title}\n{item.text}"
    return (
        "http://" in text
        or "https://" in text
        or raw_truthy(item.raw.get("has_media"))
        or raw_truthy(item.raw.get("card"))
        or raw_truthy(item.raw.get("quoted_tweet"))
    )


def raw_truthy(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "false", "null", "none", "0", "[]", "{}"}
    return bool(value)


def x_low_value_item(item: SourceItem) -> bool:
    text = f"{item.title}\n{item.text}\n{item.raw.get('bio', '')}".lower()
    patterns = [
        r"\bdm\s+me\b",
        r"\bnewsletter\b.*\bsubscribe\b",
        r"\bairdrop\b",
        r"\bcrypto\b",
        r"\binvite\s+code\b",
        r"已入驻曰炮平台",
        r"附近的可加",
        r"合作v[:：]",
    ]
    return low_information_item(item) or any(re.search(pattern, text) for pattern in patterns)


def x_signal_score(item: SourceItem) -> int:
    score = relevance_score(item)
    score += min(as_int(item.raw.get("views")) // 10000, 50)
    score += min(as_int(item.raw.get("retweets")) // 10, 40)
    if from_quality_x_author(item):
        score += 80
    if x_has_link_or_media(item):
        score += 30
    if item.kind == "x-author-reply":
        score += 25
    if item.kind == "x-reply":
        score -= 30
    if x_low_value_item(item):
        score -= 1000
    return score


def x_signal_items(items: list[SourceItem]) -> list[SourceItem]:
    historical_events = historical_source_event_keys(report_date())
    historical_families = historical_event_family_tokens(report_date())
    historical_x_posts = historical_x_post_keys(report_date())
    candidates = [
        item
        for item in items
        if item.platform == "X/Twitter"
        and item.kind == "tweet"
        and is_fresh_item(item)
        and (item.title or item.text)
        and not x_low_value_item(item)
        and (
            from_quality_x_author(item)
            or x_has_link_or_media(item)
            or entity_hits(item) > 0
            or item.score >= 100
            or as_int(item.raw.get("views")) >= 50000
        )
        and passes_relevance_gate(item)
        and source_event_key(item) not in historical_events
        and event_family_token(item_text(item)) not in historical_families
        and not matches_historical_x_sequence(item, historical_x_posts)
    ]
    selected: list[SourceItem] = []
    seen: set[str] = set()
    for item in sorted(candidates, key=x_signal_score, reverse=True):
        key = event_family_token(item_text(item)) or source_event_key(item) or item.url or item.item_id
        if key in seen:
            continue
        if any(same_x_sequence(item, existing) for existing in selected):
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= X_SIGNAL_LIMIT:
            break
    return selected


def passes_relevance_gate(item: SourceItem) -> bool:
    if keyword_hits(item) > 0:
        return True
    if entity_hits(item) > 0:
        return True
    if item.platform == "X/Twitter":
        return from_quality_x_author(item)
    if item.platform == "Reddit":
        return high_relevance_subreddit_discussion(item)
    return False


def term_matches(term: str, text: str) -> bool:
    term = term.lower()
    if " " in term:
        return term in text
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def relevance_score(item: SourceItem) -> int:
    text = f"{item.title}\n{item.text}".lower()
    hits = keyword_hits(item)
    entities = entity_hits(item)
    generic_hits = max(0, hits - entities)
    score = int(math.log1p(max(item.score, 0)) * 20 + math.log1p(max(item.comments, 0)) * 35)
    score += entities * 35 + generic_hits * 15
    if from_high_relevance_subreddit(item):
        score += 20
    score -= sum(50 for term in EXCLUDE_TERMS if term in text)
    if item.kind.startswith("discussion-L"):
        score += 8
    if item.source_url:
        score += 5
    if not passes_relevance_gate(item):
        score -= 1000
    if low_information_item(item):
        score -= 1000
    return score


def high_value_items(items: list[SourceItem]) -> list[SourceItem]:
    candidates = [
        item
        for item in items
        if not skip_by_quality_filter(f"{item.title}\n{item.text}")
        and item.kind != "discussion-POST"
        and not low_information_item(item)
        and passes_relevance_gate(item)
        and (item.title or item.text)
        and relevance_score(item) > -20
    ]
    return sorted(candidates, key=relevance_score, reverse=True)[:REPORT_ITEM_LIMIT]


def topic_items(items: list[SourceItem]) -> list[SourceItem]:
    candidates = [
        item
        for item in items
        if item.kind in {"post", "tweet"}
        and (INCLUDE_TWITTER or item.platform != "X/Twitter")
        and is_fresh_item(item)
        and not skip_by_quality_filter(f"{item.title}\n{item.text}")
        and not low_information_item(item)
        and passes_relevance_gate(item)
        and (item.title or item.text)
    ]
    selected: list[SourceItem] = []
    seen: set[str] = set()
    for item in sorted(candidates, key=relevance_score, reverse=True):
        key = item.url or item.item_id
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= REPORT_ITEM_LIMIT:
            break
    return selected


def apply_history_filter(selected: list[SourceItem], today: str) -> HistoryFilter:
    history_ids = historical_reddit_post_ids(today)
    if not history_ids:
        return HistoryFilter(fresh=selected, continuation=[], skipped_count=0, history_ids=set())

    fresh: list[SourceItem] = []
    continuation_candidates: list[SourceItem] = []
    skipped_count = 0
    for item in selected:
        post_id = reddit_post_id(item)
        if not post_id or post_id not in history_ids:
            fresh.append(item)
            continue
        if should_keep_history_continuation(item):
            continuation_candidates.append(item)
        else:
            skipped_count += 1

    continuation = sorted(
        continuation_candidates,
        key=relevance_score,
        reverse=True,
    )[:HISTORY_CONTINUATION_LIMIT]
    skipped_count += max(0, len(continuation_candidates) - len(continuation))
    return HistoryFilter(
        fresh=fresh,
        continuation=continuation,
        skipped_count=skipped_count,
        history_ids=history_ids,
    )


def should_keep_history_continuation(item: SourceItem) -> bool:
    return item.comments >= HISTORY_CONTINUATION_COMMENT_MIN or item.score >= HISTORY_CONTINUATION_SCORE_MIN


def historical_reddit_post_ids(today: str) -> set[str]:
    current_date = parse_report_date(today)
    if current_date is None or HISTORY_DEDUP_DAYS <= 0:
        return set()

    ids: set[str] = set()
    for path in sorted(REPORT_DIR.glob("????-??-??.md")):
        report_day = parse_report_date(path.stem)
        if report_day is None or report_day >= current_date:
            continue
        if (current_date - report_day).days > HISTORY_DEDUP_DAYS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        ids.update(extract_reddit_post_ids_from_text(text))
    return ids


def historical_source_event_keys(today: str) -> set[str]:
    keys: set[str] = set()
    for path in historical_document_paths(today):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        keys.update(f"reddit:{post_id}" for post_id in extract_reddit_post_ids_from_text(text))
        keys.update(f"twitter:{tweet_id}" for tweet_id in extract_twitter_tweet_ids_from_text(text))
    return keys


def historical_article_urls(today: str) -> set[str]:
    urls: set[str] = set()
    for path in historical_document_paths(today, directories=(ARTICLE_DIR,)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for url in extract_urls(text):
            normalized = normalize_article_url(url)
            if normalized and not blocked_article_url(normalized):
                urls.add(normalized)
    return urls


def historical_event_family_tokens(today: str) -> set[str]:
    tokens: set[str] = set()
    for path in historical_document_paths(today):
        try:
            text = path.read_text(encoding="utf-8").lower()
        except OSError:
            continue
        text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
        for pattern in EVENT_FAMILY_PATTERNS:
            for match in re.finditer(pattern, text):
                tokens.add(re.sub(r"\s+", "-", match.group(0).lower()))
    return tokens


def historical_x_post_keys(today: str) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    pattern = re.compile(
        r"来源：\[X @([^\]]+)\]\(https?://(?:x|twitter)\.com/[^/\s)]+/status/(\d+)\)",
        re.IGNORECASE,
    )
    for path in historical_document_paths(today):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        keys.extend((match.group(1).lower(), match.group(2)) for match in pattern.finditer(text))
    return keys


def matches_historical_x_sequence(item: SourceItem, history: list[tuple[str, str]]) -> bool:
    if item.platform != "X/Twitter":
        return False
    author = x_author_key(item)
    tweet_id = twitter_tweet_id(item)
    return any(
        author == historical_author and snowflake_seconds_apart(tweet_id, historical_id) <= 120
        for historical_author, historical_id in history
    )


def same_x_sequence(first: SourceItem, second: SourceItem) -> bool:
    return (
        first.platform == "X/Twitter"
        and second.platform == "X/Twitter"
        and x_author_key(first) == x_author_key(second)
        and snowflake_seconds_apart(twitter_tweet_id(first), twitter_tweet_id(second)) <= 120
    )


def snowflake_seconds_apart(first_id: str, second_id: str) -> float:
    try:
        first_timestamp = (int(first_id) >> 22) + 1288834974657
        second_timestamp = (int(second_id) >> 22) + 1288834974657
    except (TypeError, ValueError):
        return float("inf")
    return abs(first_timestamp - second_timestamp) / 1000


def historical_document_paths(
    today: str,
    directories: tuple[Path, ...] = (REPORT_DIR, ARTICLE_DIR),
) -> list[Path]:
    current_date = parse_report_date(today)
    if current_date is None or HISTORY_DEDUP_DAYS <= 0:
        return []
    paths: list[Path] = []
    for directory in directories:
        for path in sorted(directory.glob("????-??-??.md")):
            document_day = parse_report_date(path.stem)
            if document_day is None or document_day >= current_date:
                continue
            if (current_date - document_day).days <= HISTORY_DEDUP_DAYS:
                paths.append(path)
    return paths


def parse_report_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def extract_reddit_post_ids_from_text(text: str) -> set[str]:
    return {
        match.group(1).lower()
        for match in re.finditer(r"reddit\.com/r/[^/\s)]+/comments/([a-z0-9]{5,12})", text, re.IGNORECASE)
    }


def extract_twitter_tweet_ids_from_text(text: str) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(r"(?:x|twitter)\.com/(?:i/status|[^/\s)]+/status)/(\d+)", text, re.IGNORECASE)
    }


def discussion_information_score(item: SourceItem) -> int:
    text = clean_for_translation(item.text or item.title)
    lower = text.lower()
    if low_information_item(item) or low_value_discussion_text(lower):
        return -100
    score = entity_hits(item) * 3 + keyword_hits(item)
    score += min(len(text) // 80, 5)
    if re.search(r"https?://|\b(source|data|benchmark|metric|study|paper|report)\b", lower):
        score += 3
    if re.search(r"\b(because|therefore|however|but|if|when|cost|price|latency|quality|workflow)\b", lower):
        score += 2
    if re.search(r"\d", text):
        score += 1
    if len(text) < 35:
        score -= 4
    return score


def informative_discussion_item(item: SourceItem) -> bool:
    return discussion_information_score(item) >= 3


def selected_replies(thread: DiscussionThread) -> list[SourceItem]:
    useful = [
        reply
        for reply in thread.replies
        if reply.text
        and informative_discussion_item(reply)
    ]
    return sorted(useful, key=lambda item: (discussion_information_score(item), item.score), reverse=True)[
        :THREAD_REPLY_LIMIT
    ]


def flatten_threads(threads: list[DiscussionThread]) -> list[SourceItem]:
    flattened: list[SourceItem] = []
    seen: set[str] = set()
    for thread in threads:
        for item in [thread.root] + selected_replies(thread):
            if item.item_id in seen:
                continue
            seen.add(item.item_id)
            flattened.append(item)
    return flattened


def thread_relevance(thread: DiscussionThread) -> int:
    replies = selected_replies(thread)
    text = " ".join([thread.root.text] + [reply.text for reply in replies]).lower()
    score = thread.root.score + len(replies) * 3
    score += entity_hits(thread.root) * 18 + keyword_hits(thread.root) * 8
    score += sum(entity_hits(reply) * 10 + keyword_hits(reply) * 4 for reply in replies)
    score += min(len(text) // 180, 12)
    if thread.root.score < THREAD_MIN_SCORE:
        score -= 80
    if thread.root.score < THREAD_MIN_SCORE and not passes_relevance_gate(thread.root) and not high_signal_replies(thread):
        score -= 100
    if is_low_score_thread(thread):
        score -= 80
    return score


def discussion_threads_for_topic(topic: SourceItem, items: list[SourceItem]) -> list[DiscussionThread]:
    post_id = reddit_post_id(topic)
    if not post_id:
        return []
    ordered = [
        item
        for item in items
        if item.kind.startswith("discussion-L")
        and item.parent_post_id == post_id
        and item.text
        and not low_information_item(item)
    ]
    threads: list[DiscussionThread] = []
    current: DiscussionThread | None = None
    for item in ordered:
        if item.kind == "discussion-L0":
            current = DiscussionThread(root=item)
            threads.append(current)
            continue
        if current is not None and item.kind in {"discussion-L1", "discussion-L2"}:
            current.replies.append(item)

    primary_candidates = [
        thread
        for thread in threads
        if (thread.root.score >= THREAD_MIN_SCORE and informative_discussion_item(thread.root))
        or high_signal_replies(thread)
    ]
    low_score_candidates = [
        thread
        for thread in threads
        if thread not in primary_candidates
        and is_informative_low_score_thread(thread)
    ]
    candidates = sorted(primary_candidates, key=thread_relevance, reverse=True)
    if len(candidates) < THREAD_LIMIT:
        candidates.extend(
            sorted(low_score_candidates, key=thread_relevance, reverse=True)[
                : min(LOW_SCORE_THREAD_LIMIT, THREAD_LIMIT - len(candidates))
            ]
        )
    return candidates[:THREAD_LIMIT]


def filter_items_with_hot_discussion(
    items: list[SourceItem],
    topic_threads: dict[str, list[DiscussionThread]],
) -> tuple[list[SourceItem], int]:
    if not REQUIRE_HOT_DISCUSSION:
        return items, 0
    kept: list[SourceItem] = []
    dropped = 0
    for item in items:
        if item.platform != "Reddit" or item.kind != "post":
            kept.append(item)
            continue
        if topic_threads.get(item.item_id):
            kept.append(item)
        elif keep_without_hot_discussion(item):
            kept.append(item)
        else:
            dropped += 1
    return kept, dropped


def keep_without_hot_discussion(item: SourceItem) -> bool:
    return passes_relevance_gate(item) and (
        very_hot_item(item) or high_relevance_subreddit_discussion(item)
    )


def is_low_score_thread(thread: DiscussionThread) -> bool:
    return thread.root.score < THREAD_MIN_SCORE and not high_signal_replies(thread)


def is_informative_low_score_thread(thread: DiscussionThread) -> bool:
    return (
        is_low_score_thread(thread)
        and len(thread.root.text) >= LOW_SCORE_THREAD_MIN_LENGTH
        and passes_relevance_gate(thread.root)
    )


def high_signal_replies(thread: DiscussionThread) -> list[SourceItem]:
    return [
        reply
        for reply in selected_replies(thread)
        if reply.score >= THREAD_MIN_SCORE
    ]


def all_thread_items(topics: list[SourceItem], items: list[SourceItem]) -> list[SourceItem]:
    selected: list[SourceItem] = []
    seen: set[str] = set()
    for topic in topics:
        for comment in flatten_threads(discussion_threads_for_topic(topic, items)):
            if comment.item_id in seen:
                continue
            seen.add(comment.item_id)
            selected.append(comment)
    return selected


def x_thread_items_for_tweet(tweet: SourceItem, items: list[SourceItem]) -> list[SourceItem]:
    tweet_id = twitter_tweet_id(tweet)
    if not tweet_id:
        return []
    thread_items = [
        item
        for item in items
        if item.platform == "X/Twitter"
        and item.parent_post_id == tweet_id
        and item.item_id != f"twitter-thread-{tweet_id}"
        and item.text
        and not x_low_value_item(item)
    ]
    author_replies = [
        item
        for item in thread_items
        if item.kind == "x-author-reply"
    ]
    external_replies = [
        item
        for item in thread_items
        if item.kind == "x-reply"
        and informative_discussion_item(item)
        and (
            from_quality_x_author(item)
            or x_has_link_or_media(item)
        )
    ]
    selected = sorted(author_replies, key=x_signal_score, reverse=True)[:5]
    selected.extend(sorted(external_replies, key=x_signal_score, reverse=True)[:X_THREAD_REPLY_LIMIT])
    return dedupe_items(selected)


def x_thread_items_for_tweets(tweets: list[SourceItem], items: list[SourceItem]) -> list[SourceItem]:
    thread_items: list[SourceItem] = []
    for tweet in tweets:
        thread_items.extend(x_thread_items_for_tweet(tweet, items))
    return dedupe_items(thread_items)


def select_article_candidates(items: list[SourceItem]) -> list[ArticleCandidate]:
    candidates: list[ArticleCandidate] = []
    seen_urls: set[str] = set()
    historical_events = historical_source_event_keys(report_date())
    historical_families = historical_event_family_tokens(report_date())
    historical_x_posts = historical_x_post_keys(report_date())
    historical_urls = historical_article_urls(report_date())
    for item in items:
        if not is_article_source_item(item):
            continue
        if source_event_key(item) in historical_events:
            continue
        if event_family_token(item_text(item)) in historical_families:
            continue
        if matches_historical_x_sequence(item, historical_x_posts):
            continue
        for url in candidate_article_urls(item):
            normalized_url = normalize_article_url(url)
            if not normalized_url or normalized_url in seen_urls:
                continue
            if normalized_url in historical_urls:
                continue
            if blocked_article_url(normalized_url):
                continue
            score = article_candidate_score(item, normalized_url)
            if score < ARTICLE_MIN_SCORE:
                continue
            seen_urls.add(normalized_url)
            candidates.append(
                ArticleCandidate(
                    item=item,
                    article_url=normalized_url,
                    title=article_title(item, normalized_url),
                    score=score,
                    reason=article_reason(item, normalized_url),
                    discussion_items=article_discussion_items(item, items),
                )
            )
    candidates = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)
    candidates = resolve_and_dedupe_article_candidates(candidates[:ARTICLE_CANDIDATE_LIMIT])
    candidates = group_article_candidates(candidates)
    candidates = diversify_article_candidates(candidates)
    for candidate in candidates[: min(ARTICLE_FETCH_LIMIT, ARTICLE_LIMIT)]:
        if ARTICLE_FETCH_ENABLED:
            candidate.article_text, candidate.fetch_error = fetch_article_text(candidate.article_url)
    return candidates[:ARTICLE_LIMIT]


def resolve_and_dedupe_article_candidates(candidates: list[ArticleCandidate]) -> list[ArticleCandidate]:
    by_url: dict[str, ArticleCandidate] = {}
    historical_urls = historical_article_urls(report_date())
    for candidate in candidates:
        original_url = candidate.article_url
        resolved_url = resolve_article_url(candidate.article_url)
        if not resolved_url or article_domain(resolved_url) == "t.co" or blocked_article_url(resolved_url):
            continue
        if resolved_url in historical_urls:
            continue
        candidate.article_url = resolved_url
        if article_domain(original_url) == "t.co":
            candidate.title = article_title(candidate.item, resolved_url)
        existing = by_url.get(resolved_url)
        if existing is None or candidate.score > existing.score:
            if existing is not None:
                candidate.related_links.extend(existing.related_links)
            by_url[resolved_url] = candidate
        elif (candidate.title, candidate.article_url) not in existing.related_links:
            existing.related_links.append((candidate.title, candidate.article_url))
    return sorted(by_url.values(), key=lambda candidate: candidate.score, reverse=True)


def group_article_candidates(candidates: list[ArticleCandidate]) -> list[ArticleCandidate]:
    grouped: list[ArticleCandidate] = []
    first_by_group: dict[str, ArticleCandidate] = {}
    for candidate in candidates:
        key = article_cluster_key(candidate)
        primary = first_by_group.get(key)
        if primary is None:
            grouped.append(candidate)
            first_by_group[key] = candidate
            continue
        related = (candidate.title, candidate.article_url)
        if related not in primary.related_links:
            primary.related_links.append(related)
    return grouped


def diversify_article_candidates(candidates: list[ArticleCandidate]) -> list[ArticleCandidate]:
    selected: list[ArticleCandidate] = []
    source_counts: dict[str, int] = {}
    for candidate in candidates:
        source = article_publisher_key(candidate)
        if source_counts.get(source, 0) >= ARTICLE_AUTHOR_LIMIT:
            continue
        source_counts[source] = source_counts.get(source, 0) + 1
        selected.append(candidate)
        if len(selected) >= ARTICLE_LIMIT:
            break
    return selected


def article_publisher_key(candidate: ArticleCandidate) -> str:
    if candidate.item.platform == "X/Twitter" and candidate.item.author:
        return f"x:{x_author_key(candidate.item)}"
    return f"domain:{article_domain(candidate.article_url)}"


def article_cluster_key(candidate: ArticleCandidate) -> str:
    family = event_family_token(item_text(candidate.item))
    if family:
        return f"event:{family}"
    event_key = event_family_key(candidate.item)
    if event_key:
        return event_key
    title = re.sub(r"\b(technical report|report|paper|blog|research|part \d+)\b", "", candidate.title.lower())
    title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", title).strip()
    words = title.split()
    return " ".join(words[:5]) or article_domain(candidate.article_url)


def is_article_source_item(item: SourceItem) -> bool:
    if not is_fresh_article_item(item):
        return False
    if not item.title and not item.text:
        return False
    if low_information_item(item) or skip_by_quality_filter(f"{item.title}\n{item.text}"):
        return False
    if item.platform == "Reddit":
        return item.kind == "post" and passes_relevance_gate(item)
    if item.platform == "X/Twitter":
        if item.kind == "tweet":
            return passes_relevance_gate(item) or from_quality_x_author(item) or x_has_link_or_media(item)
        return item.kind in {"x-author-reply", "x-reply"} and (
            from_quality_x_author(item) or x_has_link_or_media(item)
        )
    return False


def candidate_article_urls(item: SourceItem) -> list[str]:
    urls: list[str] = []
    for value in [
        item.source_url,
        str(item.raw.get("card.url", "")),
        str(item.raw.get("url_overridden_by_dest", "")),
    ]:
        if value:
            urls.append(value)
    urls.extend(extract_urls(f"{item.title}\n{item.raw.get('text', '')}\n{item.raw.get('selftext', '')}"))
    return urls


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s)\]}>\"']+", text):
        urls.append(match.group(0).rstrip(".,;:!?，。；：）)]"))
    return urls


def normalize_article_url(url: str) -> str:
    url = clean_url_text(url)
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if not parsed.netloc:
        return ""
    query_pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not is_tracking_query_param(key)
    ]
    query = urllib.parse.urlencode(query_pairs)
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            query,
            "",
        )
    )


def resolve_article_url(url: str) -> str:
    if article_domain(url) != "t.co":
        return url
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "daily-news/1.0"},
            method="HEAD",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
        return normalize_article_url(final_url) or url
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError):
        pass
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "daily-news/1.0"},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
        return normalize_article_url(final_url) or url
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError):
        return url


def clean_url_text(url: str) -> str:
    cleaned = str(url or "").strip().strip("<>").replace("\\_", "_")
    cleaned = cleaned.replace("&amp;", "&")
    return cleaned


def is_tracking_query_param(name: str) -> bool:
    lower = name.lower()
    return (
        lower.startswith("utm_")
        or lower in {"ref", "ref_src", "attribution_id", "attribution_type", "source", "fbclid", "gclid"}
    )


def blocked_article_url(url: str) -> bool:
    domain = article_domain(url)
    if not domain:
        return True
    if domain in ARTICLE_BLOCKED_DOMAINS:
        return True
    if any(domain.endswith(f".{blocked}") for blocked in ARTICLE_BLOCKED_DOMAINS):
        return True
    path = urllib.parse.urlsplit(url).path.lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".avi", ".zip"))


def article_domain(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def article_candidate_score(item: SourceItem, url: str) -> int:
    score = relevance_score(item)
    score += int(math.log1p(max(item.score, 0)) * 20)
    score += int(math.log1p(max(item.comments, 0)) * 18)
    if item.platform == "X/Twitter":
        score += min(as_int(item.raw.get("views")) // 20000, 60)
        if from_quality_x_author(item):
            score += 80
    if item.platform == "Reddit" and item.source_url:
        score += 35
    domain = article_domain(url)
    if domain in ARTICLE_QUALITY_DOMAINS or any(domain.endswith(f".{trusted}") for trusted in ARTICLE_QUALITY_DOMAINS):
        score += 70
    text = f"{item.title}\n{item.text}\n{url}".lower()
    if re.search(r"\b(paper|research|report|blog|benchmark|release notes?|github|docs?|case study|analysis)\b", text):
        score += 35
    if re.search(r"\b(subscribe|newsletter|course|coupon|deal|sponsor)\b", text):
        score -= 80
    age_days = item_age_days(item)
    if age_days is not None:
        score += max(0, ARTICLE_FRESHNESS_DAYS - max(age_days, 0)) * 8
    return score


def article_title(item: SourceItem, url: str) -> str:
    title = first_value(item.raw, ["card.title", "title"])
    if title:
        return compact(title, 160)
    url_title = title_from_article_url(url)
    if url_title:
        return url_title
    if item.title:
        return compact(item.title, 160)
    return article_domain(url) or "未命名文章"


def title_from_article_url(url: str) -> str:
    try:
        path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    except ValueError:
        return ""
    filename = Path(path).name
    if not filename:
        return ""
    stem = filename.rsplit(".", 1)[0]
    if not stem:
        return ""
    title = re.sub(r"[_-]+", " ", stem).strip()
    return compact(title, 120)


def article_reason(item: SourceItem, url: str) -> str:
    reasons: list[str] = []
    domain = article_domain(url)
    if domain in ARTICLE_QUALITY_DOMAINS or any(domain.endswith(f".{trusted}") for trusted in ARTICLE_QUALITY_DOMAINS):
        reasons.append("来源可信度高")
    if item.platform == "X/Twitter" and from_quality_x_author(item):
        reasons.append("高质量 X 作者发布或转发")
    if item.platform == "Reddit" and item.comments >= 50:
        reasons.append("Reddit 有较多讨论")
    if entity_hits(item) > 0:
        reasons.append("命中核心 AI/开发实体")
    if not reasons:
        reasons.append("相关度和热度达到文章精选门槛")
    return "；".join(reasons)


def article_discussion_items(item: SourceItem, all_items: list[SourceItem]) -> list[SourceItem]:
    if item.platform == "Reddit":
        threads = discussion_threads_for_topic(item, all_items)
        return flatten_threads(threads)[:4]
    if item.platform == "X/Twitter" and item.kind == "tweet":
        return [
            reply
            for reply in x_thread_items_for_tweet(item, all_items)
            if reply.kind == "x-author-reply" or from_quality_x_author(reply) or reply.score > 0
        ][:4]
    return []


def article_cache_path() -> Path:
    return RAW_DIR / report_date() / "article-cache.json"


def load_article_cache() -> dict[str, dict[str, str]]:
    path = article_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def save_article_cache(cache: dict[str, dict[str, str]]) -> None:
    path = article_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_article_text(url: str) -> tuple[str, str]:
    cache = load_article_cache()
    cache_key = f"jina:{hashlib.sha256(url.encode('utf-8')).hexdigest()}"
    cached = cache.get(cache_key)
    if cached:
        cached_text = cached.get("text", "")
        if cached_text and article_fetch_error_text(cached_text):
            return "", first_article_fetch_error_line(cached_text)
        return cached_text, cached.get("error", "")
    try:
        request = urllib.request.Request(
            f"https://r.jina.ai/{url}",
            headers={"User-Agent": "daily-news/1.0"},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            text = response.read().decode("utf-8", errors="replace")
        text = clean_article_text(text)
        if article_fetch_error_text(text):
            error = first_article_fetch_error_line(text)
            cache[cache_key] = {"text": "", "error": error}
            save_article_cache(cache)
            return "", error
        cache[cache_key] = {"text": compact(text, 6000), "error": ""}
        save_article_cache(cache)
        return cache[cache_key]["text"], ""
    except (
        urllib.error.URLError,
        TimeoutError,
        socket.timeout,
        http.client.RemoteDisconnected,
        http.client.HTTPException,
        UnicodeDecodeError,
    ) as exc:
        error = f"{type(exc).__name__}: {exc}"
        cache[cache_key] = {"text": "", "error": error}
        save_article_cache(cache)
        return "", error


def clean_article_text(text: str) -> str:
    cleaned = re.sub(r"(?im)^Title:\s*", "", text)
    cleaned = re.sub(r"(?im)^URL Source:\s*\S+\s*", "", cleaned)
    cleaned = re.sub(r"(?im)^Markdown Content:\s*", "", cleaned)
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def article_fetch_error_text(text: str) -> bool:
    lower = text.lower()
    return "target url returned error 404" in lower or "404 error" in lower or "404: not found" in lower


def first_article_fetch_error_line(text: str) -> str:
    for line in text.splitlines():
        if "404" in line:
            return compact(line, 180)
    return "article fetch returned 404"


def article_source_item(candidate: ArticleCandidate) -> SourceItem:
    text_parts = [
        candidate.article_text,
        candidate.item.text,
    ]
    text = "\n\n".join(part for part in text_parts if part)
    if not text:
        text = candidate.item.title
    digest = compact(clean_for_translation(text), 5000)
    item_id = f"article-{hashlib.sha256(candidate.article_url.encode('utf-8')).hexdigest()[:16]}"
    return SourceItem(
        item_id=item_id,
        platform=candidate.item.platform,
        kind="article",
        title=candidate.title,
        text=digest,
        author=candidate.item.author,
        url=candidate.article_url,
        source_url=candidate.item.url,
        score=candidate.score,
        comments=candidate.item.comments,
        created_at=candidate.item.created_at,
        parent_post_id=candidate.item.item_id,
        raw={"source_platform": candidate.item.platform, "reason": candidate.reason},
    )


def item_text(item: SourceItem) -> str:
    return " ".join([item.title, item.text, item.source_url, str(item.raw.get("subreddit", ""))]).lower()


def group_key(item: SourceItem) -> str:
    text = item_text(item)
    if re.search(r"\b(qwen|llama\.cpp|gguf|mtp|turboquant|speculative decoding|tts|vocoder|speech|inflect|tiny|local|inference|embedded|wasm|esp32)\b", text):
        return "local-small-models"
    if re.search(
        r"\b(acquisition|acquire[sd]?|buys?|merger|buyout|valuation|acquihire)\b",
        text,
    ) and re.search(r"\b(ai|cursor|coding|model|agent|startup)\b", text):
        return "ai-industry-deals"
    if "openai" in text and re.search(
        r"\b(ipo|valuation|investors?|liquidity|costs?|pricing|revenue|fees?|panicking|anthropic|xai|financial|billions?|losses?|losing|burn)\b",
        text,
    ):
        return "openai-capital-pressure"
    if re.search(r"\b(ai_agents|agent|agents|agentic|workflow automation|tool use|mcp|browser use|cursor|windsurf|claude code)\b", text):
        return "agent-tooling"
    if re.search(r"\b(devday|agentkit|apps sdk|sora api|gpt-oss|gpt-5|gpt-4o)\b", text):
        return "openai-platform"
    if re.search(r"\b(codex|jira|coding assistant|ai coding|developer productivity|pull request|pdb)\b", text):
        return "ai-coding-workflow"
    if re.search(r"\b(benchmark|leaderboard|eval|model release|release|weights|dataset|fine[- ]?tune|training|huggingface)\b", text):
        return "model-release-benchmarks"
    if "saas" in text or "indie" in text or "startup" in text:
        return "ai-saas-indie"
    return source_key(item)


def group_title(key: str, item: SourceItem) -> str:
    titles = {
        "ai-industry-deals": "AI 公司并购、估值与行业整合",
        "openai-capital-pressure": "OpenAI 商业化与资本压力",
        "ai-coding-workflow": "AI coding 工具进入企业工作流",
        "agent-tooling": "Agent 工具链与自动化工作流",
        "openai-platform": "OpenAI 平台生态与模型策略",
        "model-release-benchmarks": "模型发布、Benchmark 与开源生态",
        "local-small-models": "本地小模型与低成本推理",
        "ai-saas-indie": "AI SaaS 与独立开发机会",
    }
    return titles.get(key, item.title or compact(item.text, 80) or "未命名主题")


def build_topic_groups(
    selected: list[SourceItem],
    topic_threads: dict[str, list[DiscussionThread]],
) -> tuple[list[TopicGroup], list[SourceItem]]:
    groups_by_key: dict[str, TopicGroup] = {}
    short_items: list[SourceItem] = []

    for item in selected:
        threads = topic_threads.get(item.item_id, [])
        key = group_key(item)
        if should_be_short_item(item, threads) and (not threads or not keep_weak_item_in_group(item, key)):
            short_items.append(item)
            continue
        group = groups_by_key.get(key)
        if group is None:
            group = TopicGroup(key=key, title=group_title(key, item))
            groups_by_key[key] = group
        group.items.append(item)
        group.threads_by_item_id[item.item_id] = threads

    groups = sorted(groups_by_key.values(), key=group_relevance, reverse=True)
    focus_groups = groups[:FOCUS_GROUP_LIMIT]
    overflow_items = [item for group in groups[FOCUS_GROUP_LIMIT:] for item in group.items]
    short_items.extend(overflow_items)
    return focus_groups, sorted(short_items, key=relevance_score, reverse=True)[:SHORT_ITEM_LIMIT]


def should_be_short_item(item: SourceItem, threads: list[DiscussionThread]) -> bool:
    if item.platform == "X/Twitter":
        return True
    if not threads:
        return True
    informative_threads = [
        thread
        for thread in threads
        if thread.root.score >= THREAD_MIN_SCORE
        or selected_replies(thread)
    ]
    return not informative_threads


def keep_weak_item_in_group(item: SourceItem, key: str) -> bool:
    if item.platform != "Reddit":
        return False
    if key == "openai-capital-pressure":
        return True
    if key in {"ai-industry-deals", "openai-platform", "agent-tooling", "model-release-benchmarks", "ai-saas-indie"}:
        return item.score >= 20 or item.comments >= 10
    return False


def group_relevance(group: TopicGroup) -> int:
    item_score = max((relevance_score(item) for item in group.items), default=0)
    thread_score = sum(len(threads) * 8 for threads in group.threads_by_item_id.values())
    reply_score = sum(
        len(selected_replies(thread)) * 4
        for threads in group.threads_by_item_id.values()
        for thread in threads
    )
    source_bonus = min(len(group.items), 3) * 15
    diversity_bonus = 20 if group.key in {"agent-tooling", "model-release-benchmarks", "ai-saas-indie"} else 0
    return item_score + thread_score + reply_score + source_bonus + diversity_bonus


def group_thread_items(group: TopicGroup) -> list[SourceItem]:
    items: list[SourceItem] = []
    seen: set[str] = set()
    for threads in group.threads_by_item_id.values():
        for item in flatten_threads(threads):
            if item.item_id in seen:
                continue
            seen.add(item.item_id)
            items.append(item)
    return items


def group_all_text(group: TopicGroup) -> str:
    parts = [item.title + " " + item.text for item in group.items]
    parts.extend(item.text for item in group_thread_items(group))
    return " ".join(parts).lower()


def group_signal(group: TopicGroup) -> str:
    text = group_all_text(group)
    if group.key == "ai-industry-deals":
        return "讨论重点是 AI 编程公司的并购估值、人才整合和行业集中度，而不是工具功能本身。"
    if group.key == "openai-capital-pressure":
        return "核心不是单条新闻本身，而是社区开始把 AI 能力进展和单位经济、用量控制、IPO 流动性压力放在一起讨论。"
    if group.key == "ai-coding-workflow":
        return "讨论焦点正在从“AI 能不能写代码”转向企业能否把 AI coding 纳入上下文、评审、测试和交付指标。"
    if group.key == "agent-tooling":
        return "Agent 讨论的重点正在从概念转向工具调用、工作流编排、可靠性和可接管性。"
    if group.key == "local-small-models":
        return "小模型价值不只在模型参数少，而在能否降低部署依赖、离线运行并进入边缘设备或本地助手。"
    if group.key == "model-release-benchmarks":
        return "模型发布和 benchmark 讨论的价值在于识别真实能力边界，而不是只看榜单名次。"
    if group.key == "openai-platform":
        return "开发者关注 OpenAI 平台开放性、模型保留、调试能力和新 API 细节，说明生态锁定和可控性是主要分歧。"
    if group.key == "ai-saas-indie":
        return "AI SaaS 与独立开发讨论适合观察真实付费场景、获客难点和小团队可做的细分机会。"
    if re.search(r"\b(cost|price|pricing|roi|revenue)\b", text):
        return "社区在用成本和 ROI 视角重新评估 AI 产品价值。"
    return "这个主题在多个来源或讨论线程中重复出现，值得作为今日观察项。"


def group_viewpoint_breakdown(group: TopicGroup) -> list[str]:
    text = group_all_text(group)
    if group.key == "ai-industry-deals":
        return [
            "支持/机会：并购可为成熟 AI 开发工具带来算力、分发渠道和企业客户。",
            "反对/风险：高估值、人才流失和收购方整合能力可能削弱产品独立性。",
            "分歧点：社区争论的是交易价格是否反映真实收入和技术壁垒，还是资本泡沫。",
            "可验证线索：跟踪交易结构、收入倍数、人员留任条款、产品路线和客户迁移。",
        ]
    if group.key == "ai-coding-workflow":
        return [
            "支持/机会：社区已看到 AI Agent 处理小型 Jira 工单、生成 PR、补测试等可观察工作流。",
            "反对/风险：复杂业务上下文、烂工单、跨仓库依赖和代码审查负担仍是主要瓶颈。",
            "分歧点：有人认为工具会放大开发效率，也有人认为它只是把工作转移到审查和返工上。",
            "可验证线索：跟踪部署频率、周期时间、缺陷率、PR 体积、review 时间和人工返工比例。",
        ]
    if group.key == "openai-capital-pressure":
        return [
            "支持/机会：企业预算、用量控制和 AI 成本治理会变成真实需求，而不只是财务话题。",
            "反对/风险：如果闭源模型价格上涨，需求可能转向开源/本地模型或更严格的模型路由。",
            "分歧点：一派认为 AI 公司只是烧钱泡沫，另一派认为真实使用数据和规模效应仍可能摊薄成本。",
            "可验证线索：跟踪 IPO 文件、融资抵押折扣、企业用量限制、API 定价和基础设施成本口径。",
        ]
    if group.key == "agent-tooling":
        return [
            "支持/机会：Agent 工具链开始围绕浏览器、代码库、CLI、MCP 和多步骤任务形成真实工作流。",
            "反对/风险：长任务可靠性、权限边界、失败恢复和人工接管仍是主要阻碍。",
            "分歧点：有人把 Agent 看成自动化入口，也有人认为当前更适合做半自动助手。",
            "可验证线索：跟踪任务成功率、人工接管次数、工具调用日志、权限设计和端到端耗时。",
        ]
    if group.key == "local-small-models":
        return [
            "支持/机会：极小 TTS 模型说明本地语音、离线助手、边缘设备仍有可探索空间。",
            "反对/风险：模型小不等于部署轻，PyTorch/CUDA 依赖、声码器质量和设备资源会限制落地。",
            "分歧点：社区一边认可参数效率，一边追问 ONNX、轻量 runtime、ESP32 等真实部署问题。",
            "可验证线索：跟踪 ONNX 版本、端侧内存占用、延迟、音质样例、训练预算和设备兼容性。",
        ]
    if group.key == "model-release-benchmarks":
        return [
            "支持/机会：新模型、权重、数据集和 benchmark 能暴露哪些能力正在商品化。",
            "反对/风险：榜单成绩可能掩盖成本、延迟、上下文、许可证和真实任务表现。",
            "分歧点：社区常在“指标进步”和“实际可用性”之间出现分歧。",
            "可验证线索：跟踪复现实验、许可证、推理成本、模型尺寸、真实任务反馈和开源实现。",
        ]
    if group.key == "openai-platform":
        return [
            "支持/机会：开发者在追问 Sora API、Apps SDK、AgentKit、Codex 和 GPT-OSS 的具体能力边界。",
            "反对/风险：模型保留、调试能力、平台开放性和用户自由会影响开发者信任。",
            "可验证线索：跟踪官方文档、API 限制、模型可用性、调试工具和迁移成本。",
        ]
    if group.key == "ai-saas-indie":
        return [
            "支持/机会：小团队可以从垂直流程、内部工具、成本治理和内容/销售自动化切入。",
            "反对/风险：同质化 wrapper、获客成本、留存和愿付费问题会快速淘汰弱产品。",
            "分歧点：有人看重快速上线，也有人强调必须绑定真实业务流程和明确 ROI。",
            "可验证线索：跟踪 MRR、留存、获客渠道、用户访谈、替代方案和实际节省时间。",
        ]
    lines: list[str] = []
    if re.search(r"\b(open source|local|tiny|offline|edge|esp32|wasm)\b", text):
        lines.append("支持/机会：开源、本地化和轻量部署被视为对冲高价闭源服务的路径。")
    if re.search(r"\b(productivity|faster|pull request|tests?|workflow|metrics|enterprise)\b", text):
        lines.append("支持/机会：AI coding 已能在小任务、测试、PR 和企业流程里产生可观察的效率提升。")
    if re.search(r"\b(cost|price|pricing|burn|liquidity|investors?|ipo|valuation)\b", text):
        lines.append("反对/风险：成本、烧钱、IPO 和投资人退出压力可能影响工具价格与服务稳定性。")
    if re.search(r"\b(wrong|fail|bug|limitation|quality|review|context|doesn.?t work|not work)\b", text):
        lines.append("反对/风险：社区仍反复质疑上下文、代码质量、可审查性和复杂任务可靠性。")
    if re.search(r"\b(benchmark|metric|source|link|evidence|how|why|what)\b", text):
        lines.append("可验证线索：继续跟踪 benchmark、企业交付指标、成本口径、训练/部署细节和原始公告。")
    if not lines:
        lines.append("未解问题：当前讨论更多是情绪和早期反馈，仍需要更多可验证数据。")
    return lines[:5]


def source_list(group: TopicGroup) -> str:
    links = [format_source_link(item) for item in group.items]
    return "；".join(links)


def dedupe_items(items: list[SourceItem]) -> list[SourceItem]:
    deduped: list[SourceItem] = []
    seen: set[str] = set()
    for item in items:
        if item.item_id in seen:
            continue
        seen.add(item.item_id)
        deduped.append(item)
    return deduped


def translate_with_openai(items: list[SourceItem]) -> dict[str, Enrichment]:
    if not USE_LLM:
        return {}
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            item.item_id: Enrichment(
                error="OPENAI_API_KEY is not set; skipped automatic translation.",
            )
            for item in items
        }

    payload_items = [
        {
            "id": item.item_id,
            "platform": item.platform,
            "kind": item.kind,
            "title": item.title,
            "text": item.text,
            "url": item.url or item.source_url,
            "score": item.score,
            "comments": item.comments,
        }
        for item in items
    ]
    prompt = {
        "task": "Translate and analyze technical community items for a Chinese daily intelligence report.",
        "requirements": [
            "Translate faithfully into Simplified Chinese. Do not add facts that are not in the source.",
            "Preserve product names, model names, repository names, company names, numbers, URLs, and technical terms when appropriate.",
            "If the source is ambiguous, say 原文含糊 and keep the ambiguity.",
            "signal should be a one-sentence factual intelligence takeaway in Chinese.",
            "opportunity should be a cautious product/research/business opportunity in Chinese, or 空 if there is no clear opportunity.",
            "Return strict JSON with key items, an array matching input IDs.",
        ],
        "items": payload_items,
    }
    body = json.dumps(
        {
            "model": OPENAI_MODEL,
            "input": [
                {
                    "role": "system",
                    "content": "You are a precise English-to-Chinese technical translator and analyst.",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "text": {"format": {"type": "json_object"}},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        return {
            item.item_id: Enrichment(error=f"OpenAI translation request failed: {exc}")
            for item in items
        }

    try:
        response_data = json.loads(raw)
        content = extract_openai_text(response_data)
        translated = json.loads(content)
        rows = translated.get("items", [])
    except (json.JSONDecodeError, AttributeError, KeyError) as exc:
        return {
            item.item_id: Enrichment(error=f"Could not parse translation response: {exc}")
            for item in items
        }

    enriched: dict[str, Enrichment] = {}
    for row in rows:
        if not isinstance(row, dict) or "id" not in row:
            continue
        enriched[str(row["id"])] = Enrichment(
            zh_title=compact(row.get("zh_title", ""), 220),
            zh_translation=compact(row.get("zh_translation", ""), 1200),
            signal=compact(row.get("signal", ""), 500),
            opportunity=compact(row.get("opportunity", ""), 500),
            confidence=compact(row.get("confidence", "medium"), 40),
            translated_by=OPENAI_MODEL,
        )
    return enriched


def should_use_openai() -> bool:
    if TRANSLATION_PROVIDER == "openai":
        return True
    if TRANSLATION_PROVIDER in {"google", "anthropic", "glm"}:
        return False
    return bool(os.environ.get("OPENAI_API_KEY")) and USE_LLM


def should_use_anthropic() -> bool:
    if TRANSLATION_PROVIDER in {"anthropic", "glm"}:
        return True
    if TRANSLATION_PROVIDER in {"openai", "google", "none"}:
        return False
    return bool(anthropic_api_key() and ANTHROPIC_BASE_URL)


def should_use_google() -> bool:
    if TRANSLATION_PROVIDER in {"openai", "anthropic", "glm"}:
        return False
    if TRANSLATION_PROVIDER == "none":
        return False
    return True


def translate_items(items: list[SourceItem]) -> dict[str, Enrichment]:
    if should_use_hybrid():
        return translate_with_hybrid(items)
    if should_use_anthropic():
        anthropic_result = translate_with_anthropic(items)
        if anthropic_result and any(not enrichment.error for enrichment in anthropic_result.values()):
            return anthropic_result
        if TRANSLATION_PROVIDER in {"anthropic", "glm"}:
            return anthropic_result
    if should_use_openai():
        openai_result = translate_with_openai(items)
        if openai_result and any(not enrichment.error for enrichment in openai_result.values()):
            return openai_result
        if TRANSLATION_PROVIDER == "openai":
            return openai_result
    if should_use_google():
        return translate_with_google(items)
    return {}


def validate_translation_coverage(
    items: list[SourceItem],
    enrichments: dict[str, Enrichment],
    document_name: str,
) -> None:
    if not items or TRANSLATION_PROVIDER == "none":
        return
    failed = [
        item
        for item in items
        if item.item_id not in enrichments
        or enrichments[item.item_id].error
        or not (enrichments[item.item_id].zh_translation or enrichments[item.item_id].zh_title)
    ]
    failure_percent = len(failed) * 100 / len(items)
    print(
        f"[daily-news] {document_name} translation coverage: "
        f"{len(items) - len(failed)}/{len(items)} succeeded",
        flush=True,
    )
    if failure_percent > TRANSLATION_MAX_FAILURE_PERCENT:
        raise RuntimeError(
            f"{document_name} translation failure rate {failure_percent:.1f}% exceeds "
            f"the {TRANSLATION_MAX_FAILURE_PERCENT}% limit; existing output was not overwritten."
        )


def should_use_hybrid() -> bool:
    return TRANSLATION_PROVIDER in {"hybrid", "glm-hybrid", "anthropic-hybrid"}


def translate_with_hybrid(items: list[SourceItem]) -> dict[str, Enrichment]:
    glm_targets = select_glm_translation_items(items)
    glm_ids = {item.item_id for item in glm_targets}
    google_targets = [item for item in items if item.item_id not in glm_ids]

    google_result = translate_with_google(google_targets) if google_targets else {}
    glm_result = translate_with_anthropic(glm_targets) if glm_targets else {}

    failed_glm = [
        item
        for item in glm_targets
        if item.item_id not in glm_result or glm_result[item.item_id].error
    ]
    if failed_glm:
        fallback_result = translate_with_google(failed_glm)
        google_result.update(fallback_result)

    combined = dict(google_result)
    for item_id, enrichment in glm_result.items():
        if not enrichment.error:
            combined[item_id] = enrichment
    return combined


def select_glm_translation_items(items: list[SourceItem]) -> list[SourceItem]:
    candidates = [
        item
        for item in items
        if should_translate_with_glm(item)
    ]
    return sorted(candidates, key=glm_translation_priority, reverse=True)[:HYBRID_GLM_ITEM_LIMIT]


def should_translate_with_glm(item: SourceItem) -> bool:
    if not anthropic_api_key() or not ANTHROPIC_BASE_URL:
        return False
    if item.platform != "Reddit":
        return False
    if item.kind == "post":
        return item.score >= 100 or item.comments >= 50
    if item.kind == "discussion-L0":
        return item.score >= 100 or (item.score >= 10 and len(item.text) >= 240)
    if item.kind in {"discussion-L1", "discussion-L2"}:
        return item.score >= 80 or (item.score >= 20 and len(item.text) >= 220)
    return False


def glm_translation_priority(item: SourceItem) -> int:
    score = min(item.score, 5000)
    if item.kind == "discussion-L0":
        score += 3000
    elif item.kind == "post":
        score += 1800 + min(item.comments, 2000)
    elif item.kind in {"discussion-L1", "discussion-L2"}:
        score += 700
    score += min(len(item.text), 2000) // 2
    return score


def anthropic_api_key() -> str:
    return (
        os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("AUTH_TOKEN")
        or ""
    )


def translate_with_anthropic(items: list[SourceItem]) -> dict[str, Enrichment]:
    api_key = anthropic_api_key()
    if not api_key:
        return {
            item.item_id: Enrichment(error="ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY is not set.")
            for item in items
        }
    if not ANTHROPIC_BASE_URL:
        return {
            item.item_id: Enrichment(error="ANTHROPIC_BASE_URL is not set.")
            for item in items
        }

    cache = load_anthropic_cache()
    enriched: dict[str, Enrichment] = {}
    uncached: list[SourceItem] = []
    for item in items:
        cached = cached_anthropic_enrichment(cache, item)
        if cached:
            enriched[item.item_id] = cached
        else:
            uncached.append(item)

    cache_changed = False
    batches = anthropic_batches(uncached)
    for batch_index, batch in enumerate(batches, 1):
        if batches:
            print(
                f"[daily-news] GLM translate batch {batch_index}/{len(batches)} ({len(batch)} items)",
                flush=True,
            )
        batch_result = translate_anthropic_batch(batch, api_key)
        failed_items = anthropic_failed_items(batch, batch_result)
        if failed_items and ANTHROPIC_RETRY_LIMIT > 1:
            for attempt in range(2, ANTHROPIC_RETRY_LIMIT + 1):
                print(
                    f"[daily-news] GLM retry {attempt}/{ANTHROPIC_RETRY_LIMIT} "
                    f"for {len(failed_items)} failed item(s)",
                    flush=True,
                )
                retry_failed: list[SourceItem] = []
                for item in failed_items:
                    retry_result = translate_anthropic_batch([item], api_key)
                    if retry_result:
                        batch_result.update(retry_result)
                    if item.item_id not in retry_result or retry_result[item.item_id].error:
                        retry_failed.append(item)
                failed_items = retry_failed
                if not failed_items:
                    break
        for item in failed_items:
            batch_result.setdefault(
                item.item_id,
                Enrichment(error="Anthropic-compatible translation returned no item."),
            )
        enriched.update(batch_result)
        batch_changed = False
        for item in batch:
            item_enrichment = enriched.get(item.item_id)
            if item_enrichment and not item_enrichment.error:
                cache[anthropic_cache_key(item)] = enrichment_to_cache(item_enrichment)
                batch_changed = True
                cache_changed = True
        if batch_changed:
            save_anthropic_cache(cache)
    if cache_changed:
        save_anthropic_cache(cache)
    return enriched


def anthropic_failed_items(
    items: list[SourceItem],
    result: dict[str, Enrichment],
) -> list[SourceItem]:
    return [
        item
        for item in items
        if item.item_id not in result or result[item.item_id].error
    ]


def anthropic_batches(items: list[SourceItem]) -> list[list[SourceItem]]:
    batches: list[list[SourceItem]] = []
    current: list[SourceItem] = []
    for item in items:
        if item.kind == "discussion-L0":
            if current:
                batches.append(current)
                current = []
            batches.append([item])
            continue
        current.append(item)
        if len(current) >= ANTHROPIC_BATCH_SIZE:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def translate_anthropic_batch(items: list[SourceItem], api_key: str) -> dict[str, Enrichment]:
    item_by_id = {item.item_id: item for item in items}
    payload_items = [
        {
            "id": item.item_id,
            "platform": item.platform,
            "kind": item.kind,
            "title": clean_for_translation(item.title),
            "text": compact(clean_for_translation(item.text), translation_width(item)),
            "url": item.url or item.source_url,
            "score": item.score,
            "comments": item.comments,
        }
        for item in items
    ]
    prompt = {
        "task": "Translate Reddit and X/Twitter technical community content into Simplified Chinese for a daily intelligence report.",
        "requirements": [
            "Return strict JSON only, with top-level key items.",
            "Each returned item must have exactly these fields: id, zh_title, zh_translation, signal, opportunity, confidence.",
            "zh_translation must contain the full Chinese translation of the source title/text.",
            "Translate faithfully. Do not add facts not present in the source.",
            "Preserve technical terms and product/model names in English when that is clearer: LLM, AI Agent, Codex, Jira, PR, ONNX, PyTorch, CUDA, Claude, ChatGPT.",
            "For long Reddit comments or X/Twitter threads, preserve the reasoning chain and conclusion. Do not over-summarize.",
            "Use natural Chinese suitable for a technical intelligence report, not literal machine-translation style.",
            "signal should be a one-sentence factual takeaway in Chinese.",
            "opportunity should be cautious and concrete, or 空.",
        ],
        "output_schema": {
            "items": [
                {
                    "id": "same id as input",
                    "zh_title": "Chinese title or short label",
                    "zh_translation": "full Chinese translation",
                    "signal": "one-sentence Chinese takeaway",
                    "opportunity": "concrete opportunity or 空",
                    "confidence": "high|medium|low",
                }
            ]
        },
        "items": payload_items,
    }
    body = json.dumps(
        {
            "model": ANTHROPIC_MODEL,
            "max_tokens": 8192,
            "temperature": 0.1,
            **({"thinking": {"type": "disabled"}} if ANTHROPIC_DISABLE_THINKING else {}),
            "system": "You are a precise English-to-Chinese technical translator and analyst. Output JSON only.",
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False),
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{ANTHROPIC_BASE_URL}/v1/messages",
        data=body,
        headers=anthropic_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return {
            item.item_id: Enrichment(error=f"Anthropic-compatible translation request failed: {exc}")
            for item in items
        }

    try:
        response_data = json.loads(raw)
        content = extract_anthropic_text(response_data)
        translated = json.loads(strip_json_fence(content))
        rows = translated.get("items", [])
    except (json.JSONDecodeError, AttributeError, KeyError, ValueError) as exc:
        return {
            item.item_id: Enrichment(error=f"Could not parse Anthropic-compatible translation response: {exc}")
            for item in items
        }

    enriched: dict[str, Enrichment] = {}
    for row in rows:
        if not isinstance(row, dict) or "id" not in row:
            continue
        item = item_by_id.get(str(row["id"]))
        width = translation_width(item) if item else DISCUSSION_TRANSLATION_WIDTH
        enriched[str(row["id"])] = Enrichment(
            zh_title=postprocess_translation(compact(row.get("zh_title", ""), 220)),
            zh_translation=postprocess_translation(compact(row.get("zh_translation", ""), width)),
            signal=compact(row.get("signal", ""), 500),
            opportunity=compact(row.get("opportunity", ""), 500),
            confidence=compact(row.get("confidence", "medium"), 40),
            translated_by=ANTHROPIC_MODEL,
        )
    return enriched


def anthropic_headers(api_key: str) -> dict[str, str]:
    headers = {
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("AUTH_TOKEN"):
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["x-api-key"] = api_key
    return headers


def anthropic_cache_path() -> Path:
    return RAW_DIR / report_date() / "anthropic-translation-cache.json"


def load_anthropic_cache() -> dict[str, dict[str, str]]:
    path = anthropic_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, dict)
    }


def save_anthropic_cache(cache: dict[str, dict[str, str]]) -> None:
    path = anthropic_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def anthropic_cache_key(item: SourceItem) -> str:
    source = "\n".join(
        [
            ANTHROPIC_CACHE_VERSION,
            ANTHROPIC_MODEL,
            f"disable_thinking={ANTHROPIC_DISABLE_THINKING}",
            item.kind,
            clean_for_translation(item.title),
            compact(clean_for_translation(item.text), translation_width(item)),
            str(translation_width(item)),
        ]
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"anthropic:{digest}"


def cached_anthropic_enrichment(
    cache: dict[str, dict[str, str]],
    item: SourceItem,
) -> Enrichment | None:
    row = cache.get(anthropic_cache_key(item))
    if not row:
        return None
    return Enrichment(
        zh_title=row.get("zh_title", ""),
        zh_translation=row.get("zh_translation", ""),
        signal=row.get("signal", ""),
        opportunity=row.get("opportunity", ""),
        confidence=row.get("confidence", "medium"),
        translated_by=row.get("translated_by", ANTHROPIC_MODEL),
    )


def enrichment_to_cache(enrichment: Enrichment) -> dict[str, str]:
    return {
        "zh_title": enrichment.zh_title,
        "zh_translation": enrichment.zh_translation,
        "signal": enrichment.signal,
        "opportunity": enrichment.opportunity,
        "confidence": enrichment.confidence,
        "translated_by": enrichment.translated_by,
    }


def translate_with_google(items: list[SourceItem]) -> dict[str, Enrichment]:
    enriched: dict[str, Enrichment] = {}
    cache = load_translation_cache()
    cache_changed = False
    for item in items:
        title_source = clean_for_translation(item.title or compact(item.text, 100))
        body_source = clean_for_translation(item.text or item.title)
        try:
            zh_title, title_changed = cached_google_translate(cache, title_source, width=220)
            zh_body, body_changed = cached_google_translate(cache, body_source, width=translation_width(item))
            cache_changed = cache_changed or title_changed or body_changed
            enriched[item.item_id] = Enrichment(
                zh_title=postprocess_translation(zh_title),
                zh_translation=postprocess_translation(zh_body),
                signal=heuristic_signal(item),
                opportunity=heuristic_opportunity(item),
                confidence="medium",
                translated_by="google",
            )
        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            http.client.RemoteDisconnected,
            http.client.HTTPException,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            enriched[item.item_id] = Enrichment(error=f"Google Translate failed: {exc}")
    if cache_changed:
        save_translation_cache(cache)
    return enriched


def translation_width(item: SourceItem) -> int:
    if item.kind.startswith("discussion-L"):
        return DISCUSSION_TRANSLATION_WIDTH
    if item.kind == "article":
        return 2600
    return 1600


def translation_cache_path() -> Path:
    return RAW_DIR / report_date() / "translation-cache.json"


def load_translation_cache() -> dict[str, str]:
    path = translation_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def save_translation_cache(cache: dict[str, str]) -> None:
    path = translation_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def cached_google_translate(cache: dict[str, str], text: str, width: int) -> tuple[str, bool]:
    if not text:
        return "", False
    digest = hashlib.sha256(f"{width}\n{text}".encode("utf-8")).hexdigest()
    key = f"google:{digest}"
    if key in cache:
        return cache[key], False
    translated = google_translate(text, width=width)
    cache[key] = translated
    return translated, True


def postprocess_translation(text: str) -> str:
    replacements = {
        "Calude": "Claude",
        "克劳德": "Claude",
        "聊天 GPT": "ChatGPT",
        "Chat GPT": "ChatGPT",
        "Open AI": "OpenAI",
        "Claude使用费": "Claude 使用费",
        "Claude代码": "Claude Code",
        "法学硕士": "LLM",
        "大型语言模型": "LLM",
        "Jira 小票": "Jira 工单",
        "JIRA 票证": "Jira 工单",
        "Jira 票证": "Jira 工单",
        "Jira Ticket": "Jira 工单",
        "Jira 票": "Jira 工单",
        "小 Jira 票": "小 Jira 工单",
        "票据": "工单",
        "小票": "工单",
        "门票": "工单",
        "副驾驶": "Copilot",
        "专业最高+水平": "周期高位附近",
        "\\ 使用": "使用",
        "人工智能代理": "AI Agent",
        "人工智能编码": "AI coding",
        "人工智能模型": "AI 模型",
        "人工智能公司": "AI 公司",
        "人工智能工具": "AI 工具",
        "人工智能产品": "AI 产品",
    }
    processed = text
    for src, dst in replacements.items():
        processed = processed.replace(src, dst)
    return processed


def clean_for_translation(text: str) -> str:
    cleaned = text.replace("\\n", "\n")
    cleaned = cleaned.replace("\\_", "_").replace("\\~", "~")
    cleaned = re.sub(r"https://preview\.redd\.it/\S+", "", cleaned)
    cleaned = re.sub(r"https://i\.redd\.it/\S+", "", cleaned)
    cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"[*_`#>]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def google_translate(text: str, width: int = 1400) -> str:
    text = compact(text, width)
    if not text:
        return ""
    chunks = split_for_translation(text)
    translated: list[str] = []
    for chunk in chunks:
        params = urllib.parse.urlencode(
            {
                "client": "gtx",
                "sl": "auto",
                "tl": "zh-CN",
                "dt": "t",
                "q": chunk,
            }
        )
        request = urllib.request.Request(
            f"https://translate.googleapis.com/translate_a/single?{params}",
            headers={"User-Agent": "daily-news/1.0"},
        )
        with urllib.request.urlopen(request, timeout=GOOGLE_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
        translated.append(parse_google_translation(payload))
    return compact("".join(translated), width)


def split_for_translation(text: str, chunk_size: int = 1200) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break
        cut = remaining.rfind(" ", 0, chunk_size)
        if cut < chunk_size // 2:
            cut = chunk_size
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    return chunks


def parse_google_translation(payload: Any) -> str:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise ValueError("unexpected Google Translate response")
    parts: list[str] = []
    for row in payload[0]:
        if isinstance(row, list) and row and isinstance(row[0], str):
            parts.append(row[0])
    return "".join(parts)


def extract_openai_text(response_data: dict[str, Any]) -> str:
    if isinstance(response_data.get("output_text"), str):
        return response_data["output_text"]
    chunks: list[str] = []
    for output in response_data.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def extract_anthropic_text(response_data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for content in response_data.get("content", []):
        if not isinstance(content, dict):
            continue
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            chunks.append(content["text"])
    return "\n".join(chunks).strip()


def strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def fallback_enrichment(item: SourceItem, error: str = "") -> Enrichment:
    source_text = clean_for_translation(item.text or item.title)
    if TRANSLATION_PROVIDER == "none":
        return Enrichment(
            zh_title=item.title or compact(item.text, 80),
            zh_translation=compact(source_text, translation_width(item)) or item.title,
            signal=heuristic_signal(item),
            opportunity=heuristic_opportunity(item),
            confidence="low",
            translated_by="none",
            error=error,
        )
    note = compact(source_text, translation_width(item)) or item.title
    if not note:
        note = "翻译不可用，请打开来源链接查看原文。"
    return Enrichment(
        zh_title=item.title,
        zh_translation=note,
        signal=heuristic_signal(item),
        opportunity=heuristic_opportunity(item),
        confidence="low",
        translated_by="fallback",
        error=error,
    )


def heuristic_signal(item: SourceItem) -> str:
    text = f"{item.title} {item.text}".lower()
    if "open source" in text or "huggingface" in text or "released" in text:
        return "开源/本地模型方向出现可跟踪的新项目或实践反馈。"
    if re.search(r"\b(costs?|roi|investors?|ipo|valuation|economics)\b", text):
        return "社区正在讨论 AI 产品成本、ROI 或商业化压力。"
    if "codex" in text or "developer" in text or "tool" in text:
        return "开发者工具和 AI 编程工作流仍是高频讨论主题。"
    return "该条目在目标社区中获得关注，建议结合原文判断价值。"


def heuristic_opportunity(item: SourceItem) -> str:
    text = f"{item.title} {item.text}".lower()
    if "local" in text or "tiny" in text or "small" in text or "inference" in text:
        return "关注轻量化、本地化、低成本推理相关工具机会。"
    if re.search(r"\b(saas|roi|costs?|economics)\b", text):
        return "关注能量化节省成本或提升转化的 AI SaaS 工具机会。"
    if "developer" in text or "codex" in text or "cli" in text or "sdk" in text:
        return "关注开发者工作流、CLI/SDK 集成和企业内部工具机会。"
    return "空"


def discussion_summary(topic: SourceItem, thread_items: list[SourceItem]) -> str:
    if not thread_items:
        if topic.platform == "Reddit":
            return "这个主题当前没有读取到可用评论线程；可能是平台返回限制、评论较少或内容信息密度不足。"
        return "这个主题来自 X/Twitter，当前没有评论串。"

    text = " ".join([topic.title, topic.text] + [item.text for item in thread_items]).lower()
    signals: list[str] = []
    if re.search(r"\b(costs?|pricing|roi|investors?|valuation|ipo|liquidity|revenue|fees?)\b", text):
        signals.append("成本、定价、ROI 和资本市场压力")
    if re.search(r"\b(job|jobs|engineer|programmer|developer|layoff|fired|replace|adapt)\b", text):
        signals.append("开发者岗位变化和 AI 替代焦虑")
    if re.search(r"\b(enterprise|metrics|workflow|guardrails?|context|delivery|quality|review)\b", text):
        signals.append("企业落地、工作流、上下文和质量控制")
    if re.search(r"\b(local|tiny|small|inference|tts|vocoder|offline|embedded|wasm)\b", text):
        signals.append("本地化、小模型和低资源推理")
    if re.search(r"\b(invite codes?|sora-invite-codes|discord|scam|banned)\b", text):
        signals.append("邀请码、社区运营和反诈骗/风控")
    if not signals:
        signals.append("社区情绪和实际使用反馈")
    return "讨论主要集中在：" + "；".join(signals) + "。"


def mainline_summary(topics: list[SourceItem], thread_items: list[SourceItem]) -> list[str]:
    text = " ".join([item.title + " " + item.text for item in topics + thread_items]).lower()
    lines: list[str] = []
    if re.search(r"\b(ipo|valuation|liquidity|investors?|roi|costs?|fees?|revenue)\b", text):
        lines.append("AI 公司资本压力成为主线：OpenAI/Anthropic/xAI 的融资、IPO、成本和 ROI 被社区反复讨论。")
    if re.search(r"\b(coding|codex|jira|pull request|pr|developer|enterprise|workflow|context|review|quality)\b", text):
        lines.append("AI 编程工具进入企业落地阶段：讨论焦点从“能不能写代码”转向上下文、评审、质量和交付指标。")
    if re.search(r"\b(local|tiny|tts|inference|vocoder|offline|embedded|wasm|open source)\b", text):
        lines.append("小模型和本地运行仍有机会：本地语音、低资源推理、离线 Agent 是值得继续跟踪的方向。")
    if re.search(r"\b(model routing|gpt-4o|gpt-oss|gpt-5|sora|apps sdk|agentkit)\b", text):
        lines.append("OpenAI 平台生态仍在扩张，但用户对模型路由、旧模型保留和产品策略有明显分歧。")
    if not lines:
        lines.append("今日没有形成强主线，建议优先看各主题下的讨论小结和代表性讨论线程。")
    return lines[:5]


def mainline_summary_from_groups(groups: list[TopicGroup]) -> list[str]:
    key_lines = {
        "ai-industry-deals": "AI 编程工具进入并购整合阶段：社区重点讨论估值依据、人才收购和产品独立性。",
        "openai-capital-pressure": "AI 公司资本压力成为主线：OpenAI/Anthropic/xAI 的融资、IPO、成本和 ROI 被社区反复讨论。",
        "ai-coding-workflow": "AI 编程工具进入企业落地阶段：讨论焦点从“能不能写代码”转向上下文、评审、质量和交付指标。",
        "agent-tooling": "Agent 工具链讨论升温：重点转向工具调用、工作流编排、权限边界和人工接管。",
        "local-small-models": "小模型和本地运行仍有机会：本地推理、低资源部署、离线 Agent 是值得继续跟踪的方向。",
        "model-release-benchmarks": "模型发布和 benchmark 仍是高频主题：社区更关心真实任务、推理成本和复现细节。",
        "openai-platform": "OpenAI 平台生态仍在扩张，但用户对模型路由、旧模型保留和产品策略有明显分歧。",
        "ai-saas-indie": "AI SaaS 与独立开发仍适合观察真实付费场景、获客难点和小团队机会。",
    }
    lines = [key_lines[group.key] for group in groups if group.key in key_lines]
    if not lines:
        return ["今日没有形成强主线，建议优先看各主题下的讨论小结和代表性讨论线程。"]
    return lines[:5]


def report_date() -> str:
    return os.environ.get("DAILY_NEWS_DATE", dt.date.today().isoformat())


def markdown_escape(text: str) -> str:
    return text.replace("\n", " ").strip()


def link_label(item: SourceItem) -> str:
    if item.platform == "X/Twitter":
        return f"X @{item.author}" if item.author else "X/Twitter"
    if item.author:
        return f"Reddit u/{item.author}"
    return item.platform


def format_source_link(item: SourceItem) -> str:
    url = item.url or item.source_url
    if not url:
        return item.platform
    return f"[{link_label(item)}]({url})"


def format_focus_group(
    index: int,
    group: TopicGroup,
    enrichments_by_item_id: dict[str, Enrichment],
    discussion_enrichments: dict[str, Enrichment],
    x_signals: list[SourceItem] | None = None,
    x_enrichments: dict[str, Enrichment] | None = None,
    x_thread_items_by_tweet_id: dict[str, list[SourceItem]] | None = None,
    x_thread_enrichments: dict[str, Enrichment] | None = None,
) -> str:
    x_signals = x_signals or []
    x_enrichments = x_enrichments or {}
    x_thread_items_by_tweet_id = x_thread_items_by_tweet_id or {}
    x_thread_enrichments = x_thread_enrichments or {}
    thread_items = group_thread_items(group)
    lead = group.items[0]
    lead_enrichment = enrichments_by_item_id.get(lead.item_id) or fallback_enrichment(lead)
    display_title = group.title
    if group.key.startswith(("reddit:", "twitter:")) and lead_enrichment.zh_title:
        display_title = lead_enrichment.zh_title
    lines = [
        f"### {index}. {display_title}",
        "",
        f"- 来源：{source_list(group)}",
        f"- 热度：{group_heat_label(group)}",
        f"- 核心判断：{group_signal(group)}",
        f"- 讨论小结：{discussion_summary(lead, thread_items)}",
    ]
    opportunity = group_opportunity(group)
    if opportunity and opportunity != "空":
        lines.append(f"- 机会提示：{opportunity}")
    lines.extend(["", "**观点拆解**", ""])
    lines.extend(f"- {line}" for line in group_viewpoint_breakdown(group))
    lines.extend(["", "**来源摘要**", ""])
    for item in group.items:
        enriched = enrichments_by_item_id.get(item.item_id) or fallback_enrichment(item)
        summary = enriched.zh_translation or fallback_enrichment(item).zh_translation
        summary_lines = format_summary_lines(summary, SOURCE_SUMMARY_WIDTH)
        lines.append(f"- {format_source_link(item)}：{summary_lines[0]}")
        lines.extend(f"  - {line}" for line in summary_lines[1:])
        if item.source_url:
            lines.append(f"  - 关联外部链接：[{item.source_url}]({item.source_url})")
    threads = [thread for threads in group.threads_by_item_id.values() for thread in threads]
    if threads:
        lines.extend(["", "**代表性讨论线程（L0 主评论 + 相关回复）**", ""])
        for thread in sorted(threads, key=thread_relevance, reverse=True)[:THREAD_LIMIT]:
            lines.append(format_discussion_thread(thread, discussion_enrichments))
    if x_signals:
        lines.extend(["", "**X 关键信号**", ""])
        for signal in sorted(x_signals, key=x_signal_score, reverse=True)[:3]:
            tweet_id = twitter_tweet_id(signal)
            enriched = x_enrichments.get(signal.item_id) or fallback_enrichment(signal)
            lines.append(
                format_x_signal_item(
                    signal,
                    enriched,
                    x_thread_items_by_tweet_id.get(tweet_id, []),
                    x_thread_enrichments,
                )
            )
    lines.append("")
    return "\n".join(lines)


def group_heat_label(group: TopicGroup) -> str:
    scores = [item.score for item in group.items]
    comments = [item.comments for item in group.items]
    return f"{len(group.items)} 个来源，最高 score={max(scores, default=0)}, 合计 comments={sum(comments)}"


def group_opportunity(group: TopicGroup) -> str:
    text = group_all_text(group)
    if group.key == "ai-industry-deals":
        return "关注并购后的产品整合、开发者迁移、替代工具和团队留任变化。"
    if group.key == "openai-capital-pressure":
        return "关注能解释、压缩或替代高价 AI 服务的工具，以及企业 AI 成本治理。"
    if group.key == "ai-coding-workflow":
        return "关注代码库上下文、评审、测试生成、PR 质量度量和企业内部 AI coding 工作流。"
    if group.key == "agent-tooling":
        return "关注 Agent 编排、工具调用权限、浏览器/CLI 自动化、失败恢复和人工接管体验。"
    if group.key == "local-small-models":
        return "关注 ONNX/轻量推理、本地语音助手、边缘设备和低依赖部署。"
    if group.key == "model-release-benchmarks":
        return "关注能把 benchmark 结果转成真实场景评估、部署成本和选型建议的工具。"
    if group.key == "openai-platform":
        return "关注多模型路由、开源模型接入、调试工具和平台迁移成本。"
    if group.key == "ai-saas-indie":
        return "关注垂直 SaaS、低成本获客、AI 成本控制和能直接证明 ROI 的小工具。"
    if re.search(r"\b(saas|roi|cost|pricing)\b", text):
        return "关注能量化 ROI 或降低 AI 使用成本的 SaaS 工具。"
    return "空"


def format_summary_lines(text: str, width: int) -> list[str]:
    cleaned = markdown_escape(text)
    if len(cleaned) <= width:
        return [cleaned]

    lead, omitted = split_summary(cleaned, width)
    lines = [lead]
    if omitted:
        lines.append(f"后文要点：{omitted_summary(omitted)}")
    return lines


def split_summary(text: str, width: int) -> tuple[str, str]:
    if len(text) <= width:
        return text, ""
    list_boundary = list_boundary_before(text, width)
    if list_boundary >= max(80, width // 4):
        lead = text[:list_boundary].rstrip(" ，,;；")
        omitted = text[list_boundary:].lstrip(" ：:，,;；")
        return lead, omitted
    boundary = sentence_boundary_before(text, width)
    if boundary < max(80, width // 2):
        boundary = text.rfind("，", 0, width)
    if boundary < max(80, width // 2):
        boundary = text.rfind(",", 0, width)
    if boundary < max(80, width // 2):
        boundary = width
    lead = text[:boundary].rstrip(" ，,;；")
    omitted = text[boundary:].lstrip(" ，,;；")
    return lead, omitted


def list_boundary_before(text: str, width: int) -> int:
    prefix = text[:width]
    candidates: list[int] = []
    for pattern in (r"\s\\-", r"\s-\s", r"\s•\s", r"\s\d+\.\s"):
        for match in re.finditer(pattern, prefix):
            candidates.append(match.start())
    if not candidates:
        return -1
    return min(candidate for candidate in candidates if candidate >= 0)


def sentence_boundary_before(text: str, width: int) -> int:
    boundary = -1
    prefix = text[:width]
    for match in re.finditer(r"[。！？.!?]\s*", prefix):
        if is_numeric_or_list_period(prefix, match.start(), match.end()):
            continue
        boundary = match.end()
    return boundary


def is_numeric_or_list_period(text: str, start: int, end: int) -> bool:
    if text[start] != ".":
        return False
    prev_char = text[start - 1] if start > 0 else ""
    next_char = text[end] if end < len(text) else ""
    if prev_char.isdigit() and (next_char.isdigit() or next_char.isalpha()):
        return True
    if prev_char.isdigit() and next_char.isspace():
        return True
    return False


def omitted_summary(text: str) -> str:
    lower = text.lower()
    points: list[str] = []
    if re.search(r"\bu/[a-z0-9_-]+\b", lower) or re.search(r"dmitry|alexander|ruth|christina|rohan|olivia", lower):
        return "后文主要是 AMA 参与人员名单、时间说明和补充提问信息。"
    if re.search(r"\b(ipo|valuation|liquidity|investors?|funding|revenue|cost|pricing|burn)\b|融资|估值|上市|成本|收入|亏损|烧钱", lower):
        points.append("后续主要展开融资、估值、成本或商业化压力")
    if re.search(r"\b(jira|pr|review|test|workflow|context|developer|coding)\b|代码|测试|评审|工作流|上下文", lower):
        points.append("后续主要展开开发工作流、上下文、测试或评审问题")
    if re.search(r"\b(tts|onnx|pytorch|cuda|vocoder|inference|esp32|local|edge)\b|推理|本地|声码器|边缘|依赖", lower):
        points.append("后续主要展开模型结构、部署依赖、推理成本或边缘设备限制")
    if re.search(r"\b(gpt|sora|agentkit|apps sdk|codex|api)\b|模型|接口|平台|工具", lower):
        points.append("后续主要展开模型/API/平台能力细节")
    if points:
        return "；".join(points) + "。"
    first_sentence, _ = split_summary(text, 180)
    return first_sentence + ("。" if first_sentence and first_sentence[-1] not in "。！？.!?" else "")


def format_short_item(item: SourceItem, enriched: Enrichment) -> str:
    title = enriched.zh_title or item.title or compact(item.text, 80) or "无标题"
    body = enriched.zh_translation or fallback_enrichment(item).zh_translation
    summary_lines = format_summary_lines(body, 260)
    lines = [
        f"- **{title}**",
        f"  - 来源：{format_source_link(item)}",
        f"  - 摘要：{summary_lines[0]}",
    ]
    lines.extend(f"  - {line}" for line in summary_lines[1:])
    lines.append(f"  - 观察：{enriched.signal or heuristic_signal(item)}")
    return "\n".join(lines)


def format_x_signal_item(
    item: SourceItem,
    enriched: Enrichment,
    thread_items: list[SourceItem],
    thread_enrichments: dict[str, Enrichment],
) -> str:
    title = enriched.zh_title or item.title or compact(item.text, 80) or "无标题"
    body = enriched.zh_translation or fallback_enrichment(item).zh_translation
    summary_lines = format_summary_lines(body, 360)
    lines = [
        f"- **{title}**",
        f"  - 来源：{format_source_link(item)}",
        f"  - 热度：likes/retweets={item.score}, replies={item.comments}, views={item.raw.get('views', 0)}",
        f"  - 摘要：{summary_lines[0]}",
    ]
    lines.extend(f"  - {line}" for line in summary_lines[1:])
    author_replies = [thread for thread in thread_items if thread.kind == "x-author-reply"]
    external_replies = [thread for thread in thread_items if thread.kind == "x-reply"]
    if author_replies:
        lines.append("  - 作者补充：")
        for reply in author_replies[:5]:
            reply_enriched = thread_enrichments.get(reply.item_id) or fallback_enrichment(reply)
            text = reply_enriched.zh_translation or fallback_enrichment(reply).zh_translation
            lines.append(f"    - {format_summary_lines(text, 220)[0]}")
    if external_replies:
        lines.append("  - 高质量回复/分歧：")
        for reply in external_replies[:X_THREAD_REPLY_LIMIT]:
            reply_enriched = thread_enrichments.get(reply.item_id) or fallback_enrichment(reply)
            text = reply_enriched.zh_translation or fallback_enrichment(reply).zh_translation
            lines.append(f"    - {format_summary_lines(text, 220)[0]}（{format_source_link(reply)}）")
    return "\n".join(lines)


def format_continuation_item(item: SourceItem, enriched: Enrichment) -> str:
    title = enriched.zh_title or item.title or compact(item.text, 80) or "无标题"
    body = enriched.zh_translation or fallback_enrichment(item).zh_translation
    summary_lines = format_summary_lines(body, 220)
    return "\n".join(
        [
            f"- **{title}**",
            f"  - 来源：{format_source_link(item)}",
            f"  - 当前热度：score={item.score}, comments={item.comments}",
            f"  - 延续原因：这个主题此前已经展开过，但当前仍在目标社区获得较高讨论量。",
            f"  - 本次只看：{summary_lines[0]}",
            *[f"  - {line}" for line in summary_lines[1:]],
        ]
    )


def format_discussion_thread(
    thread: DiscussionThread,
    enrichments: dict[str, Enrichment],
) -> str:
    root_enriched = enrichments.get(thread.root.item_id) or fallback_enrichment(thread.root)
    root_text = discussion_root_text(thread, root_enriched)
    replies = selected_replies(thread)
    lines = [
        f"- 观点：{root_text}",
        f"  - 热度：score={thread.root.score}, replies={len(thread.replies)}",
        f"  - 线程小结：{thread_summary(thread)}",
        f"  - 原文讨论：{format_source_link(thread.root)}",
    ]
    if is_informative_low_score_thread(thread):
        lines.append("  - 可信度提示：这是低热度长观点，保留是因为信息量较高，不代表社区高赞共识。")
    lines.extend(f"  - {line}" for line in truncation_lines(thread.root, root_text))
    if replies:
        lines.append("  - 回复/分歧：")
        for reply in replies:
            reply_enriched = enrichments.get(reply.item_id) or fallback_enrichment(reply)
            reply_text = reply_enriched.zh_translation or fallback_enrichment(reply).zh_translation
            lines.append(f"    - {compact(reply_text, REPLY_TEXT_WIDTH)}（score={reply.score}）")
            lines.extend(f"      - {line}" for line in truncation_lines(reply, reply_text))
    return "\n".join(lines)


def discussion_root_text(thread: DiscussionThread, enriched: Enrichment) -> str:
    text = enriched.zh_translation or fallback_enrichment(thread.root).zh_translation
    return compact(text, DISCUSSION_TEXT_WIDTH)


def truncation_lines(item: SourceItem, translated_text: str) -> list[str]:
    if not looks_upstream_truncated(item.text):
        return []
    summary = omitted_summary(clean_for_translation(item.text))
    return [
        "截断说明：上游返回的原始正文到这里已经中断，日报保留了已读取到的完整部分。",
        f"已读部分要点：{summary}",
        f"建议：需要完整上下文时打开原文讨论继续看：{item.url}",
    ]


def looks_upstream_truncated(text: str) -> bool:
    stripped = text.rstrip()
    if len(stripped) < 500:
        return False
    return stripped.endswith("...") or stripped.endswith("…")


def keep_full_root_comment(thread: DiscussionThread) -> bool:
    return thread.root.score >= 100 or thread_relevance(thread) >= 120


def thread_summary(thread: DiscussionThread) -> str:
    replies = selected_replies(thread)
    text = " ".join([thread.root.text] + [reply.text for reply in replies]).lower()
    signals: list[str] = []
    if re.search(r"\b(agree|exactly|yes|same|works|useful|helped)\b", text):
        signals.append("回复里有认同或补充案例")
    if re.search(r"\b(disagree|wrong|but|however|issue|problem|limitation|risk|fail|bug)\b", text):
        signals.append("也出现了反驳、限制或风险提示")
    if re.search(r"\b(how|why|what|where|link|source|example|benchmark|metric)\b", text):
        signals.append("有人追问证据、用法、链接或指标")
    if re.search(r"\b(cost|price|pricing|roi|revenue|enterprise|workflow|quality|context)\b", text):
        signals.append("讨论延伸到成本、企业流程或质量控制")
    if not replies:
        return "这条主评论本身信息量较高，但当前读取到的下级回复较少。"
    if not signals:
        return "回复主要是在围绕主评论补充背景、经验或细节。"
    return "；".join(signals) + "。"


def format_raw_result(result: CommandResult, fence: str = "yaml") -> str:
    command = " ".join(result.command)
    lines = [f"### {result.title}", "", f"`{command}`", ""]
    if result.ok:
        content = result.stdout or "_No output._"
        lines.extend([f"```{fence}", content, "```"])
    else:
        lines.extend(
            [
                "**Command failed.**",
                "",
                "```text",
                result.stderr or result.stdout or "No error output.",
                "```",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def save_raw_archive(today: str, results: list[CommandResult], read_results: list[CommandResult]) -> Path:
    raw_path = RAW_DIR / today
    raw_path.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "title": result.title,
            "command": result.command,
            "ok": result.ok,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        for result in results + read_results
    ]
    output = raw_path / "commands.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def load_raw_archive(today: str) -> tuple[list[CommandResult], list[CommandResult]]:
    raw_file = RAW_DIR / today / "commands.json"
    payload = json.loads(raw_file.read_text(encoding="utf-8"))
    results: list[CommandResult] = []
    read_results: list[CommandResult] = []
    for row in payload:
        result = CommandResult(
            title=row["title"],
            command=list(row["command"]),
            ok=bool(row["ok"]),
            stdout=str(row.get("stdout", "")),
            stderr=str(row.get("stderr", "")),
        )
        if result.title.startswith("Reddit read") or result.title.startswith("Twitter thread"):
            read_results.append(result)
        else:
            results.append(result)
    return results, read_results


def render_article_report(results: list[CommandResult], read_results: list[CommandResult]) -> str:
    today = report_date()
    all_items = parse_source_items(results, read_results)
    candidates = select_article_candidates(all_items)
    article_items = [article_source_item(candidate) for candidate in candidates]
    discussion_items = dedupe_items(
        item
        for candidate in candidates
        for item in candidate.discussion_items[:2]
    )
    translation_targets = dedupe_items(article_items + discussion_items)
    enrichments = translate_items(translation_targets)
    validate_translation_coverage(translation_targets, enrichments, "文章精选")
    article_item_by_url = {
        candidate.article_url: article_item
        for candidate, article_item in zip(candidates, article_items)
    }

    def enriched_for(item: SourceItem) -> Enrichment:
        enriched = enrichments.get(item.item_id)
        if enriched and not enriched.error:
            return enriched
        return fallback_enrichment(item, enriched.error if enriched else "")

    must_read = [candidate for candidate in candidates if candidate.article_text][:3]
    must_read_urls = {candidate.article_url for candidate in must_read}
    skim = [
        candidate
        for candidate in candidates
        if candidate.article_url not in must_read_urls
    ][:ARTICLE_LIMIT]
    sections = [
        f"# Reddit / X 高质量文章精选 - {today}",
        "",
        "## 今日结论",
        "",
        f"- 候选文章：{len(candidates)}",
        f"- 已读取正文：{sum(1 for candidate in candidates if candidate.article_text)}",
        f"- 来源范围：Reddit 外链、X card、X 正文/作者补充链接",
        f"- 筛选门槛：score >= {ARTICLE_MIN_SCORE}，每天最多 {ARTICLE_LIMIT} 篇",
        "",
    ]
    sections.extend(["## 必读", ""])
    if must_read:
        for index, candidate in enumerate(must_read, 1):
            article_item = article_item_by_url[candidate.article_url]
            sections.append(format_article_candidate(index, candidate, enriched_for(article_item), enrichments))
            sections.append("")
    else:
        sections.append("_今天没有筛出达到门槛的文章。_")
        sections.append("")

    if skim:
        sections.extend(["## 值得略读", ""])
        for index, candidate in enumerate(skim, 1):
            article_item = article_item_by_url[candidate.article_url]
            sections.append(format_article_candidate(index, candidate, enriched_for(article_item), enrichments))
            sections.append("")

    sections.extend(
        [
            "## 说明",
            "",
            "- 文章精选和日报共用同一批 Agent-Reach / OpenCLI 原始采集结果，不新增自写 Reddit/X 爬虫。",
            "- Reddit 侧优先选择带外部链接且有社区讨论的帖子；X 侧优先选择高质量作者、card 外链、作者补充里的文章链接。",
            "- 正文读取通过 Jina Reader 完成，并缓存到 data/raw/YYYY-MM-DD/article-cache.json。",
            "- “必读”只收录已成功读取正文的文章；正文读取失败的候选仅进入“值得略读”。",
            f"- 同一发布方每天最多 {ARTICLE_AUTHOR_LIMIT} 篇，同一社交发布事件只保留一个主条目。",
            "",
        ]
    )
    return "\n".join(sections)


def format_article_candidate(
    index: int,
    candidate: ArticleCandidate,
    enrichment: Enrichment,
    discussion_enrichments: dict[str, Enrichment],
) -> str:
    title = enrichment.zh_title or candidate.title
    summary = enrichment.zh_translation or fallback_enrichment(article_source_item(candidate)).zh_translation
    key_points = article_key_points(summary)
    lines = [
        f"### {index}. {title}",
        "",
        f"- 原文：[{candidate.article_url}]({candidate.article_url})",
        f"- 讨论入口：{format_source_link(candidate.item)}",
        f"- 来源：{candidate.item.platform} {article_source_label(candidate.item)}",
        f"- 分数：{candidate.score}",
        f"- 为什么值得读：{article_reading_reason(candidate, enrichment)}",
        "- 核心观点：",
    ]
    lines.extend(f"  - {point}" for point in key_points)
    lines.append(f"- 可验证线索：{article_verification_line(candidate)}")
    followup = article_followup_value(candidate, enrichment)
    if followup and followup != "空":
        lines.append(f"- 可跟进方向：{followup}")
    if candidate.related_links:
        lines.append("- 相关链接：")
        for related_title, related_url in candidate.related_links[:5]:
            lines.append(f"  - [{compact(related_title, 120)}]({related_url})")
    if candidate.fetch_error:
        lines.append(f"- 正文读取：失败，已保留社交平台摘要和原文链接（{candidate.fetch_error}）")
    elif candidate.article_text:
        lines.append("- 正文读取：已读取并纳入摘要")
    else:
        lines.append("- 正文读取：未读取，使用社交平台原文摘要")

    feedback_lines = article_feedback_lines(candidate, discussion_enrichments)
    if feedback_lines:
        lines.extend(["", "**社区反馈**", ""])
        lines.extend(f"- {line}" for line in feedback_lines)
    return "\n".join(lines)


def article_reading_reason(candidate: ArticleCandidate, enrichment: Enrichment) -> str:
    signal = enrichment.signal.strip()
    if signal and signal != "该条目在目标社区中获得关注，建议结合原文判断价值。":
        return compact(f"{candidate.reason}；{signal}", 320)
    return candidate.reason


def article_key_points(summary: str, limit: int = 3) -> list[str]:
    cleaned = markdown_escape(summary)
    if not cleaned:
        return ["原文信息不足，建议打开链接直接查看。"]
    sentences = split_article_sentences(cleaned)
    points: list[str] = []
    for sentence in sentences:
        if low_value_article_sentence(sentence):
            continue
        sentence = compact(sentence, 220)
        if len(sentence) < 18 and len(sentences) > limit:
            continue
        if sentence not in points:
            points.append(sentence)
        if len(points) >= limit:
            break
    if points:
        return points
    return format_summary_lines(cleaned, 220)[:limit]


def split_article_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s+|(?<=[。！？!?])", text)
    sentences = [part.strip(" ，,;；") for part in parts if part.strip(" ，,;；")]
    if len(sentences) <= 1:
        sentences = [part.strip(" ，,;；") for part in re.split(r"[；;]\s*", text) if part.strip(" ，,;；")]
    return sentences


def low_value_article_sentence(sentence: str) -> bool:
    lower = sentence.lower()
    patterns = [
        "无障碍帮助",
        "跳过导航",
        "跳过主要内容",
        "skip navigation",
        "skip to main content",
        "subscribe",
        "cookie",
        "cookies",
        "订阅登录",
        "关闭搜索栏",
        "最受欢迎",
        "前往《金融时报》主页",
    ]
    return any(pattern in lower for pattern in patterns)


def article_verification_line(candidate: ArticleCandidate) -> str:
    domain = article_domain(candidate.article_url)
    if candidate.article_text:
        if candidate.article_url.lower().endswith(".pdf"):
            return f"已读取 PDF/报告正文；优先核对摘要、实验设置、数据规模和结论边界（{domain}）。"
        return f"已读取网页正文；优先核对原文数据、发布时间、实验方法和链接中的原始材料（{domain}）。"
    if candidate.fetch_error:
        return f"正文读取失败，当前只依据社交平台文本和讨论判断；需要打开原文复核（{domain}）。"
    return f"未读取正文，当前只依据社交平台文本判断；需要打开原文复核（{domain}）。"


def article_followup_value(candidate: ArticleCandidate, enrichment: Enrichment) -> str:
    if enrichment.opportunity.strip() and enrichment.opportunity.strip() != "空":
        return enrichment.opportunity.strip()
    return heuristic_opportunity(article_source_item(candidate))


def article_source_label(item: SourceItem) -> str:
    if item.platform == "Reddit":
        subreddit = item_subreddit(item)
        return subreddit or (f"u/{item.author}" if item.author else "")
    if item.platform == "X/Twitter":
        return f"@{item.author}" if item.author else ""
    return ""


def article_feedback_lines(
    candidate: ArticleCandidate,
    discussion_enrichments: dict[str, Enrichment],
) -> list[str]:
    lines: list[str] = []
    for item in candidate.discussion_items[:3]:
        enriched = discussion_enrichments.get(item.item_id)
        text = ""
        if enriched and not enriched.error:
            text = enriched.zh_translation
        if not text:
            text = clean_for_translation(item.text or item.title)
        if text:
            lines.append(f"{format_summary_lines(text, 240)[0]}（{format_source_link(item)}）")
    if not lines:
        if candidate.item.platform == "Reddit" and candidate.item.comments:
            lines.append(f"暂无筛出的高质量评论；Reddit 总讨论量为 {candidate.item.comments} 条，建议打开讨论入口复核分歧。")
        elif candidate.item.platform == "X/Twitter" and candidate.item.comments:
            lines.append(f"暂无筛出的高质量回复；X 总回复数为 {candidate.item.comments}，建议打开 thread 查看作者补充和评论区链接。")
    return lines


def render_report(results: list[CommandResult], read_results: list[CommandResult]) -> str:
    today = report_date()
    save_raw_archive(today, results, read_results)
    all_items = parse_source_items(results, read_results)
    reddit_items = [item for item in all_items if item.platform != "X/Twitter"]
    selected_candidates = topic_items(reddit_items)
    x_signals = x_signal_items(all_items)
    history_filter = apply_history_filter(selected_candidates, today)
    pre_discussion_selected = history_filter.fresh
    pre_discussion_continuation = history_filter.continuation
    pre_discussion_items = pre_discussion_selected + pre_discussion_continuation
    all_topic_threads = {
        item.item_id: discussion_threads_for_topic(item, all_items)
        for item in pre_discussion_items
    }
    selected, no_discussion_count = filter_items_with_hot_discussion(
        pre_discussion_selected,
        all_topic_threads,
    )
    continuation_items, continuation_no_discussion_count = filter_items_with_hot_discussion(
        pre_discussion_continuation,
        all_topic_threads,
    )
    topic_threads = {item.item_id: all_topic_threads.get(item.item_id, []) for item in selected}
    focus_groups, short_items = build_topic_groups(selected, topic_threads)
    x_signals_by_group: dict[str, list[SourceItem]] = {}
    for signal in x_signals:
        x_signals_by_group.setdefault(group_key(signal), []).append(signal)
    focus_group_keys = {group.key for group in focus_groups}
    standalone_x_signals = [
        signal
        for signal in x_signals
        if group_key(signal) not in focus_group_keys
    ][:X_SIGNAL_LIMIT]
    x_thread_items_by_tweet_id = {
        twitter_tweet_id(signal): x_thread_items_for_tweet(signal, all_items)
        for signal in x_signals
        if twitter_tweet_id(signal)
    }
    x_thread_items = dedupe_items(
        item
        for items_for_tweet in x_thread_items_by_tweet_id.values()
        for item in items_for_tweet
    )
    focus_items = [item for group in focus_groups for item in group.items]
    focus_discussion = [item for group in focus_groups for item in group_thread_items(group)]
    short_discussion = all_thread_items(short_items, all_items)
    discussion = focus_discussion + short_discussion
    thread_count = sum(
        len(threads)
        for group in focus_groups
        for threads in group.threads_by_item_id.values()
    )
    reply_count = sum(
        len(selected_replies(thread))
        for group in focus_groups
        for threads in group.threads_by_item_id.values()
        for thread in threads
    )
    enrichment_targets = dedupe_items(
        focus_items
        + short_items
        + continuation_items
        + discussion
        + x_signals
        + x_thread_items
    )
    enrichments = translate_items(enrichment_targets)
    validate_translation_coverage(enrichment_targets, enrichments, "技术社区情报日报")

    def enriched_for(item: SourceItem) -> Enrichment:
        enriched = enrichments.get(item.item_id)
        if enriched and not enriched.error:
            return enriched
        return fallback_enrichment(item, enriched.error if enriched else "")

    if should_use_hybrid():
        translation_label = f"Hybrid: GLM core / Google fallback ({ANTHROPIC_MODEL})"
    elif should_use_anthropic():
        translation_label = f"Anthropic-compatible / {ANTHROPIC_MODEL}"
    elif should_use_openai():
        translation_label = "OpenAI"
    else:
        translation_label = "Google Translate"
    if TRANSLATION_PROVIDER == "none":
        translation_label = "未启用"
    translation_note = translation_mode_note()

    sections = [
        f"# 技术社区情报日报 - {today}",
        "",
        "## 今日结论",
        "",
        f"- 原始候选条目：{len(all_items)}",
        f"- 入选 Reddit 帖：{len(selected)}",
        f"- 重点议题：{len(focus_groups)}",
        f"- X 资讯/信号：{len(x_signals)}",
        f"- 短讯/观察：{len(short_items)}",
        f"- 历史去重：过滤 {history_filter.skipped_count} 个重复主题，保留 {len(continuation_items)} 个延续讨论",
        f"- 讨论门槛：过滤 {no_discussion_count + continuation_no_discussion_count} 个无热门评论主题",
        f"- 代表性讨论线程：{thread_count}",
        f"- 线程内精选回复：{reply_count}",
        f"- 翻译：{translation_label}",
        "",
        "## 今日主线",
        "",
    ]
    sections.extend(f"- {line}" for line in mainline_summary_from_groups(focus_groups))
    sections.extend(["", "## 重点主题", ""])
    if focus_groups:
        for index, group in enumerate(focus_groups, start=1):
            group_items = group.items
            group_thread_items_list = group_thread_items(group)
            item_enrichments = {
                item.item_id: enriched_for(item)
                for item in group_items
            }
            discussion_enrichments = {
                item.item_id: enriched_for(item)
                for item in group_thread_items_list
            }
            group_x_signals = x_signals_by_group.get(group.key, [])
            x_enrichments = {
                item.item_id: enriched_for(item)
                for item in group_x_signals
            }
            x_thread_enrichments = {
                item.item_id: enriched_for(item)
                for signal in group_x_signals
                for item in x_thread_items_by_tweet_id.get(twitter_tweet_id(signal), [])
            }
            sections.append(
                format_focus_group(
                    index,
                    group,
                    item_enrichments,
                    discussion_enrichments,
                    group_x_signals,
                    x_enrichments,
                    x_thread_items_by_tweet_id,
                    x_thread_enrichments,
                )
            )
    else:
        sections.append("_没有筛出高价值条目。请检查 OpenCLI 登录态或增大 DAILY_NEWS_LIMIT。_\n")

    if continuation_items:
        sections.extend(["", "## 历史延续", ""])
        sections.append(
            f"_以下主题在过去 {HISTORY_DEDUP_DAYS} 天日报里已经出现过，本次只保留热度仍高或讨论仍活跃的延续项，不重复展开完整评论。_"
        )
        sections.append("")
        for item in continuation_items:
            sections.append(format_continuation_item(item, enriched_for(item)))
            sections.append("")

    if short_items:
        sections.extend(["", "## 短讯与观察", ""])
        for item in short_items:
            sections.append(format_short_item(item, enriched_for(item)))
            sections.append("")

    if standalone_x_signals:
        sections.extend(["", "## 资讯与文章", ""])
        for item in standalone_x_signals:
            tweet_id = twitter_tweet_id(item)
            thread_items = x_thread_items_by_tweet_id.get(tweet_id, [])
            thread_enrichments = {
                thread.item_id: enriched_for(thread)
                for thread in thread_items
            }
            sections.append(format_x_signal_item(item, enriched_for(item), thread_items, thread_enrichments))
            sections.append("")

    sections.append(
        textwrap.dedent(
            f"""
            ## 说明

            - 重点主题会先按议题合并，再展示核心判断、观点拆解、来源摘要和代表性 Reddit 讨论线程。
            - 当前报告默认同时读取 Reddit 和 X/Twitter；可用 DAILY_NEWS_INCLUDE_TWITTER=0 关闭 X。
            - 弱讨论 Reddit 条目进入“短讯与观察”，不与深度 Reddit 讨论占同等权重。
            - 日报只收录近期内容：Reddit 最近 {REDDIT_FRESHNESS_DAYS} 天，X/Twitter 最近 {X_FRESHNESS_DAYS} 天；文章精选单独使用 {ARTICLE_FRESHNESS_DAYS} 天窗口。
            - 默认只保留读取到合格热门评论的 Reddit 帖子；只有标题热、但评论没有热度的帖子会被过滤。
            - 评论还会按信息增量过滤；纯情绪、玩笑、客服和账号问题不会仅凭高赞进入代表性讨论。
            - X/Twitter 用于捕捉早期资讯、作者补充、外链文章和少量高信号回复；Reddit 仍负责社区讨论深度。
            - 采集仍仅通过 Agent-Reach / OpenCLI 完成，没有自写 Reddit/X 爬虫。
            - {translation_note}
            """
        ).strip()
    )
    sections.append("")
    return "\n".join(sections)


def translation_mode_note() -> str:
    if should_use_hybrid():
        return "混合翻译模式下，重点主题、热门 L0 主评论和关键回复优先使用 GLM；其余内容使用 Google Translate 或缓存。"
    if should_use_anthropic():
        return f"当前使用 Anthropic-compatible 接口调用 {ANTHROPIC_MODEL} 翻译；解析失败的条目会重试，仍失败则回退显示清洗后的原文。"
    if should_use_openai():
        return "当前使用 OpenAI 模型翻译；失败条目会回退显示清洗后的原文。"
    if TRANSLATION_PROVIDER == "none":
        return "当前未启用翻译，报告直接显示清洗后的原文。"
    return "当前使用 Google Translate 翻译；失败条目会回退显示清洗后的原文。"


def with_frontmatter(markdown: str, doc_type: str, today: str, tags: list[str]) -> str:
    if markdown.startswith("---\n"):
        return markdown
    frontmatter_lines = [
        "---",
        f"date: {today}",
        f"type: {doc_type}",
        "source: reddit-x",
        "generated_by: daily-news",
        "tags:",
        *[f"  - {tag}" for tag in tags],
        "---",
    ]
    frontmatter = "\n".join(frontmatter_lines)
    return f"{frontmatter}\n\n{markdown}"


def write_markdown_outputs(today: str, report: str, article_report: str) -> tuple[Path, Path, list[Path]]:
    report_path = REPORT_DIR / f"{today}.md"
    article_path = ARTICLE_DIR / f"{today}.md"
    atomic_write_text(
        report_path,
        with_frontmatter(report, "daily-report", today, ["daily-news", "ai", "reddit", "x"]),
    )
    atomic_write_text(
        article_path,
        with_frontmatter(article_report, "article-digest", today, ["article-digest", "ai", "reddit", "x"]),
    )

    obsidian_paths = write_obsidian_outputs(today, report_path, article_path)
    return report_path, article_path, obsidian_paths


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_obsidian_outputs(today: str, report_path: Path, article_path: Path) -> list[Path]:
    if not OBSIDIAN_VAULT_DIR:
        return []
    base = Path(OBSIDIAN_VAULT_DIR).expanduser()
    if not base.exists():
        print(f"[daily-news] Obsidian vault not found: {base}", flush=True)
        return []
    target_base = base / OBSIDIAN_SUBDIR
    outputs = [
        (report_path, target_base / "reports" / f"{today}.md"),
        (article_path, target_base / "articles" / f"{today}.md"),
    ]
    written: list[Path] = []
    for source, target in outputs:
        atomic_write_text(target, source.read_text(encoding="utf-8"))
        written.append(target)
    return written


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLE_DIR.mkdir(parents=True, exist_ok=True)
    today = report_date()
    if FROM_RAW:
        results, read_results = load_raw_archive(today)
    else:
        results, read_results = collect()
    report = render_report(results, read_results)
    article_report = render_article_report(results, read_results)
    report_path, article_path, obsidian_paths = write_markdown_outputs(today, report, article_report)
    print(report_path)
    print(article_path)
    for path in obsidian_paths:
        print(path)


if __name__ == "__main__":
    main()
