"""Isolated full-test runner for daily-news."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass
class Check:
    name: str
    passed: bool
    duration: float
    detail: str


class FullTestRunner:
    def __init__(self, project_root: Path, report_dir: Path) -> None:
        self.project_root = project_root.resolve()
        self.report_dir = report_dir
        self.checks: list[Check] = []
        self.started_at = dt.datetime.now()

    def run(self, modes: list[str], cached_date: str | None) -> Path:
        for mode in modes:
            print(f"\n[full-test] {mode}", flush=True)
            if mode == "offline":
                self.run_offline()
            elif mode == "cached":
                self.run_cached(cached_date)
            elif mode == "live":
                self.run_live()
        return self.write_report(modes)

    def run_offline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-news-offline-") as directory:
            workdir = Path(directory)
            env = self.isolated_env(workdir)
            env["PYTHONPYCACHEPREFIX"] = str(workdir / "pycache")
            self.command_check(
                "单元测试",
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                cwd=self.project_root,
                env=env,
                timeout=120,
            )
            self.command_check(
                "Python 编译检查",
                [
                    sys.executable,
                    "-m",
                    "compileall",
                    "-q",
                    "daily_news",
                    "main.py",
                ],
                cwd=self.project_root,
                env=env,
                timeout=60,
            )
            self.command_check(
                "CLI 帮助入口",
                [sys.executable, str(self.project_root / "main.py"), "--help"],
                cwd=workdir,
                env=env,
                timeout=30,
                expected_text="schedule",
            )
            self.command_check(
                "SQLite 初始化与完整性",
                [sys.executable, str(self.project_root / "main.py"), "db", "stats"],
                cwd=workdir,
                env=env,
                timeout=30,
                expected_text="完整性：ok",
            )

    def run_cached(self, requested_date: str | None) -> None:
        report_date = requested_date or latest_raw_date(self.project_root / "data" / "raw")
        if not report_date:
            self.add_check("缓存数据发现", False, 0, "没有找到 commands.json")
            return
        source = self.project_root / "data" / "raw" / report_date
        with tempfile.TemporaryDirectory(prefix="daily-news-cached-") as directory:
            workdir = Path(directory)
            target = workdir / "data" / "raw" / report_date
            shutil.copytree(source, target)
            env = self.isolated_env(workdir)
            env.update(
                {
                    "DAILY_NEWS_DATE": report_date,
                    "DAILY_NEWS_FROM_RAW": "1",
                    "DAILY_NEWS_TRANSLATION_PROVIDER": "none",
                    "DAILY_NEWS_ARTICLE_FETCH": "0",
                }
            )
            completed = self.command_check(
                f"缓存端到端生成（{report_date}）",
                [
                    sys.executable,
                    str(self.project_root / "main.py"),
                    "rerun",
                    "--date",
                    report_date,
                ],
                cwd=workdir,
                env=env,
                timeout=180,
            )
            if completed and completed.returncode == 0:
                self.validate_outputs(workdir, report_date, require_live_sources=False)
                self.command_check(
                    "Health CLI",
                    [
                        sys.executable,
                        str(self.project_root / "main.py"),
                        "health",
                        "--date",
                        report_date,
                    ],
                    cwd=workdir,
                    env=env,
                    timeout=30,
                    expected_text="运行健康度",
                )

    def run_live(self) -> None:
        self.command_check(
            "Agent-Reach 渠道诊断",
            ["agent-reach", "doctor", "--json"],
            cwd=self.project_root,
            env=os.environ.copy(),
            timeout=120,
            expected_text='"reddit"',
        )
        report_date = dt.date.today().isoformat()
        with tempfile.TemporaryDirectory(prefix="daily-news-live-") as directory:
            workdir = Path(directory)
            env = self.isolated_env(workdir)
            env.update(
                {
                    "DAILY_NEWS_DATE": report_date,
                    "DAILY_NEWS_FROM_RAW": "0",
                    "DAILY_NEWS_LIMIT": "5",
                    "DAILY_NEWS_TOPIC_COUNT": "3",
                    "DAILY_NEWS_SUBREDDIT_COUNT": "2",
                    "DAILY_NEWS_READ_LIMIT": "3",
                    "DAILY_NEWS_X_LIMIT": "3",
                    "DAILY_NEWS_X_TOPIC_COUNT": "2",
                    "DAILY_NEWS_X_ACCOUNT_COUNT": "2",
                    "DAILY_NEWS_X_THREAD_READ_LIMIT": "2",
                    "DAILY_NEWS_ARTICLE_LIMIT": "3",
                    "DAILY_NEWS_ARTICLE_FETCH_LIMIT": "3",
                    "DAILY_NEWS_TRANSLATION_PROVIDER": "glm",
                    "DAILY_NEWS_ANTHROPIC_MODEL": "glm-5.2",
                    "DAILY_NEWS_ANTHROPIC_DISABLE_THINKING": "1",
                }
            )
            completed = self.command_check(
                "真实在线端到端生成",
                [sys.executable, str(self.project_root / "main.py")],
                cwd=workdir,
                env=env,
                timeout=1200,
            )
            if completed and completed.returncode == 0:
                self.validate_outputs(workdir, report_date, require_live_sources=True)

    def isolated_env(self, workdir: Path) -> dict[str, str]:
        env = os.environ.copy()
        config_path = workdir / "config" / "daily_news.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(self.sanitized_config(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        env.update(
            {
                "DAILY_NEWS_CONFIG": str(config_path),
                "DAILY_NEWS_DB_PATH": str(
                    workdir / "data" / "db" / "daily_news.sqlite3"
                ),
                "DAILY_NEWS_OBSIDIAN_VAULT_DIR": str(
                    workdir / "disabled-obsidian"
                ),
                "PYTHONPATH": str(self.project_root),
            }
        )
        return env

    def sanitized_config(self) -> dict[str, object]:
        path = self.project_root / "config" / "daily_news.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(payload, dict):
            return {}
        blocked = {
            "database_path",
            "obsidian_vault_dir",
            "obsidian_subdir",
        }
        secret_fragments = ("cookie", "token", "secret", "password", "api_key")
        return {
            key: value
            for key, value in payload.items()
            if key not in blocked
            and not any(fragment in key.lower() for fragment in secret_fragments)
        }

    def command_check(
        self,
        name: str,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
        expected_text: str | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        started = time.monotonic()
        print(f"[full-test] running: {name}", flush=True)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            self.add_check(name, False, time.monotonic() - started, str(exc))
            return None
        output = f"{completed.stdout}\n{completed.stderr}".strip()
        passed = completed.returncode == 0
        if expected_text:
            passed = passed and expected_text in output
        detail = f"退出码 {completed.returncode}"
        if not passed and output:
            detail += f"；末尾输出：{tail_text(output)}"
        self.add_check(name, passed, time.monotonic() - started, detail)
        return completed

    def validate_outputs(
        self,
        workdir: Path,
        report_date: str,
        *,
        require_live_sources: bool,
    ) -> None:
        started = time.monotonic()
        required = {
            "日报": workdir / "data" / "reports" / f"{report_date}.md",
            "文章": workdir / "data" / "articles" / f"{report_date}.md",
            "评价": workdir / "data" / "reviews" / f"{report_date}.md",
        }
        missing = [name for name, path in required.items() if not path.exists()]
        headings_ok = all(
            report_date in path.read_text(encoding="utf-8")
            for path in required.values()
            if path.exists()
        )
        self.add_check(
            "Markdown 产物",
            not missing and headings_ok,
            time.monotonic() - started,
            "日报、文章、评价均已生成"
            if not missing and headings_ok
            else f"缺失或日期异常：{', '.join(missing) or '内容'}",
        )

        db_path = workdir / "data" / "db" / "daily_news.sqlite3"
        started = time.monotonic()
        try:
            with sqlite3.connect(db_path) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                row = connection.execute(
                    """
                    SELECT
                        r.status,
                        r.source_count,
                        m.total_seconds,
                        q.selected_reddit,
                        q.focus_topics,
                        q.x_signals,
                        q.short_items
                    FROM report_runs r
                    LEFT JOIN run_metrics m ON m.run_id = r.id
                    LEFT JOIN quality_snapshots q ON q.run_id = r.id
                    WHERE r.report_date = ?
                    ORDER BY r.id DESC
                    LIMIT 1
                    """,
                    (report_date,),
                ).fetchone()
                translations = connection.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN error = '' THEN 1 ELSE 0 END) AS succeeded
                    FROM translations
                    WHERE provider = 'anthropic-compatible'
                      AND model = 'glm-5.2'
                    """
                ).fetchone()
            passed = bool(
                integrity == "ok"
                and row
                and row[0] == "success"
                and row[2] is not None
                and sum(int(value or 0) for value in row[3:7]) > 0
                and (not require_live_sources or int(row[1]) > 0)
            )
            detail = (
                f"完整性 {integrity}，运行状态 {row[0] if row else 'missing'}，"
                f"来源 {row[1] if row else 0}，"
                f"指标 {'已写入' if row and row[2] is not None else '缺失'}，"
                f"有效内容 {sum(int(value or 0) for value in row[3:7]) if row else 0}"
            )
            translation_total = int(translations[0] or 0)
            translation_succeeded = int(translations[1] or 0)
        except (OSError, sqlite3.Error) as exc:
            passed = False
            detail = str(exc)
            translation_total = 0
            translation_succeeded = 0
        self.add_check(
            "SQLite 产物状态",
            passed,
            time.monotonic() - started,
            detail,
        )
        if require_live_sources:
            failures = translation_total - translation_succeeded
            failure_percent = (
                failures * 100 / translation_total if translation_total else 100
            )
            self.add_check(
                "GLM 5.2 翻译落库",
                translation_total > 0
                and translation_succeeded > 0
                and failure_percent <= 20,
                0,
                (
                    f"成功 {translation_succeeded}/{translation_total}，"
                    f"失败率 {failure_percent:.1f}%"
                ),
            )

        raw_path = workdir / "data" / "raw" / report_date / "commands.json"
        started = time.monotonic()
        try:
            commands = json.loads(raw_path.read_text(encoding="utf-8"))
            if not isinstance(commands, list):
                raise TypeError("commands.json 顶层必须是列表")
            successful = [item for item in commands if item.get("ok")]
            titles = [str(item.get("title", "")) for item in successful]
            reddit_ok = any(title.startswith("Reddit") for title in titles)
            twitter_ok = any(title.startswith("Twitter") for title in titles)
            passed = bool(commands) and (
                not require_live_sources or (reddit_ok and twitter_ok)
            )
            detail = (
                f"命令 {len(commands)}，成功 {len(successful)}，"
                f"Reddit {'通过' if reddit_ok else '缺失'}，"
                f"X {'通过' if twitter_ok else '缺失'}"
            )
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            passed = False
            detail = str(exc)
        self.add_check(
            "采集归档",
            passed,
            time.monotonic() - started,
            detail,
        )

    def add_check(
        self,
        name: str,
        passed: bool,
        duration: float,
        detail: str,
    ) -> None:
        self.checks.append(Check(name, passed, duration, detail))
        state = "PASS" if passed else "FAIL"
        print(f"[full-test] {state} {name} ({duration:.1f}s): {detail}", flush=True)

    def write_report(self, modes: list[str]) -> Path:
        finished_at = dt.datetime.now()
        passed = sum(check.passed for check in self.checks)
        total = len(self.checks)
        status = "通过" if passed == total and total else "失败"
        lines = [
            "---",
            f"date: {finished_at.date().isoformat()}",
            "type: full-test",
            f"status: {status}",
            "---",
            "",
            f"# 全量测试报告 - {finished_at:%Y-%m-%d %H:%M:%S}",
            "",
            f"- 模式：{', '.join(modes)}",
            f"- 结果：{passed}/{total} 通过",
            f"- 总耗时：{(finished_at - self.started_at).total_seconds():.1f} 秒",
            "",
            "## 检查项",
            "",
            "| 状态 | 检查项 | 耗时 | 详情 |",
            "| --- | --- | ---: | --- |",
        ]
        for check in self.checks:
            marker = "通过" if check.passed else "失败"
            detail = check.detail.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {marker} | {check.name} | {check.duration:.1f}s | {detail} |"
            )
        lines.extend(
            [
                "",
                "## 说明",
                "",
                "- 测试在临时目录执行，不写入正式日报、SQLite 或 Obsidian。",
                "- 在线模式使用受限采样量验证完整链路，避免高频访问平台。",
                "- 报告不记录 Cookie、Token、密码或 API 密钥。",
                "",
            ]
        )
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / (
            f"full-test-{finished_at:%Y%m%d-%H%M%S}-{'-'.join(modes)}.md"
        )
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n[full-test] report: {path}", flush=True)
        return path


def latest_raw_date(raw_dir: Path) -> str | None:
    candidates = []
    for path in raw_dir.glob("*/commands.json"):
        try:
            dt.date.fromisoformat(path.parent.name)
        except ValueError:
            continue
        candidates.append(path.parent.name)
    candidates.sort()
    return candidates[-1] if candidates else None


def tail_text(value: str, limit: int = 800) -> str:
    compact = " ".join(value.split())
    return compact[-limit:]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 Daily News 全量测试")
    parser.add_argument(
        "mode",
        choices=["all", "offline", "cached", "live"],
        nargs="?",
        default="all",
    )
    parser.add_argument("--date", help="cached 模式使用的 YYYY-MM-DD")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("data/test-reports"),
        help="验收报告目录",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.date:
        try:
            dt.date.fromisoformat(args.date)
        except ValueError as exc:
            raise SystemExit("--date 必须是 YYYY-MM-DD") from exc
    project_root = Path(__file__).resolve().parent.parent
    modes = ["offline", "cached", "live"] if args.mode == "all" else [args.mode]
    runner = FullTestRunner(project_root, args.report_dir.resolve())
    runner.run(modes, args.date)
    return 0 if runner.checks and all(check.passed for check in runner.checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
