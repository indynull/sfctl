"""Data parsing, text helpers, and ranking utilities."""

from __future__ import annotations

import difflib
import itertools
import json
import re
from collections import defaultdict

from sfctl.constants import EM_DASH
from sfctl.models import FileDiff, ModelData, ParsedContent


def _sanitize(text: str, max_len: int = 200) -> str:
    """Strip brackets and truncate for safe use in Rich markup."""
    return text.replace("[", "(").replace("]", ")")[:max_len]


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


def to_label(item_id: str) -> str:
    if not item_id:
        return ""
    cleaned = re.sub(r"^model[_ ]", "", item_id, flags=re.IGNORECASE).strip()
    return cleaned.upper() if len(cleaned) <= 2 else cleaned.title()


def rank_color(position: int, total: int) -> str:
    if total <= 1:
        return "green"
    if position == 0:
        return "green"
    if position == total - 1:
        return "red"
    return "yellow"


def get_full_ranking(entry: dict, key: str) -> str:
    """Return ranking as 'A > B > C' with rank colors, or empty string if not available."""
    ranking = entry.get(key)
    if not ranking:
        return ""
    value = ranking.get("value") or []
    labels = [to_label(item.get("id", "")) for item in value if item.get("id")]
    if not labels:
        return ""
    parts = [
        f"[{rank_color(i, len(labels))}]{_sanitize(label)}[/]" for i, label in enumerate(labels)
    ]
    return " > ".join(parts)


def build_diff_line_map(diff_text: str) -> dict[int, int]:
    """Map diff-text line indices to real source line numbers.

    Tracks both old-file and new-file counters from hunk headers
    (@@ -X,Y +A,B @@). Deleted lines show old-file numbers,
    additions and context show new-file numbers.
    Lines before the first hunk (preamble) are excluded.
    """
    lines = diff_text.split("\n")
    old_line = 0
    new_line = 0
    in_hunk = False
    line_map: dict[int, int] = {}

    for i, line in enumerate(lines):
        hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)", line)
        if hunk:
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(2))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("diff ") or line.startswith("---") or line.startswith("+++"):
            in_hunk = False
            continue
        if line.startswith("-"):
            line_map[i] = old_line
            old_line += 1
            continue
        if line.startswith("+"):
            line_map[i] = new_line
            new_line += 1
            continue
        line_map[i] = new_line
        old_line += 1
        new_line += 1

    return line_map


def diff_line_ref(diff_text: str, sel_start: int, sel_end: int) -> str:
    """Map TextArea selection (0-based line indices) to real source line numbers."""
    line_map = build_diff_line_map(diff_text)

    start = line_map.get(sel_start, sel_start + 1)
    end = line_map.get(sel_end, sel_end + 1)
    if start == end:
        return f"L{start}"
    return f"L{min(start, end)}-L{max(start, end)}"


_PREAMBLE_PREFIXES = ("diff --git ", "diff ", "index ", "old mode ", "new mode ",
                      "similarity index ", "rename from ", "rename to ",
                      "--- ", "+++ ")


def strip_diff_preamble(diff_text: str) -> str:
    """Remove git diff preamble lines, keeping only hunk headers and content."""
    lines = diff_text.split("\n")
    out: list[str] = []
    for line in lines:
        if any(line.startswith(p) for p in _PREAMBLE_PREFIXES):
            continue
        out.append(line)
    # Strip leading/trailing blank lines left by removal
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def extract_file_diffs(diff_text: str) -> list[FileDiff]:
    """Split a multi-file unified diff into per-file blocks."""
    if not diff_text or not diff_text.strip():
        return []
    blocks = re.split(r"(?=diff --git)", diff_text.strip())
    files: list[FileDiff] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        fname = "unknown-file"
        for line in lines[:6]:
            if line.startswith("diff --git"):
                parts = line.split()
                if len(parts) >= 4:
                    fname = parts[3].removeprefix("b/").removeprefix("a/")
                elif len(parts) >= 3:
                    fname = parts[2].removeprefix("a/").removeprefix("b/")
                break
            if line.startswith("+++ b/"):
                fname = line[6:].strip()
                break
        files.append(FileDiff(filename=fname, diff=strip_diff_preamble(block)))
    return files


