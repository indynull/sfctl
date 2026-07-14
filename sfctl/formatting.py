"""Text formatting, sanitization, and trace display helpers."""

from __future__ import annotations

import json
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


def format_value(v: object, max_len: int = 120) -> str:
    """Format a single value for inline display, truncating long strings."""
    if v is None:
        return "[dim italic]null[/]"
    if isinstance(v, bool):
        return f"[dim]{v}[/]"
    if isinstance(v, (int, float)):
        return f"[dim]{v}[/]"
    if isinstance(v, str):
        s = sanitize(v, max_len)
        return f"[dim]{s}[/]" if s else '[dim italic]""[/]'
    if isinstance(v, list):
        if not v:
            return "[dim italic](empty list)[/]"
        items = ", ".join(sanitize(str(x), 40) for x in v[:5])
        suffix = f" ... +{len(v) - 5}" if len(v) > 5 else ""
        return f"[dim]({items}{suffix})[/]"
    if isinstance(v, dict):
        if not v:
            return "[dim italic]{...}[/]"
        items = ", ".join(
            f"{sanitize(str(k), 20)}={sanitize(str(val), 30)}" for k, val in list(v.items())[:4]
        )
        suffix = f" ... +{len(v) - 4}" if len(v) > 4 else ""
        return f"[dim]{items}{suffix}[/]"
    return f"[dim]{sanitize(str(v), max_len)}[/]"


def try_parse(v: object) -> object:
    """If v is a JSON string, parse it into a dict/list."""
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return v


_MAX_BLOCK_LINES = 20

_SKIP_INPUT_KEYS = frozenset({"variant"})
_SKIP_OUTPUT_KEYS = frozenset({"type"})


def is_multiline(v: object) -> bool:
    return isinstance(v, str) and "\n" in v


def decode_bytes(v: list[int]) -> str:
    """Decode a list of byte values to a UTF-8 string."""
    try:
        return bytes(v).decode("utf-8", errors="replace")
    except (TypeError, ValueError, OverflowError):
        return str(v)


def format_block(text: str, indent: str = "      ") -> str:
    """Truncate a multi-line string and indent it for display."""
    lines = text.split("\n")
    if len(lines) > _MAX_BLOCK_LINES:
        lines = [*lines[:_MAX_BLOCK_LINES], f"... +{len(lines) - _MAX_BLOCK_LINES} lines"]
    return "\n".join(f"{indent}{line}" for line in lines)


def unwrap_output(d: dict) -> object:
    """Unwrap a wrapped output dict into its human-readable payload.

    Proposal outputs use two patterns:

    1. Single-wrapper: ``{"type": "ReadFile", "FileContent": {"content": "..."}}``
       -- strip *type*, descend into the single remaining dict and pull
       ``content`` / ``*_for_prompt``.
    2. Flat wrapper: ``{"type": "Bash", "output_for_prompt": "exit: 0\\n...", ...}``
       -- prefer the ``*_for_prompt`` key directly.
    """
    remaining = {k: v for k, v in d.items() if k not in _SKIP_OUTPUT_KEYS}

    for k, v in remaining.items():
        if k.endswith("_for_prompt") and isinstance(v, str) and v.strip():
            return v

    if len(remaining) == 1:
        inner = next(iter(remaining.values()))
        if isinstance(inner, dict):
            for ik in ("content", "tool_output_for_prompt", "summary_for_prompt"):
                iv = inner.get(ik)
                if isinstance(iv, str) and iv.strip():
                    return iv
            if len(inner) == 1:
                sole = next(iter(inner.values()))
                if isinstance(sole, str):
                    return sole
        if isinstance(inner, str):
            return inner

    return remaining
