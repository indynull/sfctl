"""Text formatting, sanitization, and trace display helpers."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime

from sfctl.constants import EM_DASH


def format_timestamp(ts: int | float | str) -> str:
    """Convert a millisecond Unix timestamp to local human-readable time."""
    try:
        ms = int(ts)
        dt = datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(ts)


def sanitize(text: str, max_len: int = 200) -> str:
    """Strip newlines, brackets, and truncate for safe use in Rich markup."""
    return (
        text.replace("\n", " ")
        .replace("\r", "")
        .replace("[", "(")
        .replace("]", ")")[:max_len]
        .strip()
    )


def bump_headings(text: str, parent_level: int = 1) -> str:
    """Makes the shallowest heading become exactly parent_level + 1."""
    if not text or text.strip() in ("", EM_DASH):
        return text or EM_DASH
    matches = list(re.finditer(r"^(#{1,6})\s", text, re.MULTILINE))
    if not matches:
        return text
    min_level = min(len(m.group(1)) for m in matches)
    shift = (parent_level + 1) - min_level

    def repl(m):
        return "#" * min(len(m.group(1)) + shift, 6)

    return re.sub(r"^(#{1,6})(?=\s)", repl, text, flags=re.MULTILINE)


def rank_color(position: int, total: int) -> str:
    if total <= 1:
        return "green"
    if position == 0:
        return "green"
    if position == total - 1:
        return "red"
    return "yellow"


def format_duration(ms: int | float) -> str:
    """Format a duration in milliseconds for display."""
    if ms >= 60_000:
        return f"{ms / 60_000:.1f}m"
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def clean_event_name(name: str) -> str:
    return name.replace("__sf", "").replace("tool_event", "").strip("_") or "unknown"


_TRACE_COLORS = [
    "#5f87ff",
    "#d75fd7",
    "#5fd7d7",
    "#d7af5f",
    "#d75f5f",
    "#5fd75f",
    "#5f87af",
    "#875f87",
    "#00af87",
    "#af8700",
]


def trace_type_color(index: int) -> str:
    return _TRACE_COLORS[index % len(_TRACE_COLORS)]


def group_events(events: list) -> dict[str, list]:
    grouped: dict[str, list] = defaultdict(list)
    for e in events:
        grouped[clean_event_name(e.name)].append(e)
    return dict(grouped)


def format_event_line(ev) -> str:
    label = sanitize(clean_event_name(ev.name)) if ev.name else sanitize(ev.title)
    parts = [f"[bold]{label}[/]"]
    if ev.name and ev.title and ev.title != ev.name:
        parts.append(f"[dim]{sanitize(ev.title, 60)}[/]")
    if ev.exit_code and ev.exit_code != "no_error":
        parts.append(f"[bold red]{sanitize(ev.exit_code, 50)}[/]")
    if ev.wall_time:
        parts.append(f"[dim]{format_duration(ev.wall_time)}[/]")
    return "  ".join(parts)
