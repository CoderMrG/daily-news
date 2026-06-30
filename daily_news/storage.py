"""SQLite persistence for daily-news runtime data."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from daily_news.models import ArticleCandidate, Enrichment, SourceItem


MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS report_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        source_count INTEGER NOT NULL DEFAULT 0,
        report_path TEXT,
        article_path TEXT,
        error TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_report_runs_date
        ON report_runs(report_date, status);

    CREATE TABLE IF NOT EXISTS source_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        item_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        score INTEGER NOT NULL DEFAULT 0,
        comments INTEGER NOT NULL DEFAULT 0,
        created_at TEXT,
        parent_post_id TEXT NOT NULL DEFAULT '',
        raw_json TEXT NOT NULL DEFAULT '{}',
        first_seen_date TEXT NOT NULL,
        last_seen_date TEXT NOT NULL,
        UNIQUE(platform, item_key)
    );
    CREATE INDEX IF NOT EXISTS idx_source_items_parent
        ON source_items(platform, parent_post_id);
    CREATE INDEX IF NOT EXISTS idx_source_items_seen
        ON source_items(last_seen_date);

    CREATE TABLE IF NOT EXISTS run_sources (
        run_id INTEGER NOT NULL REFERENCES report_runs(id) ON DELETE CASCADE,
        source_item_id INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
        PRIMARY KEY(run_id, source_item_id)
    );

    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        source_item_id INTEGER REFERENCES source_items(id) ON DELETE SET NULL,
        title TEXT NOT NULL DEFAULT '',
        score INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        article_text TEXT NOT NULL DEFAULT '',
        fetch_error TEXT NOT NULL DEFAULT '',
        related_links_json TEXT NOT NULL DEFAULT '[]',
        first_seen_date TEXT NOT NULL,
        last_seen_date TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_articles_seen
        ON articles(last_seen_date);

    CREATE TABLE IF NOT EXISTS translations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
        source_hash TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        zh_title TEXT NOT NULL DEFAULT '',
        zh_translation TEXT NOT NULL DEFAULT '',
        signal TEXT NOT NULL DEFAULT '',
        opportunity TEXT NOT NULL DEFAULT '',
        confidence TEXT NOT NULL DEFAULT 'medium',
        error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(source_item_id, source_hash, provider, model)
    );

    CREATE TABLE IF NOT EXISTS report_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES report_runs(id) ON DELETE CASCADE,
        report_date TEXT NOT NULL,
        document_type TEXT NOT NULL,
        path TEXT NOT NULL DEFAULT '',
        markdown TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(run_id, document_type)
    );
    CREATE INDEX IF NOT EXISTS idx_report_documents_history
        ON report_documents(report_date, document_type);

    CREATE TABLE IF NOT EXISTS report_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL REFERENCES report_documents(id) ON DELETE CASCADE,
        section TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        event_key TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        article_url TEXT NOT NULL DEFAULT '',
        UNIQUE(document_id, position, source_url, article_url)
    );
    CREATE INDEX IF NOT EXISTS idx_report_entries_event
        ON report_entries(event_key);
    CREATE INDEX IF NOT EXISTS idx_report_entries_article
        ON report_entries(article_url);

    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        document_type TEXT NOT NULL,
        entry_key TEXT NOT NULL,
        rating TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(report_date, document_type, entry_key)
    );
    """
]


