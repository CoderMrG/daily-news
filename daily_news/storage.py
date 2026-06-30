"""SQLite persistence for daily-news runtime data."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator

from daily_news.models import ArticleCandidate, Enrichment, SourceItem

if TYPE_CHECKING:
    from daily_news.observability import RunMetrics


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
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL UNIQUE REFERENCES report_runs(id) ON DELETE CASCADE,
        report_date TEXT NOT NULL,
        source_count INTEGER NOT NULL DEFAULT 0,
        selected_reddit INTEGER NOT NULL DEFAULT 0,
        focus_topics INTEGER NOT NULL DEFAULT 0,
        x_signals INTEGER NOT NULL DEFAULT 0,
        short_items INTEGER NOT NULL DEFAULT 0,
        duplicate_filtered INTEGER NOT NULL DEFAULT 0,
        discussion_filtered INTEGER NOT NULL DEFAULT 0,
        discussion_threads INTEGER NOT NULL DEFAULT 0,
        selected_replies INTEGER NOT NULL DEFAULT 0,
        article_candidates INTEGER NOT NULL DEFAULT 0,
        articles_fetched INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_quality_snapshots_date
        ON quality_snapshots(report_date);
    """,
    """
    CREATE TABLE IF NOT EXISTS run_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL UNIQUE REFERENCES report_runs(id) ON DELETE CASCADE,
        report_date TEXT NOT NULL,
        collection_seconds REAL NOT NULL DEFAULT 0,
        reddit_seconds REAL NOT NULL DEFAULT 0,
        x_seconds REAL NOT NULL DEFAULT 0,
        article_seconds REAL NOT NULL DEFAULT 0,
        translation_seconds REAL NOT NULL DEFAULT 0,
        render_seconds REAL NOT NULL DEFAULT 0,
        publish_seconds REAL NOT NULL DEFAULT 0,
        total_seconds REAL NOT NULL DEFAULT 0,
        command_total INTEGER NOT NULL DEFAULT 0,
        command_succeeded INTEGER NOT NULL DEFAULT 0,
        reddit_total INTEGER NOT NULL DEFAULT 0,
        reddit_succeeded INTEGER NOT NULL DEFAULT 0,
        x_total INTEGER NOT NULL DEFAULT 0,
        x_succeeded INTEGER NOT NULL DEFAULT 0,
        translation_total INTEGER NOT NULL DEFAULT 0,
        translation_succeeded INTEGER NOT NULL DEFAULT 0,
        article_candidates INTEGER NOT NULL DEFAULT 0,
        articles_fetched INTEGER NOT NULL DEFAULT 0,
        failed_stage TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_run_metrics_date
        ON run_metrics(report_date);
    """
]


