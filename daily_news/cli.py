"""Command-line interface for daily-news."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {None, "run"}:
        if args.command == "run" and args.skip_existing and successful_today():
            print("今天已有成功日报，跳过重复运行。")
            return
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
    if args.command == "schedule":
        handle_schedule(args)
        return
    parser.error("unknown command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-news")
    subparsers = parser.add_subparsers(dest="command")
    run = subparsers.add_parser("run", help="生成当天日报")
    run.add_argument(
        "--skip-existing",
        action="store_true",
        help="当天已有成功日报时跳过",
    )

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

    schedule = subparsers.add_parser("schedule", help="管理 macOS 每日自动运行")
    schedule_subparsers = schedule.add_subparsers(
        dest="schedule_command",
        required=True,
    )
    install = schedule_subparsers.add_parser("install", help="安装 LaunchAgent")
    install.add_argument("--hour", type=int, default=8, help="小时，默认 8")
    install.add_argument("--minute", type=int, default=30, help="分钟，默认 30")
    schedule_subparsers.add_parser("status", help="查看 LaunchAgent 状态")
    schedule_subparsers.add_parser("uninstall", help="卸载 LaunchAgent")
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
    from daily_news.scheduler import read_schedule_time

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
    schedule_time = read_schedule_time()
    if schedule_time:
        print(f"自动运行：每天 {schedule_time[0]:02d}:{schedule_time[1]:02d}")
    else:
        print("自动运行：未安装")
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


def handle_schedule(args: argparse.Namespace) -> None:
    from daily_news.scheduler import (
        install_schedule,
        launch_agent_path,
        read_schedule_time,
        schedule_status,
        uninstall_schedule,
    )

    project_root = Path(__file__).resolve().parent.parent
    if args.schedule_command == "install":
        try:
            path = install_schedule(project_root, args.hour, args.minute)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            raise SystemExit(f"安装自动运行失败：{exc}") from exc
        print(f"已安装：每天 {args.hour:02d}:{args.minute:02d}")
        print(path)
        return
    if args.schedule_command == "uninstall":
        removed = uninstall_schedule()
        print("已卸载。" if removed else "自动运行尚未安装。")
        return
    loaded, detail = schedule_status()
    schedule_time = read_schedule_time()
    if schedule_time:
        print(f"配置时间：每天 {schedule_time[0]:02d}:{schedule_time[1]:02d}")
        print(launch_agent_path())
    else:
        print("配置文件：未安装")
    print("运行状态：已加载" if loaded else "运行状态：未加载")
    if schedule_time and not loaded and detail:
        print(detail)


def successful_today() -> bool:
    from daily_news.settings import DB_PATH
    from daily_news.storage import DailyNewsStore

    today = dt.date.today().isoformat()
    with DailyNewsStore(DB_PATH) as store:
        return store.has_successful_run(today)


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
