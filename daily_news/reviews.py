"""Obsidian-friendly review notes and feedback ingestion."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from daily_news.storage import DailyNewsStore


RATING_ALIASES = {
    "有用": "useful",
    "useful": "useful",
    "一般": "normal",
    "normal": "normal",
    "无用": "useless",
    "useless": "useless",
    "跟进": "followup",
    "followup": "followup",
}

RATING_LABELS = {
    "useful": "有用",
    "normal": "一般",
    "useless": "无用",
    "followup": "跟进",
}


def build_review_markdown(report_date: str, entries: list[dict[str, str]]) -> str:
    lines = [
        "---",
        f"date: {report_date}",
        "type: daily-review",
        "generated_by: daily-news",
        "tags:",
        "  - daily-news",
        "  - review",
        "---",
        "",
        f"# 日报反馈 - {report_date}",
        "",
    ]
    sections = [
        ("日报条目", "daily-report", "reports"),
        ("文章精选", "article-digest", "articles"),
    ]
    for heading, document_type, directory in sections:
        matching = [entry for entry in entries if entry["document_type"] == document_type]
        lines.extend([f"## {heading}", ""])
        if not matching:
            lines.extend(["_当天没有可评价条目。_", ""])
            continue
        for entry in matching:
            title = clean_wiki_text(entry["title"])
            target = f"../{directory}/{report_date}#{title}"
            lines.extend(
                [
                    (
                        f"- [ ] [[{target}|{title}]] "
                        f"<!-- daily-news:key={entry['entry_key']};type={document_type} -->"
                    ),
                    "  - 评价：待评价",
                    "  - 备注：",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_review_feedback(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    marker = re.compile(
        r"<!--\s*daily-news:key=([a-f0-9]+);type=([a-z-]+)\s*-->"
    )
    for line in markdown.splitlines():
        match = marker.search(line)
        if match:
            if current:
                append_feedback_row(rows, current)
            current = {
                "entry_key": match.group(1),
                "document_type": match.group(2),
                "rating": "",
                "note": "",
            }
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- 评价："):
            current["rating"] = stripped.split("：", 1)[1].strip()
        elif stripped.startswith("- 备注："):
            current["note"] = stripped.split("：", 1)[1].strip()
    if current:
        append_feedback_row(rows, current)
    return rows


def append_feedback_row(rows: list[dict[str, str]], row: dict[str, str]) -> None:
    rating = normalize_rating(row.get("rating", ""))
    if not rating:
        return
    rows.append(
        {
            "entry_key": row["entry_key"],
            "document_type": row["document_type"],
            "rating": rating,
            "note": row.get("note", ""),
        }
    )


def normalize_rating(value: str) -> str:
    return RATING_ALIASES.get(value.strip().lower(), "")


def sync_review_feedback(
    store: DailyNewsStore,
    paths: Iterable[Path],
) -> int:
    synced = 0
    seen: set[tuple[str, str, str]] = set()
    for path in paths:
        if not path.exists():
            continue
        report_date = path.stem
        try:
            markdown = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for row in parse_review_feedback(markdown):
            key = (report_date, row["document_type"], row["entry_key"])
            if key in seen:
                continue
            seen.add(key)
            store.record_feedback(
                report_date,
                row["document_type"],
                row["entry_key"],
                row["rating"],
                row["note"],
            )
            synced += 1
    return synced


def review_paths(
    local_dir: Path,
    obsidian_vault_dir: str,
    obsidian_subdir: str,
) -> list[Path]:
    paths: list[Path] = []
    if obsidian_vault_dir:
        obsidian_dir = (
            Path(obsidian_vault_dir).expanduser()
            / obsidian_subdir
            / "reviews"
        )
        paths.extend(sorted(obsidian_dir.glob("????-??-??.md")))
    paths.extend(sorted(local_dir.glob("????-??-??.md")))
    return paths


def write_review_outputs(
    report_date: str,
    markdown: str,
    local_dir: Path,
    obsidian_vault_dir: str,
    obsidian_subdir: str,
) -> list[Path]:
    local_path = local_dir / f"{report_date}.md"
    obsidian_path: Path | None = None
    if obsidian_vault_dir:
        vault = Path(obsidian_vault_dir).expanduser()
        if vault.exists():
            obsidian_path = vault / obsidian_subdir / "reviews" / f"{report_date}.md"

    existing = ""
    if obsidian_path and obsidian_path.exists():
        existing = obsidian_path.read_text(encoding="utf-8")
    elif local_path.exists():
        existing = local_path.read_text(encoding="utf-8")
    merged = merge_review_markdown(markdown, existing)
    atomic_write_text(local_path, merged)

    written = [local_path]
    if obsidian_path:
        atomic_write_text(obsidian_path, merged)
        written.append(obsidian_path)
    return written


def merge_review_markdown(generated: str, existing: str) -> str:
    if not existing:
        return generated
    if "daily-news:key=" not in existing:
        return existing
    feedback = {
        row["entry_key"]: row
        for row in parse_review_feedback(existing)
    }
    if not feedback:
        return generated
    lines: list[str] = []
    current_key = ""
    marker = re.compile(r"daily-news:key=([a-f0-9]+)")
    for line in generated.splitlines():
        match = marker.search(line)
        if match:
            current_key = match.group(1)
            lines.append(line)
            continue
        row = feedback.get(current_key)
        stripped = line.strip()
        if row and stripped.startswith("- 评价："):
            indent = line[: len(line) - len(line.lstrip())]
            line = f"{indent}- 评价：{RATING_LABELS[row['rating']]}"
        elif row and stripped.startswith("- 备注："):
            indent = line[: len(line) - len(line.lstrip())]
            line = f"{indent}- 备注：{row['note']}"
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def clean_wiki_text(value: str) -> str:
    return re.sub(r"[\[\]|#]", " ", value).strip()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
