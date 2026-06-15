"""Diff parsing, file extraction, and language detection."""

from __future__ import annotations

import json
import re

from sfctl.models import FileDiff, ModelData, ParsedContent, TraceEvent

_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".xml": "xml",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
}


def language_from_filename(filename: str) -> str | None:
    """Map a filename to a TextArea language identifier, or None if unknown."""
    dot = filename.rfind(".")
    if dot < 0:
        return None
    return _EXT_TO_LANGUAGE.get(filename[dot:].lower())


class DiffLine:
    """A single parsed line from a unified diff."""

    __slots__ = ("kind", "source", "text")

    def __init__(self, kind: str, text: str, source: str) -> None:
        self.kind = kind
        self.text = text
        self.source = source


def parse_diff_lines(diff_text: str) -> list[DiffLine]:
    """Parse unified diff text into structured DiffLines.

    Each line gets a kind: 'add', 'del', 'ctx', 'hunk', or 'meta'.
    ``text`` is the clean source (prefix stripped).
    ``source`` is the original diff line.
    """
    result: list[DiffLine] = []
    for line in diff_text.split("\n"):
        if line.startswith("@@"):
            result.append(DiffLine("hunk", line, line))
        elif line.startswith("+"):
            result.append(DiffLine("add", line[1:], line))
        elif line.startswith("-"):
            result.append(DiffLine("del", line[1:], line))
        else:
            result.append(DiffLine("ctx", line[1:] if line.startswith(" ") else line, line))
    return result


_TRIPLE_QUOTES = ('"""', "'''")


def build_highlighted_sides(
    diff_lines: list[DiffLine],
) -> tuple[list[str], list[int], list[str], list[int]]:
    """Build old-side and new-side text for tree-sitter highlighting.

    Splits a unified diff into two coherent views (old = ctx+del,
    new = ctx+add) so that tree-sitter can parse each side without
    seeing interleaved added/deleted code.  Hunk headers are replaced
    with blank lines and orphaned triple-quote closers at hunk
    boundaries get a synthetic opener injected so that tree-sitter
    doesn't treat them as string-start tokens.

    Returns (new_lines, new_map, old_lines, old_map) where each map
    entry gives the unified diff-line index for that result line,
    or -1 for synthetic balancer lines.
    """
    new_lines, new_map = _build_side(diff_lines, frozenset({"ctx", "add", "hunk"}))
    old_lines, old_map = _build_side(diff_lines, frozenset({"ctx", "del", "hunk"}))
    return new_lines, new_map, old_lines, old_map


def _count_triple_quotes(text: str) -> int:
    return sum(text.count(tq) for tq in _TRIPLE_QUOTES)


def _build_side(
    diff_lines: list[DiffLine],
    include_kinds: frozenset[str],
) -> tuple[list[str], list[int]]:
    hunk_starts = [i for i, dl in enumerate(diff_lines) if dl.kind == "hunk"]

    # Detect orphaned triple-quote closers in leading context of each hunk.
    # A hunk's leading context is the run of ``ctx`` lines immediately after
    # the ``@@`` header.  If the first triple-quote in that run has no prior
    # opener within the hunk, it is closing a construct that started above
    # the visible diff — we mark the hunk so a synthetic opener is injected.
    orphaned: set[int] = set()
    for h_pos, h_start in enumerate(hunk_starts):
        h_end = hunk_starts[h_pos + 1] if h_pos + 1 < len(hunk_starts) else len(diff_lines)
        balance = 0
        for j in range(h_start + 1, h_end):
            dl = diff_lines[j]
            if dl.kind != "ctx" or dl.kind not in include_kinds:
                break
            count = _count_triple_quotes(dl.text)
            if count > 0 and balance == 0:
                orphaned.add(h_start)
            balance += count

    result: list[str] = []
    rmap: list[int] = []

    for i, dl in enumerate(diff_lines):
        if dl.kind not in include_kinds:
            continue
        if dl.kind == "hunk":
            result.append("")
            rmap.append(i)
            if i in orphaned:
                result.append('"""')
                rmap.append(-1)
        else:
            result.append(dl.text)
            rmap.append(i)

    # If the overall triple-quote count is odd (e.g. a trailing context line
    # opens a string whose close is below the diff), append a balancer.
    if _count_triple_quotes("\n".join(result)) % 2 == 1:
        result.append('"""')
        rmap.append(-1)

    return result, rmap


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


_PREAMBLE_PREFIXES = (
    "diff --git ",
    "diff ",
    "index ",
    "old mode ",
    "new mode ",
    "new file mode ",
    "deleted file mode ",
    "similarity index ",
    "rename from ",
    "rename to ",
    "--- ",
    "+++ ",
)


def strip_diff_preamble(diff_text: str) -> str:
    """Remove git diff preamble lines, keeping only hunk headers and content."""
    lines = diff_text.split("\n")
    out: list[str] = []
    for line in lines:
        if any(line.startswith(p) for p in _PREAMBLE_PREFIXES):
            continue
        out.append(line)
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


