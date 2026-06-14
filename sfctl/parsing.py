"""Data parsing, text helpers, and ranking utilities."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime

from sfctl.constants import EM_DASH
from sfctl.models import FileDiff, ModelData, ParsedContent, ProposalData


def format_timestamp(ts: int | float | str) -> str:
    """Convert a millisecond Unix timestamp to local human-readable time."""
    try:
        ms = int(ts)
        dt = datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(ts)


def _sanitize(text: str, max_len: int = 200) -> str:
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
        result: list = json.loads(v)
        return result
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


def group_events(events: list) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if isinstance(e, dict):
            grouped[clean_event_name(str(e.get("name", "")))].append(e)
    return dict(grouped)


def format_event_line(ev: dict) -> str:
    title = ev.get("title")
    label = _sanitize(str(title)) if title else _sanitize(clean_event_name(str(ev.get("name", ""))))
    parts = [f"[bold]{label}[/]"]
    exit_code = ev.get("exit_code")
    if exit_code and exit_code != "no_error":
        parts.append(f"[bold red]{_sanitize(str(exit_code), 50)}[/]")
    wall_time = ev.get("wall_time")
    if wall_time:
        parts.append(f"[dim]{_format_duration(wall_time)}[/]")
    return "  ".join(parts)


def _ranking_label(entry: dict, key: str) -> str:
    """Extract a ranking as 'A > B > C' from a history entry."""
    ranking = entry.get(key) or {}
    value = ranking.get("value") or []
    labels = [
        to_label(item.get("id", "")) for item in value if isinstance(item, dict) and item.get("id")
    ]
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
        rl = get_full_ranking(entry, key)
        if rl:
            lines.append(f"[dim]{label}:[/dim] {rl}")

    return "\n".join(lines)


def history_justification(entry: dict) -> str:
    """Extract the justification text from a history entry."""
    return _justification_value(entry).strip()


def history_ranking_changes(prev: dict, curr: dict) -> list[str]:
    """Return Rich-markup lines showing old and new rankings with rank colors."""
    lines: list[str] = []

    for key, label in [
        ("preference_ranking", "Preference"),
        ("response_quality_ranking", "Response Quality"),
        ("code_quality_ranking", "Code Quality"),
    ]:
        old_r = get_full_ranking(prev, key)
        new_r = get_full_ranking(curr, key)
        old_plain = _ranking_label(prev, key)
        new_plain = _ranking_label(curr, key)
        if old_plain != new_plain and (old_plain or new_plain):
            old_display = old_r or "[dim](none)[/]"
            new_display = new_r or "[dim](none)[/]"
            lines.append(f"[bold]{label}:[/]  {old_display}  [dim]\u2192[/]  {new_display}")

    old_conf = (prev.get("confidence") or {}).get("value", "")
    new_conf = (curr.get("confidence") or {}).get("value", "")
    if old_conf != new_conf:
        lines.append(
            f"[bold]Confidence:[/]  {old_conf or '[dim](none)[/]'}  [dim]\u2192[/]  {new_conf or '[dim](none)[/]'}"
        )

    return lines


def _justification_value(entry: dict) -> str:
    """Extract the justification string from a history entry."""
    val = (entry.get("justification") or {}).get("value", "")
    return val if isinstance(val, str) else ""


def history_justification_texts(prev: dict, curr: dict) -> tuple[str, str] | None:
    """Return (old, new) justification texts if they differ, else None."""
    old_just = _justification_value(prev)
    new_just = _justification_value(curr)
    if old_just.strip() == new_just.strip():
        return None
    return (old_just, new_just)


def feedback_for_entry(history: list, index: int) -> list[dict]:
    """Return feedback entries that are new in history[index] vs history[index-1].

    Feedback accumulates across history entries, so each entry contains all
    previous feedback plus any new ones. This returns only the new ones.
    """
    curr_fb: list[dict] = (history[index].get("feedback") or {}).get("entries", [])
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
    return _justification_value(prev).strip() != _justification_value(curr).strip()


def _sf_value(field: dict | None) -> str:
    """Unwrap a Starfleet rich field: ``{"_sf_rich": true, "value": "..."}``."""
    if not field or not isinstance(field, dict):
        return ""
    val = field.get("value", "")
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val) if val else ""


def _extract_rubrics(rubrics_field: dict | None) -> list[str]:
    """Extract rubric text values from the nested rubrics structure."""
    if not rubrics_field:
        return []
    items = rubrics_field.get("items", [])
    result: list[str] = []
    for item in items:
        nested = item.get("nestedAnnotations", {})
        rubric = nested.get("rubric", {})
        text = _sf_value(rubric)
        if text:
            result.append(text)
    return result


def _format_duration(ms: int | float) -> str:
    """Format a duration in milliseconds for display."""
    if ms >= 60_000:
        return f"{ms / 60_000:.1f}m"
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _variant_to_snake(variant: str) -> str:
    """Convert a PascalCase or Title Case string to ``snake_case``."""
    result = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", variant).lower()
    return re.sub(r"\s+", "_", result)


def _tool_name_from_input(raw_input: str | dict) -> str:
    """Extract a groupable tool name from a tool_call's rawInput.

    The ``variant`` field (e.g. ``Grep``, ``ReadFile``, ``Bash``) is
    converted to snake_case to match code-review event naming.
    Falls back to the title if rawInput has no variant.
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


