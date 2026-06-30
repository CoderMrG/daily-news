"""macOS launchd integration for daily-news."""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
from pathlib import Path


LABEL = "com.codermrg.daily-news"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_launch_agent(
    project_root: Path,
    hour: int,
    minute: int,
) -> dict[str, object]:
    script = project_root / "scripts" / "run_scheduled.sh"
    logs = project_root / "data" / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/open",
            "-na",
            "/Applications/Ghostty.app",
            "--args",
            "-e",
            "/bin/zsh",
            "-lic",
            f"exec {shlex.quote(str(script))}",
        ],
        "StartCalendarInterval": {
            "Hour": hour,
            "Minute": minute,
        },
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "launchd.out.log"),
        "StandardErrorPath": str(logs / "launchd.err.log"),
    }


def install_schedule(project_root: Path, hour: int, minute: int) -> Path:
    validate_time(hour, minute)
    plist_path = launch_agent_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    (project_root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    payload = plistlib.dumps(
        build_launch_agent(project_root, hour, minute),
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )
    temporary = plist_path.with_suffix(".plist.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, plist_path)

    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        check=True,
    )
    subprocess.run(
        ["launchctl", "enable", f"{domain}/{LABEL}"],
        check=True,
    )
    return plist_path


def uninstall_schedule() -> bool:
    plist_path = launch_agent_path()
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if not plist_path.exists():
        return False
    plist_path.unlink()
    return True


def schedule_status() -> tuple[bool, str]:
    domain = f"gui/{os.getuid()}/{LABEL}"
    result = subprocess.run(
        ["launchctl", "print", domain],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, result.stdout


def read_schedule_time() -> tuple[int, int] | None:
    path = launch_agent_path()
    if not path.exists():
        return None
    with path.open("rb") as handle:
        payload = plistlib.load(handle)
    interval = payload.get("StartCalendarInterval", {})
    return int(interval.get("Hour", 0)), int(interval.get("Minute", 0))


def validate_time(hour: int, minute: int) -> None:
    if not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    if not 0 <= minute <= 59:
        raise ValueError("minute must be between 0 and 59")