def parse_json_field(v: str | None) -> list:
    """Parse an embedded JSON string into a list."""
    if not v:
        return []
    try:
        result: list = json.loads(v)
        return result
    except (json.JSONDecodeError, ValueError):
        return []


def _variant_to_snake(variant: str) -> str:
    """Convert a PascalCase or Title Case string to ``snake_case``."""
    result = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", variant).lower()
    return re.sub(r"\s+", "_", result)


def tool_name_from_input(raw_input: str | dict) -> str:
    """Extract a groupable tool name from a tool_call's rawInput.

    The ``variant`` field (e.g. ``Grep``, ``ReadFile``, ``Bash``) is
    converted to snake_case to match code-review event naming.
    """
    if isinstance(raw_input, str):
        try:
            raw_input = json.loads(raw_input)
        except (json.JSONDecodeError, ValueError):
            return ""
    if isinstance(raw_input, dict):
        variant = raw_input.get("variant", "")
        if variant:
            return _variant_to_snake(variant)
    return ""


def parse_messages_trace(
    items: list[dict],
) -> tuple[str, list[TraceEvent], list[dict], int | None]:
    """Parse a messages-format trace into (summary, tool_events, messages, elapsed_ms).

    Both model-ranking and project-proposal traces share this format:
    a list of dicts with ``role`` in (``user``, ``assistant``,
    ``assistant_thinking``, ``tool_call``).
    """
    tool_events: list[TraceEvent] = []
    messages: list[dict] = []
    summary = ""
    pending_ev: TraceEvent | None = None
    first_ts: int | None = None
    last_ts: int | None = None
    prev_ts: int | None = None
    user_wait_ms = 0

    for item in items:
        ts = item.get("timestamp")
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        role = item.get("role", "")
        if role == "user":
            if pending_ev:
                pending_ev = None
            if ts and prev_ts and ts > prev_ts:
                user_wait_ms += ts - prev_ts
            content = item.get("content", "")
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            messages.append({"role": role, "content": content})
            prev_ts = ts
            continue
        if pending_ev and ts and pending_ev.timestamp and ts > pending_ev.timestamp:
            pending_ev.wall_time = ts - pending_ev.timestamp
            pending_ev = None
        if role == "tool_call":
            title = item.get("title", "")
            raw_input = item.get("rawInput", "")
            ev = TraceEvent(
                name=tool_name_from_input(raw_input) or _variant_to_snake(title),
                title=title,
                input=raw_input,
                output=item.get("rawOutput", ""),
                wall_time=None,
                exit_code="no_error" if item.get("status") == "completed" else item.get("status", ""),
                timestamp=ts,
            )
            tool_events.append(ev)
            pending_ev = ev
        elif role == "assistant_thinking":
            content = item.get("content", "")
            if isinstance(content, str) and content:
                ev = TraceEvent(
                    name="thinking",
                    output=content,
                    wall_time=None,
                    exit_code="no_error",
                    timestamp=ts,
                )
                tool_events.append(ev)
                pending_ev = ev
        elif role == "assistant":
            content = item.get("content", "")
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            messages.append({"role": role, "content": content})
        prev_ts = ts if ts else prev_ts

    for msg in reversed(messages):
        if msg["role"] == "assistant" and len(msg.get("content", "")) > 200:
            summary = msg["content"]
            break

    total_ms = (last_ts - first_ts) if first_ts and last_ts and last_ts > first_ts else None
    elapsed_ms = (total_ms - user_wait_ms) if total_ms and user_wait_ms else total_ms
    return summary, tool_events, messages, elapsed_ms


def parse_content(blob: dict) -> ParsedContent:
    items = blob.get("content", {}).get("items", [])

    def find(title: str) -> dict | None:
        return next((i for i in items if i.get("title") == title), None)

    repo_item = find("Repository")
    repo_text = repo_item["text"].strip("* ") if repo_item else ""
    prompt_item = find("Current Prompt")
    collection = find("Model Traces")
    model_items = collection.get("items", []) if collection else []

    models: list[ModelData] = []
    for m in model_items:
        trace = m["trace"]
        diff_text = m["diff"]["codeDiff"]
        raw_messages = parse_json_field(trace["messages"])
        summary, tool_events, messages, _ = parse_messages_trace(raw_messages)
        models.append(
            ModelData(
                name=m.get("title", "Unknown"),
                diff=diff_text,
                trace_summary=trace["trace"] or summary,
                messages=messages,
                tool_events=tool_events,
                file_diffs=extract_file_diffs(diff_text),
            )
        )

    return ParsedContent(
        task_id=blob.get("taskId"),
        repository=repo_text,
        current_prompt=prompt_item.get("content", "") if prompt_item else "",
        models=models,
    )
