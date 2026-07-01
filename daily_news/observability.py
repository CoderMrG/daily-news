"""Runtime metrics and health presentation for daily-news."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Iterable

from daily_news.models import CommandResult, Enrichment, SourceItem


@dataclass
class RunMetrics:
    report_date: str
    collection_seconds: float = 0.0
    reddit_seconds: float = 0.0
    x_seconds: float = 0.0
    article_seconds: float = 0.0
    translation_seconds: float = 0.0
    render_seconds: float = 0.0
    publish_seconds: float = 0.0
    total_seconds: float = 0.0
    command_total: int = 0
    command_succeeded: int = 0
    reddit_total: int = 0
    reddit_succeeded: int = 0
    x_total: int = 0
    x_succeeded: int = 0
    translation_total: int = 0
    translation_succeeded: int = 0
    article_candidates: int = 0
    articles_fetched: int = 0
    failed_stage: str = ""
    _started_at: float = field(default_factory=time.monotonic, repr=False)

    def record_commands(self, results: Iterable[CommandResult]) -> None:
        commands = list(results)
        self.command_total = len(commands)
        self.command_succeeded = sum(result.ok for result in commands)
        reddit = [result for result in commands if result.title.startswith("Reddit")]
        twitter = [result for result in commands if result.title.startswith("Twitter")]
        self.reddit_total = len(reddit)
        self.reddit_succeeded = sum(result.ok for result in reddit)
        self.x_total = len(twitter)
        self.x_succeeded = sum(result.ok for result in twitter)
        self.reddit_seconds = sum(result.duration_seconds for result in reddit)
        self.x_seconds = sum(result.duration_seconds for result in twitter)

    def record_translations(
        self,
        items: Iterable[SourceItem],
        enrichments: dict[str, Enrichment],
        duration: float,
    ) -> None:
        targets = list(items)
        self.translation_seconds += duration
        self.translation_total += len(targets)
        self.translation_succeeded += sum(
            item.item_id in enrichments
            and not enrichments[item.item_id].error
            and bool(
                enrichments[item.item_id].zh_translation
                or enrichments[item.item_id].zh_title
            )
            for item in targets
        )

    def finish(self) -> None:
        self.total_seconds = time.monotonic() - self._started_at

    def as_record(self) -> dict[str, str | int | float]:
        record = asdict(self)
        record.pop("_started_at", None)
        return record


def format_health_row(row: dict[str, object]) -> str:
    status = health_status_label(str(row.get("status", "")))
    if row.get("total_seconds") is None:
        return f"{row.get('report_date', '')} {status}；历史运行无指标"
    total_seconds = float(row.get("total_seconds") or 0)
    reddit = ratio(row, "reddit_succeeded", "reddit_total")
    twitter = ratio(row, "x_succeeded", "x_total")
    translations = ratio(
        row,
        "translation_succeeded",
        "translation_total",
    )
    articles = ratio(row, "articles_fetched", "article_candidates")
    timings = (
        f"采集 {format_duration(float(row.get('collection_seconds') or 0))}"
        f"（Reddit {format_duration(float(row.get('reddit_seconds') or 0))} / "
        f"X {format_duration(float(row.get('x_seconds') or 0))}）；"
        f"文章 {format_duration(float(row.get('article_seconds') or 0))}；"
        f"翻译 {format_duration(float(row.get('translation_seconds') or 0))}；"
        f"渲染 {format_duration(float(row.get('render_seconds') or 0))}；"
        f"发布 {format_duration(float(row.get('publish_seconds') or 0))}"
    )
    stage = str(row.get("failed_stage") or "")
    suffix = f"；失败阶段 {stage}" if stage else ""
    return (
        f"{row.get('report_date', '')} {status}；"
        f"总耗时 {format_duration(total_seconds)}；"
        f"Reddit {reddit}；X {twitter}；翻译 {translations}；文章 {articles}；"
        f"{timings}"
        f"{suffix}"
    )


def health_notification(row: dict[str, object] | None, report_date: str) -> tuple[str, str]:
    if row is None:
        return "Daily News 未运行", f"{report_date} 暂无运行记录"
    status = str(row.get("status", ""))
    if status != "success":
        stage = str(row.get("failed_stage") or "unknown")
        error = compact_text(str(row.get("error") or ""), 90)
        detail = f"{report_date} 失败于 {stage}"
        if error:
            detail = f"{detail}：{error}"
        return "Daily News 自动运行失败", detail
    return (
        "Daily News 已生成",
        (
            f"{report_date}，耗时 "
            f"{format_duration(float(row.get('total_seconds') or 0))}；"
            f"Reddit {ratio(row, 'reddit_succeeded', 'reddit_total')}；"
            f"X {ratio(row, 'x_succeeded', 'x_total')}；"
            f"翻译 {ratio(row, 'translation_succeeded', 'translation_total')}；"
            f"文章 {ratio(row, 'articles_fetched', 'article_candidates')}"
        ),
    )


def send_macos_notification(title: str, message: str) -> bool:
    if sys.platform != "darwin":
        return False
    script = (
        f"display notification {applescript_string(message)} "
        f"with title {applescript_string(title)}"
    )
    completed = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def health_status_label(status: str) -> str:
    return {
        "success": "成功",
        "failed": "失败",
        "interrupted": "已中断",
        "running": "运行中",
        "imported": "已导入",
    }.get(status, status or "未知")


def ratio(row: dict[str, object], numerator: str, denominator: str) -> str:
    return f"{int(row.get(numerator) or 0)}/{int(row.get(denominator) or 0)}"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}秒"
    minutes, remainder = divmod(int(round(seconds)), 60)
    return f"{minutes}分{remainder:02d}秒"


def compact_text(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    return cleaned[:limit]


def applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'
