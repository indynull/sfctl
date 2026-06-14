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


def dicts_to_trace_events(raw: list) -> list[TraceEvent]:
    """Convert a list of raw dicts into TraceEvent objects."""
    events: list[TraceEvent] = []
    for item in raw:
        if isinstance(item, dict):
            events.append(TraceEvent(
                name=str(item.get("name", "")),
                title=str(item.get("title", "")),
                wall_time=item.get("wall_time"),
                exit_code=str(item.get("exit_code", "no_error")),
                timestamp=item.get("timestamp"),
                input=item.get("input", item.get("args", item.get("arguments", ""))),
                output=item.get("output", item.get("result", item.get("response", ""))),
            ))
    return events


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
                messages=parse_json_field(trace.get("messages")),
                tool_events=dicts_to_trace_events(parse_json_field(trace.get("toolEvents"))),
                file_diffs=extract_file_diffs(diff_text),
            )
        )

    return ParsedContent(
        task_id=blob.get("taskId"),
        repository=repo_text,
        current_prompt=prompt_item.get("content", "") if prompt_item else "",
        models=models,
    )