def _parse_json_field(v: str | None) -> list:
    """Parse an embedded JSON string into a list."""
    if not v:
        return []
    try:
        return json.loads(v)
    except (json.JSONDecodeError, ValueError):
        return []


def parse_content(blob: dict) -> ParsedContent:
    items = blob.get("content", {}).get("items", [])

    def find(t: str, title: str) -> dict | None:
        return next((i for i in items if i.get("type") == t and i.get("title") == title), None)

    repo_item = find("text", "Repository")
    repo_text = repo_item["text"].strip("* ") if repo_item else ""
    prompt_item = find("message", "Current Prompt")
    collection = find("collection", "Model Traces")
    model_items = collection.get("items", []) if collection else []

    models: list[ModelData] = []
    for m in model_items:
        trace = m["trace"]
        diff_text = m["diff"]["codeDiff"]
        models.append(
            ModelData(
                name=m.get("title", "Unknown"),
                diff=diff_text,
                trace_summary=trace.get("trace"),
                messages=_parse_json_field(trace.get("messages")),
                tool_events=_parse_json_field(trace.get("toolEvents")),
                file_diffs=extract_file_diffs(diff_text),
            )
        )

    return ParsedContent(
        task_id=blob.get("taskId"),
        repository=repo_text,
        current_prompt=prompt_item.get("content", "") if prompt_item else "",
        models=models,
    )



def clean_event_name(name: str) -> str:
    return name.replace("__sf", "").replace("tool_event", "").strip("_") or "unknown"


_TRACE_COLORS = [
    "bright_blue",
    "bright_magenta",
    "bright_cyan",
    "bright_yellow",
    "bright_red",
    "bright_green",
    "steel_blue",
    "plum4",
    "dark_cyan",
    "dark_goldenrod",
]


def trace_type_color(index: int) -> str:
    return _TRACE_COLORS[index % len(_TRACE_COLORS)]


def group_events(model: ModelData) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for e in model.tool_events:
        grouped[clean_event_name(str(e.get("name", "")))].append(e)
    return dict(grouped)


def format_event_line(ev: dict) -> str:
    name = _sanitize(clean_event_name(str(ev.get("name", ""))))
    parts = [f"[bold]{name}[/]"]
    exit_code = ev.get("exit_code")
    if exit_code and exit_code != "no_error":
        parts.append(f"[bold red]{_sanitize(str(exit_code), 50)}[/]")
    wall_time = ev.get("wall_time")
    if wall_time:
        parts.append(f"[dim]{wall_time}ms[/]")
    return "  ".join(parts)



def _ranking_label(entry: dict, key: str) -> str:
    """Extract a ranking as 'A > B > C' from a history entry."""
    ranking = entry.get(key) or {}
    value = ranking.get("value") or []
    labels = [to_label(item.get("id", "")) for item in value if isinstance(item, dict) and item.get("id")]
    return " > ".join(labels) if labels else ""


def format_history_entry(entry: dict, index: int, show_email: bool = False) -> str:
    """Format a history entry's metadata as Rich markup (no justification)."""
    level = entry.get("reviewLevel", "?")
    confidence = (entry.get("confidence") or {}).get("value", "")

    header = f"[bold]Entry {index}[/bold]  |  Level {level}"
    if show_email:
        header += f"  |  {_sanitize(entry.get('email', 'unknown'))}"
    lines = [header]
    if confidence:
        lines.append(f"[dim]Confidence:[/dim] {_sanitize(confidence)}")

    for key, label in [
        ("preference_ranking", "Preference"),
        ("response_quality_ranking", "Response Quality"),
        ("code_quality_ranking", "Code Quality"),
    ]:
        rl = _ranking_label(entry, key)
        if rl:
            lines.append(f"[dim]{label}:[/dim] {rl}")

    return "\n".join(lines)