class DailyNewsStore:
    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DailyNewsStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            int(row["version"])
            for row in self.connection.execute("SELECT version FROM schema_migrations")
        }
        for version, migration in enumerate(MIGRATIONS, start=1):
            if version in applied:
                continue
            with self.connection:
                self.connection.executescript(migration)
                self.connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, now_iso()),
                )

    def start_run(self, report_date: str, mode: str) -> int:
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO report_runs(report_date, mode, status, started_at)
                VALUES (?, ?, 'running', ?)
                """,
                (report_date, mode, now_iso()),
            )
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        *,
        source_count: int = 0,
        report_path: str = "",
        article_path: str = "",
        error: str = "",
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE report_runs
                SET status = ?, finished_at = ?, source_count = ?,
                    report_path = ?, article_path = ?, error = ?
                WHERE id = ?
                """,
                (
                    status,
                    now_iso(),
                    source_count,
                    report_path,
                    article_path,
                    error[:2000],
                    run_id,
                ),
            )

    def upsert_source_items(
        self,
        run_id: int,
        report_date: str,
        items: Iterable[SourceItem],
    ) -> dict[tuple[str, str], int]:
        item_ids: dict[tuple[str, str], int] = {}
        with self.connection:
            for item in items:
                item_key = stable_item_key(item)
                self.connection.execute(
                    """
                    INSERT INTO source_items(
                        platform, item_key, kind, title, body, author, url,
                        source_url, score, comments, created_at, parent_post_id,
                        raw_json, first_seen_date, last_seen_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, item_key) DO UPDATE SET
                        kind = excluded.kind,
                        title = excluded.title,
                        body = excluded.body,
                        author = excluded.author,
                        url = excluded.url,
                        source_url = excluded.source_url,
                        score = excluded.score,
                        comments = excluded.comments,
                        created_at = COALESCE(excluded.created_at, source_items.created_at),
                        parent_post_id = excluded.parent_post_id,
                        raw_json = excluded.raw_json,
                        last_seen_date = excluded.last_seen_date
                    """,
                    (
                        item.platform,
                        item_key,
                        item.kind,
                        item.title,
                        item.text,
                        item.author,
                        item.url,
                        item.source_url,
                        item.score,
                        item.comments,
                        item.created_at.isoformat() if item.created_at else None,
                        item.parent_post_id,
                        json.dumps(item.raw, ensure_ascii=False, default=str),
                        report_date,
                        report_date,
                    ),
                )
                row = self.connection.execute(
                    "SELECT id FROM source_items WHERE platform = ? AND item_key = ?",
                    (item.platform, item_key),
                ).fetchone()
                if row is None:
                    continue
                source_item_id = int(row["id"])
                item_ids[(item.platform, item.item_id)] = source_item_id
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO run_sources(run_id, source_item_id)
                    VALUES (?, ?)
                    """,
                    (run_id, source_item_id),
                )
        return item_ids

    def record_articles(
        self,
        report_date: str,
        candidates: Iterable[ArticleCandidate],
    ) -> None:
        with self.connection:
            for candidate in candidates:
                source_item_id = self.source_item_id(candidate.item)
                self.connection.execute(
                    """
                    INSERT INTO articles(
                        url, source_item_id, title, score, reason, article_text,
                        fetch_error, related_links_json, first_seen_date, last_seen_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        source_item_id = COALESCE(excluded.source_item_id, articles.source_item_id),
                        title = excluded.title,
                        score = excluded.score,
                        reason = excluded.reason,
                        article_text = CASE
                            WHEN excluded.article_text <> '' THEN excluded.article_text
                            ELSE articles.article_text
                        END,
                        fetch_error = excluded.fetch_error,
                        related_links_json = excluded.related_links_json,
                        last_seen_date = excluded.last_seen_date
                    """,
                    (
                        candidate.article_url,
                        source_item_id,
                        candidate.title,
                        candidate.score,
                        candidate.reason,
                        candidate.article_text,
                        candidate.fetch_error,
                        json.dumps(candidate.related_links, ensure_ascii=False),
                        report_date,
                        report_date,
                    ),
                )

    def record_translations(
        self,
        items: Iterable[SourceItem],
        enrichments: dict[str, Enrichment],
        provider: str,
        model: str,
    ) -> None:
        with self.connection:
            for item in items:
                enrichment = enrichments.get(item.item_id)
                source_item_id = self.source_item_id(item)
                if enrichment is None or source_item_id is None:
                    continue
                source_hash = hashlib.sha256(
                    f"{item.kind}\n{item.title}\n{item.text}".encode("utf-8")
                ).hexdigest()
                self.connection.execute(
                    """
                    INSERT INTO translations(
                        source_item_id, source_hash, provider, model, zh_title,
                        zh_translation, signal, opportunity, confidence, error,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_item_id, source_hash, provider, model)
                    DO UPDATE SET
                        zh_title = excluded.zh_title,
                        zh_translation = excluded.zh_translation,
                        signal = excluded.signal,
                        opportunity = excluded.opportunity,
                        confidence = excluded.confidence,
                        error = excluded.error,
                        created_at = excluded.created_at
                    """,
                    (
                        source_item_id,
                        source_hash,
                        provider,
                        model,
                        enrichment.zh_title,
                        enrichment.zh_translation,
                        enrichment.signal,
                        enrichment.opportunity,
                        enrichment.confidence,
                        enrichment.error,
                        now_iso(),
                    ),
                )

    def source_item_id(self, item: SourceItem) -> int | None:
        row = self.connection.execute(
            "SELECT id FROM source_items WHERE platform = ? AND item_key = ?",
            (item.platform, stable_item_key(item)),
        ).fetchone()
        return int(row["id"]) if row else None

    def record_publication(
        self,
        run_id: int,
        report_date: str,
        document_type: str,
        path: Path | str,
        markdown: str,
    ) -> int:
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO report_documents(
                    run_id, report_date, document_type, path, markdown, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, document_type) DO UPDATE SET
                    path = excluded.path,
                    markdown = excluded.markdown,
                    created_at = excluded.created_at
                """,
                (run_id, report_date, document_type, str(path), markdown, now_iso()),
            )
            row = self.connection.execute(
                """
                SELECT id FROM report_documents
                WHERE run_id = ? AND document_type = ?
                """,
                (run_id, document_type),
            ).fetchone()
            if row is None:
                return int(cursor.lastrowid)
            document_id = int(row["id"])
            self.connection.execute(
                "DELETE FROM report_entries WHERE document_id = ?",
                (document_id,),
            )
            for entry in extract_report_entries(markdown):
                self.connection.execute(
                    """
                    INSERT INTO report_entries(
                        document_id, section, position, title, event_key,
                        source_url, article_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        entry["section"],
                        entry["position"],
                        entry["title"],
                        entry["event_key"],
                        entry["source_url"],
                        entry["article_url"],
                    ),
                )
        return document_id

    def historical_documents(
        self,
        today: str,
        days: int,
        document_types: tuple[str, ...] = ("daily-report", "article-digest"),
    ) -> list[str]:
        if days <= 0 or not document_types:
            return []
        current = dt.date.fromisoformat(today)
        start = (current - dt.timedelta(days=days)).isoformat()
        placeholders = ",".join("?" for _ in document_types)
        rows = self.connection.execute(
            f"""
            SELECT markdown
            FROM report_documents
            WHERE report_date >= ? AND report_date < ?
              AND document_type IN ({placeholders})
              AND id IN (
                  SELECT MAX(id)
                  FROM report_documents
                  GROUP BY report_date, document_type
              )
            ORDER BY report_date, id
            """,
            (start, today, *document_types),
        ).fetchall()
        return [str(row["markdown"]) for row in rows]

    def backfill_markdown_history(
        self,
        report_dir: Path,
        article_dir: Path,
    ) -> int:
        if self.metadata("markdown_history_backfilled") == "1":
            return 0
        imported = 0
        paths_by_date: dict[str, dict[str, Path]] = {}
        for document_type, directory in [
            ("daily-report", report_dir),
            ("article-digest", article_dir),
        ]:
            for path in sorted(directory.glob("????-??-??.md")):
                paths_by_date.setdefault(path.stem, {})[document_type] = path
        for report_date, documents in sorted(paths_by_date.items()):
            run_id = self.start_run(report_date, "import")
            try:
                for document_type, path in documents.items():
                    markdown = path.read_text(encoding="utf-8")
                    self.record_publication(
                        run_id,
                        report_date,
                        document_type,
                        path,
                        markdown,
                    )
                    imported += 1
                self.finish_run(run_id, "imported")
            except OSError as exc:
                self.finish_run(run_id, "failed", error=str(exc))
        self.set_metadata("markdown_history_backfilled", "1")
        return imported

    def metadata(self, key: str) -> str:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else ""

    def set_metadata(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def table_counts(self) -> dict[str, int]:
        tables = [
            "report_runs",
            "source_items",
            "articles",
            "translations",
            "report_documents",
            "report_entries",
            "feedback",
        ]
        return {
            table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    def run_source_count(self, run_id: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM run_sources WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row[0]) if row else 0


def stable_item_key(item: SourceItem) -> str:
    if item.item_id:
        return item.item_id
    digest = hashlib.sha256(
        f"{item.kind}\n{item.url}\n{item.title}\n{item.text[:500]}".encode("utf-8")
    ).hexdigest()
    return f"generated-{digest[:24]}"


def extract_report_entries(markdown: str) -> list[dict[str, str | int]]:
    entries: list[dict[str, str | int]] = []
    section = ""
    title = ""
    position = 0
    seen: set[tuple[str, str]] = set()
    for line in markdown.splitlines():
        if line.startswith("## "):
            section = line.removeprefix("## ").strip()
        elif line.startswith("### "):
            title = re.sub(r"^\d+\.\s*", "", line.removeprefix("### ").strip())
        elif line.startswith("- **") and line.endswith("**"):
            title = line[4:-2].strip()
        article_match = re.search(r"^- 原文：\[(https?://[^\]]+)\]", line)
        source_match = re.search(
            r"https?://(?:www\.)?reddit\.com/r/[^/\s)]+/comments/([a-z0-9]+)[^\s)]*"
            r"|https?://(?:x|twitter)\.com/(?:i/status|[^/\s)]+/status)/(\d+)",
            line,
            re.IGNORECASE,
        )
        article_url = article_match.group(1) if article_match else ""
        source_url = source_match.group(0) if source_match else ""
        if not article_url and not source_url:
            continue
        event_key = ""
        if source_match:
            if source_match.group(1):
                event_key = f"reddit:{source_match.group(1).lower()}"
            elif source_match.group(2):
                event_key = f"twitter:{source_match.group(2)}"
        dedupe_key = (source_url, article_url)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        position += 1
        entries.append(
            {
                "section": section,
                "position": position,
                "title": title,
                "event_key": event_key,
                "source_url": source_url,
                "article_url": article_url,
            }
        )
    return entries


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
