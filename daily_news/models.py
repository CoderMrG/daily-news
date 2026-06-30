"""Shared data models for daily-news."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandResult:
    title: str
    command: list[str]
    ok: bool
    stdout: str
    stderr: str
    duration_seconds: float = 0.0


@dataclass
class SourceItem:
    item_id: str
    platform: str
    kind: str
    title: str = ""
    text: str = ""
    author: str = ""
    url: str = ""
    source_url: str = ""
    score: int = 0
    comments: int = 0
    created_at: dt.datetime | None = None
    parent_post_id: str = ""
    source_command: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Enrichment:
    zh_title: str = ""
    zh_translation: str = ""
    signal: str = ""
    opportunity: str = ""
    confidence: str = "medium"
    translated_by: str = "none"
    error: str = ""


@dataclass
class DiscussionThread:
    root: SourceItem
    replies: list[SourceItem] = field(default_factory=list)


@dataclass
class TopicGroup:
    key: str
    title: str
    items: list[SourceItem] = field(default_factory=list)
    threads_by_item_id: dict[str, list[DiscussionThread]] = field(default_factory=dict)


@dataclass
class HistoryFilter:
    fresh: list[SourceItem]
    continuation: list[SourceItem]
    skipped_count: int
    history_ids: set[str] = field(default_factory=set)


@dataclass
class ArticleCandidate:
    item: SourceItem
    article_url: str
    title: str
    score: int
    reason: str
    discussion_items: list[SourceItem] = field(default_factory=list)
    related_links: list[tuple[str, str]] = field(default_factory=list)
    article_text: str = ""
    fetch_error: str = ""