def _parse_proposal_trace(
    trace: dict | None,
) -> tuple[str, list[dict], list[dict], int | None]:
    """Parse a proposal trace into (summary, tool_events, messages, elapsed_ms).

    The real format is ``{"trace": [list of items]}`` where each item has a
    ``role`` field (``user``, ``assistant``, ``assistant_thinking``, ``tool_call``).
    Tool-call items are normalized to match the code-review event shape so
    the existing rendering pipeline works unchanged.
    """
    if not trace:
        return "", [], [], None

    items = trace.get("trace")
    if isinstance(items, str):
        return items, _parse_json_field(trace.get("toolEvents")), _parse_json_field(trace.get("messages")), None
    if not isinstance(items, list):
        return "", [], [], None

    tool_events: list[dict] = []
    messages: list[dict] = []
    summary = ""
    pending_ev: dict | None = None
    first_ts: int | None = None
    last_ts: int | None = None

    for item in items:
        if not isinstance(item, dict):
            continue
        ts = item.get("timestamp")
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        if pending_ev and ts and pending_ev["timestamp"] and ts > pending_ev["timestamp"]:
            pending_ev["wall_time"] = ts - pending_ev["timestamp"]
            pending_ev = None
        role = item.get("role", "")
        if role == "tool_call":
            title = item.get("title", "")
            raw_input = item.get("rawInput", "")
            ev = {
                "name": _tool_name_from_input(raw_input) or _variant_to_snake(title),
                "title": title,
                "input": raw_input,
                "output": item.get("rawOutput", ""),
                "wall_time": None,
                "exit_code": "no_error" if item.get("status") == "completed" else item.get("status", ""),
                "timestamp": ts,
            }
            tool_events.append(ev)
            pending_ev = ev
        elif role == "assistant_thinking":
            content = item.get("content", "")
            if isinstance(content, str) and content:
                ev = {
                    "name": "thinking",
                    "output": content,
                    "wall_time": None,
                    "exit_code": "no_error",
                    "timestamp": ts,
                }
                tool_events.append(ev)
                pending_ev = ev
        elif role in ("assistant", "user"):
            content = item.get("content", "")
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            messages.append({"role": role, "content": content})

    for msg in reversed(messages):
        if msg["role"] == "assistant" and len(msg.get("content", "")) > 200:
            summary = msg["content"]
            break

    elapsed_ms = (last_ts - first_ts) if first_ts and last_ts and last_ts > first_ts else None
    return summary, tool_events, messages, elapsed_ms


def parse_proposal(history: list[dict], trace: dict | None = None) -> ProposalData:
    """Parse a ProposalData from the latest history entry.

    *trace* is the optional fetched trace JSON.  Handles both the real
    proposal format (``{"trace": [list of message dicts]}``) and the legacy
    code-review format (``{"trace": "str", "toolEvents": "json", ...}``).
    """
    if not history:
        return ProposalData()

    entry = history[-1]
    cq = entry.get("coding_question", {})

    # Extract repo URL from sessions
    sessions = cq.get("sessions", [])
    repo_url = sessions[0].get("githubLink", "") if sessions else ""

    # Get the rollout data (try rollouts.A first, then rolloutA, then rollout)
    rollout = cq.get("rollouts", {}).get("A") or cq.get("rolloutA") or cq.get("rollout") or {}

    # Prompt from finalFeedback
    final_fb = rollout.get("finalFeedback", [])
    prompt = ""
    for fb in final_fb:
        if fb.get("questionId") == "prompt":
            prompt = fb.get("value", "")
            break
    if not prompt:
        # Fallback to first turn's prompt
        turns = rollout.get("turns", [])
        if turns:
            content = turns[0].get("prompt", {}).get("content", [])
            if content:
                prompt = content[0].get("text", "")

    # Code patch from first turn
    turns = rollout.get("turns", [])
    code_patch = turns[0].get("codePatch", "") if turns else ""

    # Bash history -- turn bashHistory has user setup commands run before the model
    setup_commands = turns[0].get("bashHistory", []) if turns else []
    bash_history = rollout.get("finalBashHistory") or []
    if not bash_history and turns:
        bash_history = turns[0].get("bashHistory", [])

    # Issues + comments
    issues_field = entry.get("opus_issues_partial") or entry.get("opus_issues_no") or {}
    issues = _sf_value(issues_field)
    issue_comments = issues_field.get("comments", [])

    # Model / trace metadata
    trace_ref = rollout.get("traceRef", "")
    session = rollout.get("finalSessionSummary") or (
        turns[0].get("sessionSummary") if turns else {}
    ) or {}
    model_id = session.get("current_model_id", "")

    trace_summary, tool_events, messages, trace_elapsed_ms = _parse_proposal_trace(trace)

    return ProposalData(
        repo_url=repo_url,
        repo_description=_sf_value(entry.get("repo_description")),
        prompt=prompt,
        difficulty=_sf_value(entry.get("difficulty_explanation")),
        familiarity=_sf_value(entry.get("familiarity_explanation")),
        rubrics=_extract_rubrics(entry.get("rubrics")),
        duration=_sf_value(entry.get("opus_duration")),
        solved=_sf_value(entry.get("opus_solved")),
        issues=issues,
        issue_comments=issue_comments,
        domain=_sf_value(entry.get("domain")),
        code_patch=code_patch,
        bash_history=bash_history,
        setup_commands=setup_commands,
        file_diffs=extract_file_diffs(code_patch),
        model_id=model_id,
        trace_ref=trace_ref,
        trace_summary=trace_summary,
        trace_elapsed_ms=trace_elapsed_ms,
        tool_events=tool_events,
        messages=messages,
    )


