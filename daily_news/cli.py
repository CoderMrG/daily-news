"""Command-line interface for daily-news."""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {None, "run"}:
        run_daily_news()
        return
    if args.command == "rerun":
        validate_date(args.date)
        os.environ["DAILY_NEWS_DATE"] = args.date
        os.environ["DAILY_NEWS_FROM_RAW"] = "1"
        run_daily_news()
        return
    if args.command == "status":
        show_status(args.days)
        return
    if args.command == "db" and args.db_command == "stats":
        show_database_stats()
        return
    if args.command == "feedback":
        handle_feedback(args)
        return
    parser.error("unknown command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-news")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run", help="生成当天日报")

    rerun = subparsers.add_parser("rerun", help="使用 raw 数据重新生成指定日期")
    rerun.add_argument("--date", required=True, help="日期，格式 YYYY-MM-DD")

    status = subparsers.add_parser("status", help="查看最近运行与 7 日质量观察")
    status.add_argument("--days", type=int, default=7, help="显示最近 N 天，默认 7")

    db = subparsers.add_parser("db", help="数据库工具")
    db_subparsers = db.add_subparsers(dest="db_command", required=True)
    db_subparsers.add_parser("stats", help="查看数据库统计和完整性")

    feedback = subparsers.add_parser("feedback", help="查看或写入条目反馈")
    feedback.add_argument("--date", required=True, help="日期，格式 YYYY-MM-DD")
    feedback.add_argument("--list", action="store_true", help="列出当天可评价条目")
    feedback.add_argument("--entry", help="评价文件中的 entry key")
    feedback.add_argument(
        "--type",
        choices=["daily-report", "article-digest"],
        default="daily-report",
        help="条目所属文档",
    )
    feedback.add_argument("--rating", help="有用/一般/无用/跟进")
    feedback.add_argument("--note", default="", help="可选备注")
    return parser


def run_daily_news() -> None:
    from daily_news.app import main as app_main

    app_main()


def show_status(days: int) -> None:
    from daily_news.reviews import review_paths, sync_review_feedback
    from daily_news.settings import (
        DB_PATH,
        OBSIDIAN_SUBDIR,
        OBSIDIAN_VAULT_DIR,
        REVIEW_DIR,
    )
    from daily_news.storage import DailyNewsStore

    days = max(1, min(days, 30))
    with DailyNewsStore(DB_PATH) as store:
        sync_review_feedback(
            store,
            review_paths(REVIEW_DIR, OBSIDIAN_VAULT_DIR, OBSIDIAN_SUBDIR),
        )
        snapshots = store.recent_quality_snapshots(days)
        dates = store.successful_run_dates(30)
        feedback = store.feedback_summary()

    print(f"数据库：{DB_PATH}")
    print(f"连续运行：{success_streak(dates)} 天")
    print(f"7 日观察：{min(len(set(dates)), 7)}/7 天")
    print(
        "反馈："
        f"有用 {feedback.get('useful', 0)} / "
        f"一般 {feedback.get('normal', 0)} / "
        f"无用 {feedback.get('useless', 0)} / "
        f"跟进 {feedback.get('followup', 0)}"
    )
    if not snapshots:
        print("暂无质量快照。")
        return
    print("")
    print("日期        议题  X信号  讨论线程  文章读取/候选  去重")
    for row in snapshots:
        print(
            f"{row['report_date']}  "
            f"{row['focus_topics']:>4}  "
            f"{row['x_signals']:>5}  "
            f"{row['discussion_threads']:>8}  "
            f"{row['articles_fetched']:>4}/{row['article_candidates']:<4}  "
            f"{row['duplicate_filtered']:>4}"
        )


def show_database_stats() -> None:
    from daily_news.settings import DB_PATH
    from daily_news.storage import DailyNewsStore

    with DailyNewsStore(DB_PATH) as store:
        counts = store.table_counts()
        integrity = str(store.connection.execute("PRAGMA integrity_check").fetchone()[0])
        versions = [
            str(row["version"])
            for row in store.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    size = Path(DB_PATH).stat().st_size if Path(DB_PATH).exists() else 0
    print(f"数据库：{DB_PATH}")
    print(f"大小：{size / 1024 / 1024:.2f} MB")
    print(f"迁移版本：{', '.join(versions)}")
    print(f"完整性：{integrity}")
    for table, count in counts.items():
        print(f"{table}: {count}")


def handle_feedback(args: argparse.Namespace) -> None:
    from daily_news.reviews import RATING_LABELS, normalize_rating
    from daily_news.settings import DB_PATH
    from daily_news.storage import DailyNewsStore

    validate_date(args.date)
    with DailyNewsStore(DB_PATH) as store:
        if args.list:
            entries = store.review_entries(args.date)
            if not entries:
                print("当天没有可评价条目。")
                return
            for entry in entries:
                print(
                    f"{entry['entry_key']}  "
                    f"{entry['document_type']}  "
                    f"{entry['title']}"
                )
            return
        if not args.entry or not args.rating:
            raise SystemExit("feedback 需要 --entry 和 --rating，或使用 --list")
        rating = normalize_rating(args.rating)
        if not rating:
            choices = " / ".join(RATING_LABELS.values())
            raise SystemExit(f"无效评价，可选：{choices}")
        store.record_feedback(
            args.date,
            args.type,
            args.entry,
            rating,
            args.note,
        )
    print(f"已记录：{args.date} {args.entry} {RATING_LABELS[rating]}")


def success_streak(dates: list[str]) -> int:
    if not dates:
        return 0
    parsed = sorted({dt.date.fromisoformat(value) for value in dates}, reverse=True)
    streak = 1
    for previous, current in zip(parsed, parsed[1:]):
        if previous - current != dt.timedelta(days=1):
            break
        streak += 1
    return streak


def validate_date(value: str) -> None:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"无效日期：{value}，应为 YYYY-MM-DD") from exc