class DailyNewsStore:
    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser()
        database_existed = self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._transaction_depth = 0
        if database_existed and self._migration_needed():
            timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.backup_database(
                self.path.parent / "backups",
                f"pre-migration-v{len(MIGRATIONS)}-{timestamp}",
                retention_days=30,
            )
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DailyNewsStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._transaction_depth:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return

        self.connection.execute("BEGIN IMMEDIATE")
        self._transaction_depth = 1
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        finally:
            self._transaction_depth = 0

    @contextmanager
    def _write_scope(self) -> Iterator[None]:
        if self._transaction_depth:
            yield
            return
        with self.connection:
            yield

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

    def _migration_needed(self) -> bool:
        table = self.connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()
        if table is None:
            return True
        row = self.connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        return int(row[0] or 0) < len(MIGRATIONS)

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
        with self._write_scope():
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

    def fail_stale_runs(self, max_age_minutes: int) -> int:
        cutoff = (
            dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(minutes=max_age_minutes)
        ).isoformat()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE report_runs
                SET status = 'failed',
                    finished_at = ?,
                    error = CASE
                        WHEN error IS NULL OR error = ''
                        THEN 'Recovered stale running task'
                        ELSE error
                    END
                WHERE status = 'running' AND started_at < ?
                """,
                (now_iso(), cutoff),
            )
        return int(cursor.rowcount)

    def backup_database(
        self,
        directory: Path,
        name: str,
        *,
        retention_days: int,
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{name}.sqlite3"
        temporary = directory / f".{name}.{time.time_ns()}.tmp"
        try:
            with sqlite3.connect(temporary) as backup:
                self.connection.backup(backup)
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        cutoff = time.time() - retention_days * 86400
        for path in directory.glob("*.sqlite3"):
            try:
                if path != destination and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
        return destination

    def record_run_metrics(self, run_id: int, metrics: RunMetrics) -> None:
        values = metrics.as_record()
        with self._write_scope():
            self.connection.execute(
                """
                INSERT INTO run_metrics(
                    run_id, report_date, collection_seconds, reddit_seconds,
                    x_seconds, article_seconds, translation_seconds,
                    render_seconds, publish_seconds, total_seconds,
                    command_total, command_succeeded, reddit_total,
                    reddit_succeeded, x_total, x_succeeded, translation_total,
                    translation_succeeded, article_candidates,
                    articles_fetched, failed_stage, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(run_id) DO UPDATE SET
                    collection_seconds = excluded.collection_seconds,
                    reddit_seconds = excluded.reddit_seconds,
                    x_seconds = excluded.x_seconds,
                    article_seconds = excluded.article_seconds,
                    translation_seconds = excluded.translation_seconds,
                    render_seconds = excluded.render_seconds,
                    publish_seconds = excluded.publish_seconds,
                    total_seconds = excluded.total_seconds,
                    command_total = excluded.command_total,
                    command_succeeded = excluded.command_succeeded,
                    reddit_total = excluded.reddit_total,
                    reddit_succeeded = excluded.reddit_succeeded,
                    x_total = excluded.x_total,
                    x_succeeded = excluded.x_succeeded,
                    translation_total = excluded.translation_total,
                    translation_succeeded = excluded.translation_succeeded,
                    article_candidates = excluded.article_candidates,
                    articles_fetched = excluded.articles_fetched,
                    failed_stage = excluded.failed_stage,
                    created_at = excluded.created_at
                """,
                (
                    run_id,
                    values["report_date"],
                    values["collection_seconds"],
                    values["reddit_seconds"],
                    values["x_seconds"],
                    values["article_seconds"],
                    values["translation_seconds"],
                    values["render_seconds"],
                    values["publish_seconds"],
                    values["total_seconds"],
                    values["command_total"],
                    values["command_succeeded"],
                    values["reddit_total"],
                    values["reddit_succeeded"],
                    values["x_total"],
                    values["x_succeeded"],
                    values["translation_total"],
                    values["translation_succeeded"],
                    values["article_candidates"],
                    values["articles_fetched"],
                    values["failed_stage"],
                    now_iso(),
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
        with self._write_scope():
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

    def record_quality_snapshot(
        self,
        run_id: int,
        report_date: str,
        report_markdown: str,
        article_markdown: str,
    ) -> None:
        metrics = {
            "source_count": markdown_metric(report_markdown, "原始候选条目"),
            "selected_reddit": markdown_metric(report_markdown, "入选 Reddit 帖"),
            "focus_topics": markdown_metric(report_markdown, "重点议题"),
            "x_signals": markdown_metric(report_markdown, "X 资讯/信号"),
            "short_items": markdown_metric(report_markdown, "短讯/观察"),
            "duplicate_filtered": markdown_metric(report_markdown, "历史去重", "过滤"),
            "discussion_filtered": markdown_metric(report_markdown, "讨论门槛", "过滤"),
            "discussion_threads": markdown_metric(report_markdown, "代表性讨论线程"),
            "selected_replies": markdown_metric(report_markdown, "线程内精选回复"),
            "article_candidates": markdown_metric(article_markdown, "候选文章"),
            "articles_fetched": markdown_metric(article_markdown, "已读取正文"),
        }
        with self._write_scope():
            self.connection.execute(
                """
                INSERT INTO quality_snapshots(
                    run_id, report_date, source_count, selected_reddit,
                    focus_topics, x_signals, short_items, duplicate_filtered,
                    discussion_filtered, discussion_threads, selected_replies,
                    article_candidates, articles_fetched, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    source_count = excluded.source_count,
                    selected_reddit = excluded.selected_reddit,
                    focus_topics = excluded.focus_topics,
                    x_signals = excluded.x_signals,
                    short_items = excluded.short_items,
                    duplicate_filtered = excluded.duplicate_filtered,
                    discussion_filtered = excluded.discussion_filtered,
                    discussion_threads = excluded.discussion_threads,
                    selected_replies = excluded.selected_replies,
                    article_candidates = excluded.article_candidates,
                    articles_fetched = excluded.articles_fetched,
                    created_at = excluded.created_at
                """,
                (
                    run_id,
                    report_date,
                    metrics["source_count"],
                    metrics["selected_reddit"],
                    metrics["focus_topics"],
                    metrics["x_signals"],
                    metrics["short_items"],
                    metrics["duplicate_filtered"],
                    metrics["discussion_filtered"],
                    metrics["discussion_threads"],
                    metrics["selected_replies"],
                    metrics["article_candidates"],
                    metrics["articles_fetched"],
                    now_iso(),
                ),
            )

    def review_entries(
        self,
        report_date: str,
        run_id: int | None = None,
    ) -> list[dict[str, str]]:
        if run_id is not None:
            rows = self.connection.execute(
                """
                SELECT d.document_type, d.path, e.section, e.title, e.event_key,
                       e.source_url, e.article_url, e.position
                FROM report_documents d
                JOIN report_entries e ON e.document_id = d.id
                WHERE d.report_date = ? AND d.run_id = ?
                ORDER BY
                    CASE d.document_type WHEN 'daily-report' THEN 0 ELSE 1 END,
                    e.position
                """,
                (report_date, run_id),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT d.document_type, d.path, e.section, e.title, e.event_key,
                       e.source_url, e.article_url, e.position
                FROM report_documents d
                JOIN report_entries e ON e.document_id = d.id
                JOIN report_runs r ON r.id = d.run_id
                WHERE d.report_date = ?
                  AND r.status IN ('success', 'imported')
                  AND d.id IN (
                      SELECT MAX(d2.id)
                      FROM report_documents d2
                      JOIN report_runs r2 ON r2.id = d2.run_id
                      WHERE d2.report_date = ?
                        AND r2.status IN ('success', 'imported')
                      GROUP BY d2.document_type
                  )
                ORDER BY
                    CASE d.document_type WHEN 'daily-report' THEN 0 ELSE 1 END,
                    e.position
                """,
                (report_date, report_date),
            ).fetchall()
        allowed_sections = {
            "daily-report": {"重点主题", "历史延续", "短讯与观察", "资讯与文章"},
            "article-digest": {"必读", "值得略读"},
        }
        entries: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            document_type = str(row["document_type"])
            section = str(row["section"])
            title = str(row["title"]).strip()
            if not title or section not in allowed_sections.get(document_type, set()):
                continue
            dedupe_key = (document_type, title)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            identity = str(row["article_url"] or row["event_key"] or row["source_url"])
            if not identity:
                identity = title
            entry_key = hashlib.sha256(
                f"{document_type}\n{identity}".encode("utf-8")
            ).hexdigest()[:20]
            entries.append(
                {
                    "entry_key": entry_key,
                    "document_type": document_type,
                    "section": section,
                    "title": title,
                    "source_url": str(row["article_url"] or row["source_url"]),
                }
            )
        return entries

    def record_feedback(
        self,
        report_date: str,
        document_type: str,
        entry_key: str,
        rating: str,
        note: str = "",
    ) -> None:
        timestamp = now_iso()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO feedback(
                    report_date, document_type, entry_key, rating, note,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_date, document_type, entry_key)
                DO UPDATE SET
                    rating = excluded.rating,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    report_date,
                    document_type,
                    entry_key,
                    rating,
                    note,
                    timestamp,
                    timestamp,
                ),
            )

    def feedback_summary(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT rating, COUNT(*) AS count FROM feedback GROUP BY rating"
        ).fetchall()
        return {str(row["rating"]): int(row["count"]) for row in rows}

    def recent_quality_snapshots(self, limit: int = 7) -> list[dict[str, int | str]]:
        rows = self.connection.execute(
            """
            SELECT q.*
            FROM quality_snapshots q
            JOIN report_runs r ON r.id = q.run_id
            WHERE r.status = 'success'
              AND q.id IN (
                  SELECT MAX(q2.id)
                  FROM quality_snapshots q2
                  JOIN report_runs r2 ON r2.id = q2.run_id
                  WHERE r2.status = 'success'
                  GROUP BY q2.report_date
              )
            ORDER BY q.report_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_run_health(
        self,
        limit: int = 7,
        report_date: str | None = None,
    ) -> list[dict[str, object]]:
        where = "WHERE r.report_date = ?" if report_date else ""
        parameters: tuple[object, ...] = (
            (report_date, limit) if report_date else (limit,)
        )
        rows = self.connection.execute(
            f"""
            SELECT
                r.id AS run_id,
                r.report_date,
                r.mode,
                r.status,
                r.started_at,
                r.finished_at,
                r.source_count,
                r.error,
                m.collection_seconds,
                m.reddit_seconds,
                m.x_seconds,
                m.article_seconds,
                m.translation_seconds,
                m.render_seconds,
                m.publish_seconds,
                m.total_seconds,
                m.command_total,
                m.command_succeeded,
                m.reddit_total,
                m.reddit_succeeded,
                m.x_total,
                m.x_succeeded,
                m.translation_total,
                m.translation_succeeded,
                m.article_candidates,
                m.articles_fetched,
                m.failed_stage
            FROM report_runs r
            LEFT JOIN run_metrics m ON m.run_id = r.id
            {where}
            ORDER BY r.id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]

    def successful_run_dates(self, limit: int = 30) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT report_date
            FROM report_runs
            WHERE status = 'success'
            ORDER BY report_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [str(row["report_date"]) for row in rows]

    def has_successful_run(self, report_date: str) -> bool:
        row = self.connection.execute(
            """
            SELECT report_path, article_path
            FROM report_runs
            WHERE report_date = ? AND status = 'success'
            ORDER BY id DESC
            LIMIT 1
            """,
            (report_date,),
        ).fetchone()
        if row is None:
            return False
        paths = [Path(str(row["report_path"])), Path(str(row["article_path"]))]
        return all(
            str(path) not in {"", "."}
            and path.is_file()
            and path.stat().st_size > 0
            for path in paths
        )

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
            SELECT d.markdown
            FROM report_documents d
            JOIN report_runs r ON r.id = d.run_id
            WHERE d.report_date >= ? AND d.report_date < ?
              AND d.document_type IN ({placeholders})
              AND r.status IN ('success', 'imported')
              AND d.id IN (
                  SELECT MAX(d2.id)
                  FROM report_documents d2
                  JOIN report_runs r2 ON r2.id = d2.run_id
                  WHERE r2.status IN ('success', 'imported')
                  GROUP BY d2.report_date, d2.document_type
              )
            ORDER BY d.report_date, d.id
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
            "quality_snapshots",
            "run_metrics",
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


def markdown_metric(markdown: str, label: str, detail: str = "") -> int:
    for line in markdown.splitlines():
        if not line.startswith(f"- {label}："):
            continue
        value = line.split("：", 1)[1]
        if detail:
            match = re.search(rf"{re.escape(detail)}\s*(\d+)", value)
        else:
            match = re.search(r"(\d+)", value)
        return int(match.group(1)) if match else 0
    return 0


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