def proposal_rubric_changes(prev: list[str], curr: list[str]) -> list[str]:
    """Return Rich-markup lines showing rubric additions/removals between entries."""
    lines: list[str] = []
    prev_set = set(prev)
    curr_set = set(curr)
    for r in curr:
        if r not in prev_set:
            lines.append(f"[green]+[/] {_sanitize(r)}")
    for r in prev:
        if r not in curr_set:
            lines.append(f"[red]-[/] {_sanitize(r)}")
    return lines


# Keys from the raw history entry that appear in the "Current" tab.
_PROPOSAL_SF_FIELDS: list[tuple[str, str]] = [
    ("repo_description", "Repo Description"),
    ("domain", "Domain"),
    ("opus_duration", "Duration"),
    ("opus_solved", "Solved"),
    ("familiarity_explanation", "Understanding"),
    ("difficulty_explanation", "Difficulty"),
]


def _proposal_issues_value(entry: dict) -> str:
    """Extract the issues text from a history entry."""
    field = entry.get("opus_issues_partial") or entry.get("opus_issues_no") or {}
    return _sf_value(field)


def _proposal_repo_url(entry: dict) -> str:
    """Extract the repo URL from a history entry."""
    cq = entry.get("coding_question", {})
    sessions = cq.get("sessions", [])
    return sessions[0].get("githubLink", "") if sessions else ""


def has_proposal_changes(prev: dict, curr: dict) -> bool:
    """Check if any Current-tab field changed between two proposal history entries."""
    if _extract_rubrics(prev.get("rubrics")) != _extract_rubrics(curr.get("rubrics")):
        return True
    for key, _ in _PROPOSAL_SF_FIELDS:
        if _sf_value(prev.get(key)) != _sf_value(curr.get(key)):
            return True
    if _proposal_issues_value(prev) != _proposal_issues_value(curr):
        return True
    return _proposal_repo_url(prev) != _proposal_repo_url(curr)


def proposal_all_changes(prev: dict, curr: dict) -> list[str]:
    """Return Rich-markup lines for all changed fields between two proposal entries."""
    lines: list[str] = []
    prev_url = _proposal_repo_url(prev)
    curr_url = _proposal_repo_url(curr)
    if prev_url != curr_url:
        if prev_url:
            lines.append(f"[bold]Repo URL:[/bold] [red]{_sanitize(prev_url)}[/red] → [green]{_sanitize(curr_url)}[/green]")
        else:
            lines.append(f"[bold]Repo URL:[/bold] [green]{_sanitize(curr_url)}[/green]")
    for key, label in _PROPOSAL_SF_FIELDS:
        old = _sf_value(prev.get(key))
        new = _sf_value(curr.get(key))
        if old != new:
            old_s = _sanitize(old, 80) or "(empty)"
            new_s = _sanitize(new, 80) or "(empty)"
            lines.append(f"[bold]{label}:[/bold] [red]{old_s}[/red] → [green]{new_s}[/green]")
    old_issues = _proposal_issues_value(prev)
    new_issues = _proposal_issues_value(curr)
    if old_issues != new_issues:
        if old_issues and new_issues:
            lines.append("[bold]Issues:[/bold] changed")
        elif new_issues:
            lines.append("[bold]Issues:[/bold] [green]added[/green]")
        else:
            lines.append("[bold]Issues:[/bold] [red]removed[/red]")
    rubric_lines = proposal_rubric_changes(
        _extract_rubrics(prev.get("rubrics")),
        _extract_rubrics(curr.get("rubrics")),
    )
    if rubric_lines:
        lines.append("[bold]Rubrics:[/bold]")
        lines.extend(rubric_lines)
    return lines