def history_justification(entry: dict) -> str:
    """Extract the justification text from a history entry."""
    justification = (entry.get("justification") or {}).get("value", "")
    if isinstance(justification, str) and justification.strip():
        return justification.strip()
    return ""


def history_diff(prev: dict, curr: dict) -> str:
    """Compute unified diff between two consecutive history entries.

    Diffs justification text and shows ranking changes as header lines.
    """
    diff_lines: list[str] = []

    # Ranking changes
    for key, label in [
        ("preference_ranking", "Preference"),
        ("response_quality_ranking", "Response Quality"),
        ("code_quality_ranking", "Code Quality"),
    ]:
        old_r = _ranking_label(prev, key)
        new_r = _ranking_label(curr, key)
        if old_r != new_r and (old_r or new_r):
            diff_lines.append(f"# {label}: {old_r or '(none)'} -> {new_r or '(none)'}")

    # Confidence change
    old_conf = (prev.get("confidence") or {}).get("value", "")
    new_conf = (curr.get("confidence") or {}).get("value", "")
    if old_conf != new_conf:
        diff_lines.append(f"# Confidence: {old_conf or '(none)'} -> {new_conf or '(none)'}")

    # Justification diff
    old_just = (prev.get("justification") or {}).get("value", "")
    new_just = (curr.get("justification") or {}).get("value", "")
    if not isinstance(old_just, str):
        old_just = ""
    if not isinstance(new_just, str):
        new_just = ""

    if old_just != new_just:
        old_lines = old_just.splitlines(keepends=True)
        new_lines = new_just.splitlines(keepends=True)
        udiff = difflib.unified_diff(
            old_lines, new_lines, fromfile="previous", tofile="current", lineterm=""
        )
        diff_lines.extend(udiff)

    return "\n".join(diff_lines) if diff_lines else ""


def dedupe_feedback(history: list, feedback: dict) -> list[dict]:
    all_fb = (feedback.get("entries") or []) + list(
        itertools.chain.from_iterable(h.get("feedback", {}).get("entries", []) for h in history)
    )
    seen: set[str] = set()
    unique: list[dict] = []
    for fb in all_fb:
        ts = str(fb.get("timestamp", ""))
        if ts and ts not in seen:
            seen.add(ts)
            unique.append(fb)
    return unique


def feedback_for_entry(history: list, index: int) -> list[dict]:
    """Return feedback entries that are new in history[index] vs history[index-1].

    Feedback accumulates across history entries, so each entry contains all
    previous feedback plus any new ones. This returns only the new ones.
    """
    curr_fb = (history[index].get("feedback") or {}).get("entries", [])
    if index == 0:
        return curr_fb

    prev_timestamps = {
        str(fb.get("timestamp", ""))
        for fb in (history[index - 1].get("feedback") or {}).get("entries", [])
    }
    return [fb for fb in curr_fb if str(fb.get("timestamp", "")) not in prev_timestamps]


def has_meaningful_changes(prev: dict, curr: dict) -> bool:
    """Check if a history entry has any actual changes from the previous."""
    # Different rankings
    for key in ("preference_ranking", "response_quality_ranking", "code_quality_ranking"):
        if _ranking_label(prev, key) != _ranking_label(curr, key):
            return True
    # Different confidence
    if (prev.get("confidence") or {}).get("value", "") != (curr.get("confidence") or {}).get(
        "value", ""
    ):
        return True
    # Different justification
    old_just = (prev.get("justification") or {}).get("value", "")
    new_just = (curr.get("justification") or {}).get("value", "")
    if not isinstance(old_just, str):
        old_just = ""
    if not isinstance(new_just, str):
        new_just = ""
    return old_just.strip() != new_just.strip()
