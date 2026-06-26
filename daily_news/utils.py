"""Parsing and text utility helpers for daily-news."""

from __future__ import annotations

import datetime as dt
import re
import textwrap
from typing import Any, Iterable


def count_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if value in {"''", '""'}:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace("\\'", "'").replace('\\"', '"')


def parse_yaml_block(lines: list[str], index: int, parent_indent: int, folded: bool) -> tuple[str, int]:
    block: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line.strip() and count_indent(line) <= parent_indent:
            break
        if not line.strip():
            block.append("")
        else:
            block.append(line[min(len(line), parent_indent + 2) :])
        index += 1
    if not folded:
        return "\n".join(block).strip(), index

    paragraphs: list[str] = []
    current: list[str] = []
    for line in block:
        if not line.strip():
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
        else:
            current.append(line.strip())
    if current:
        paragraphs.append(" ".join(current).strip())
    return "\n\n".join(paragraphs), index


def parse_simple_yaml_list(text: str) -> list[dict[str, Any]]:
    """Parse the limited OpenCLI YAML shape without external dependencies."""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    parent_key: str | None = None
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if line.startswith("- "):
            current = {}
            parent_key = None
            items.append(current)
            rest = line[2:].strip()
            if rest and ":" in rest:
                key, value = rest.split(":", 1)
                current[key.strip()] = strip_yaml_scalar(value)
            index += 1
            continue

        if current is not None and count_indent(line) == 2 and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value in {"|-", "|", ">-", ">"}:
                block, index = parse_yaml_block(lines, index + 1, 2, value.startswith(">"))
                current[key] = block
                parent_key = None
                continue
            current[key] = strip_yaml_scalar(value)
            parent_key = key if value == "" else None
            index += 1
            continue

        if current is not None and parent_key and count_indent(line) == 4 and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value in {"|-", "|", ">-", ">"}:
                block, index = parse_yaml_block(lines, index + 1, 4, value.startswith(">"))
                current[f"{parent_key}.{key}"] = block
                continue
            current[f"{parent_key}.{key}"] = strip_yaml_scalar(value)
        index += 1

    return items


def as_int(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").strip()
    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_epoch_datetime(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = float(str(value).strip())
    except ValueError:
        return None
    try:
        return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def parse_twitter_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    formats = [
        "%a %b %d %H:%M:%S %z %Y",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
    ]
    for fmt in formats:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def compact(text: object, width: int = 900) -> str:
    value = "" if text is None else str(text)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= width:
        return value
    return value[: width - 3].rstrip() + "..."


def first_value(item: dict[str, Any], names: Iterable[str]) -> object:
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return ""
