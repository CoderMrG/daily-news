"""Runtime safety primitives for unattended execution."""

from __future__ import annotations

import contextvars
import datetime as dt
import fcntl
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from daily_news.models import CommandResult


class RunAlreadyActive(RuntimeError):
    """Raised when another daily-news process owns the run lock."""


class RunBudgetExceeded(RuntimeError):
    """Raised when the configured whole-run deadline has expired."""


class RunInterrupted(RuntimeError):
    """Raised when the operating system asks the run to terminate."""


_RUN_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "daily_news_run_deadline",
    default=None,
)


class RunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: object | None = None

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "unknown"
            handle.close()
            raise RunAlreadyActive(f"已有日报任务正在运行：{owner}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} started={time.time():.0f}\n")
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is None:
            return
        handle = self._handle
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self._handle = None


class FileRollback:
    """Restore a group of files when publication raises an exception."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self.paths = list(dict.fromkeys(paths))
        self.snapshots: dict[Path, bytes | None] = {}

    def __enter__(self) -> FileRollback:
        for path in self.paths:
            self.snapshots[path] = path.read_bytes() if path.exists() else None
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        if exc_type is None:
            return
        for path, content in self.snapshots.items():
            if content is None:
                if path.exists():
                    path.unlink()
                continue
            atomic_write_bytes(path, content)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.rollback")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def cleanup_dated_directories(
    root: Path,
    retention_days: int,
    *,
    today: dt.date | None = None,
) -> list[Path]:
    if not root.exists():
        return []
    cutoff = (today or dt.date.today()) - dt.timedelta(days=retention_days)
    removed: list[Path] = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            directory_date = dt.date.fromisoformat(path.name)
        except ValueError:
            continue
        if directory_date >= cutoff:
            continue
        shutil.rmtree(path)
        removed.append(path)
    return removed


def start_run_budget(seconds: int) -> contextvars.Token[float | None]:
    return _RUN_DEADLINE.set(time.monotonic() + seconds)


def reset_run_budget(token: contextvars.Token[float | None]) -> None:
    _RUN_DEADLINE.reset(token)


def remaining_timeout(default_seconds: int) -> float:
    deadline = _RUN_DEADLINE.get()
    if deadline is None:
        return float(default_seconds)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RunBudgetExceeded("日报运行超过总时间预算")
    return max(1.0, min(float(default_seconds), remaining))


def ensure_run_budget() -> None:
    remaining_timeout(1)


@dataclass
class CollectionGuard:
    failure_limit: int
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    open_channels: set[str] = field(default_factory=set)

    def execute(
        self,
        channel: str,
        title: str,
        command: list[str],
        runner: Callable[[str, list[str], float], CommandResult],
        timeout_seconds: int,
    ) -> CommandResult:
        if channel in self.open_channels:
            return skipped_result(
                title,
                command,
                f"{channel} circuit is open after consecutive failures",
            )
        try:
            timeout = remaining_timeout(timeout_seconds)
        except RunBudgetExceeded as exc:
            self.open_channels.add(channel)
            return skipped_result(title, command, str(exc))

        result = runner(title, command, timeout)
        if result.ok:
            self.consecutive_failures[channel] = 0
            return result
        failures = self.consecutive_failures.get(channel, 0) + 1
        self.consecutive_failures[channel] = failures
        if failures >= self.failure_limit:
            self.open_channels.add(channel)
        return result


def skipped_result(
    title: str,
    command: list[str],
    reason: str,
) -> CommandResult:
    return CommandResult(
        title=title,
        command=command,
        ok=False,
        stdout="",
        stderr=f"Skipped: {reason}",
        duration_seconds=0,
    )
