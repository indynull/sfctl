"""Proposal task parsing and change detection."""

from __future__ import annotations

import re
from datetime import datetime

from sfctl.diff import extract_file_diffs, parse_messages_trace
from sfctl.formatting import format_duration, sanitize
from sfctl.models import ProposalData, TraceEvent


def sf_value(field: dict | None) -> str:
    """Unwrap a Starfleet rich field: ``{"_sf_rich": true, "value": "..."}``."""
    if not field or not isinstance(field, dict):
        return ""
    val = field.get("value", "")
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val) if val else ""


def extract_rubrics(rubrics_field: dict | None) -> list[str]:
    """Extract rubric text values from the nested rubrics structure."""
    if not rubrics_field:
        return []
    items = rubrics_field.get("items", [])
    result: list[str] = []
    for item in items:
        nested = item.get("nestedAnnotations", {})
        rubric = nested.get("rubric", {})
        text = sf_value(rubric)
        if text:
            result.append(text)
    return result


def _parse_proposal_trace(
    trace: dict | None,
) -> tuple[str, list[TraceEvent], list[dict], int | None]:
    """Parse a proposal trace (always a list of message dicts)."""
    if not trace:
        return "", [], [], None
    items = trace.get("trace")
    if isinstance(items, list):
        return parse_messages_trace(items)
    return "", [], [], None


def parse_proposal(history: list[dict], trace: dict | None = None) -> ProposalData:
    """Parse a ProposalData from the latest history entry.

    *trace* is the optional fetched trace JSON (``{"trace": [list of message dicts]}``).
    """
    if not history:
        return ProposalData()

    entry = history[-1]
    cq = entry.get("coding_question", {})

    sessions = cq.get("sessions", [])
    repo_url = sessions[-1].get("githubLink", "") if sessions else ""

    rollout = cq.get("rollouts", {}).get("A", {})

    final_fb = rollout.get("finalFeedback", [])
    prompt = ""
    for fb in final_fb:
        if fb.get("questionId") == "prompt":
            prompt = fb.get("value", "")
            break
    if not prompt:
        turns = rollout.get("turns", [])
        if turns:
            content = turns[0].get("prompt", {}).get("content", [])
            if content:
                prompt = content[0].get("text", "")

    turns = rollout.get("turns", [])
    code_patch = turns[0].get("codePatch", "") if turns else ""

    setup_commands = turns[0].get("bashHistory", []) if turns else []
    bash_history = rollout.get("finalBashHistory") or []
    if not bash_history and turns:
        bash_history = turns[0].get("bashHistory", [])

    issues_field = entry.get("opus_issues_partial") or entry.get("opus_issues_no") or {}
    issues = sf_value(issues_field)
    issue_comments = issues_field.get("comments", [])

    trace_ref = rollout.get("traceRef", "")
    session = rollout.get("finalSessionSummary") or (
        turns[0].get("sessionSummary") if turns else {}
    ) or {}
    model_id = session.get("current_model_id", "")

    trace_summary, tool_events, messages, _ = _parse_proposal_trace(trace)
    trace_elapsed_ms = _proposal_run_elapsed_ms(entry)

    return ProposalData(
        repo_url=repo_url,
        repo_description=sf_value(entry.get("repo_description")),
        prompt=prompt,
        difficulty=sf_value(entry.get("difficulty_explanation")),
        familiarity=sf_value(entry.get("familiarity_explanation")),
        rubrics=extract_rubrics(entry.get("rubrics")),
        duration=sf_value(entry.get("opus_duration")),
        solved=sf_value(entry.get("opus_solved")),
        issues=issues,
        issue_comments=issue_comments,
        domain=sf_value(entry.get("domain")),
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
            lines.append(f"[green]+[/] {sanitize(r)}")
    for r in prev:
        if r not in curr_set:
            lines.append(f"[red]-[/] {sanitize(r)}")
    return lines


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
    return sf_value(field)


def _proposal_repo_url(entry: dict) -> str:
    """Extract the repo URL from a history entry."""
    cq = entry.get("coding_question", {})
    sessions = cq.get("sessions", [])
    return sessions[0].get("githubLink", "") if sessions else ""


def _proposal_rollout(entry: dict) -> dict:
    """Extract the rollout dict from a history entry."""
    return entry.get("coding_question", {}).get("rollouts", {}).get("A", {})


def _proposal_trace_ref(entry: dict) -> str:
    """Extract the traceRef from a history entry."""
    return _proposal_rollout(entry).get("traceRef", "")


def _proposal_prompt(entry: dict) -> str:
    """Extract the prompt text from a history entry."""
    rollout = _proposal_rollout(entry)
    for fb in rollout.get("finalFeedback", []):
        if fb.get("questionId") == "prompt":
            return fb.get("value", "")
    turns = rollout.get("turns", [])
    if turns:
        content = turns[0].get("prompt", {}).get("content", [])
        if content:
            return content[0].get("text", "")
    return ""


def _proposal_code_patch(entry: dict) -> str:
    """Extract the codePatch from a history entry."""
    turns = _proposal_rollout(entry).get("turns", [])
    return turns[0].get("codePatch", "") if turns else ""


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, truncating nanoseconds to microseconds."""
    if not ts:
        return None
    try:
        if len(ts) > 27 and ts.endswith("Z"):
            ts = ts[:26] + "Z"
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _proposal_run_elapsed_ms(entry: dict) -> int | None:
    """Compute model-active duration in ms.

    Sums ``postPromptSystemMetrics.capturedAt - prePromptSystemMetrics.capturedAt``
    across all turns to exclude user idle time between turns.  Falls back to
    ``finalSessionSummary`` wall-clock if per-turn metrics are missing.
    """
    rollout = _proposal_rollout(entry)
    turns = rollout.get("turns", [])
    active_ms = 0
    any_turn = False
    for turn in turns:
        pre = _parse_iso((turn.get("prePromptSystemMetrics") or {}).get("capturedAt", ""))
        post = _parse_iso((turn.get("postPromptSystemMetrics") or {}).get("capturedAt", ""))
        if pre and post and post > pre:
            active_ms += int((post - pre).total_seconds() * 1000)
            any_turn = True
    if any_turn:
        return active_ms

    session = rollout.get("finalSessionSummary") or {}
    created = _parse_iso(session.get("created_at", ""))
    updated = _parse_iso(session.get("last_active_at") or session.get("updated_at", ""))
    if created and updated and updated > created:
        return int((updated - created).total_seconds() * 1000)
    return None


def solved_markup(solved: str) -> str:
    """Return Rich markup for a solved status value with appropriate color."""
    color = {"full": "green", "partial": "yellow", "no": "red"}.get(solved, "white")
    return f"[{color}]{solved}[/{color}]"


def format_proposal_meta(
    entry: dict,
    elapsed_ms: int | None = None,
    model_id: str = "",
) -> str:
    """Format the meta-bar for a proposal entry (domain, duration, solved, model)."""
    parts: list[str] = []
    domain = sf_value(entry.get("domain"))
    if domain:
        parts.append(f"[bold]Domain:[/bold] {domain}")
    duration = sf_value(entry.get("opus_duration"))
    ms = elapsed_ms if elapsed_ms is not None else _proposal_run_elapsed_ms(entry)
    if duration:
        dur_str = duration
        if ms:
            dur_str += f" (actual: {format_duration(ms)})"
        parts.append(f"[bold]Duration:[/bold] {dur_str}")
    elif ms:
        parts.append(f"[bold]Duration:[/bold] {format_duration(ms)}")
    solved = sf_value(entry.get("opus_solved"))
    if solved:
        parts.append(f"[bold]Solved:[/bold] {solved_markup(solved)}")
    if model_id:
        parts.append(f"[bold]Model:[/bold] [dim]{model_id}[/dim]")
    return "  |  ".join(parts) if parts else ""


def proposal_field_summary(entry: dict) -> list[str]:
    """Return Rich-markup lines summarising all proposal fields for a history entry."""
    lines: list[str] = []
    url = _proposal_repo_url(entry)
    if url:
        lines.append(f"[bold]Repo URL:[/bold] {sanitize(url)}")
    for key, label in _PROPOSAL_SF_FIELDS:
        val = sf_value(entry.get(key))
        if val:
            lines.append(f"[bold]{label}:[/bold] {sanitize(val, 80)}")
    issues = _proposal_issues_value(entry)
    if issues:
        lines.append(f"[bold]Issues:[/bold] {sanitize(issues, 80)}")
    rubrics = extract_rubrics(entry.get("rubrics"))
    if rubrics:
        lines.append(f"[bold]Rubrics ({len(rubrics)}):[/bold]")
        for i, r in enumerate(rubrics, 1):
            lines.append(f"  {i}. {sanitize(r)}")
    return lines


def has_proposal_changes(prev: dict, curr: dict) -> bool:
    """Check if any Current-tab field changed between two proposal history entries."""
    if _proposal_trace_ref(prev) != _proposal_trace_ref(curr):
        return True
    if extract_rubrics(prev.get("rubrics")) != extract_rubrics(curr.get("rubrics")):
        return True
    for key, _ in _PROPOSAL_SF_FIELDS:
        if sf_value(prev.get(key)) != sf_value(curr.get(key)):
            return True
    if _proposal_issues_value(prev) != _proposal_issues_value(curr):
        return True
    if _proposal_repo_url(prev) != _proposal_repo_url(curr):
        return True
    if _proposal_prompt(prev) != _proposal_prompt(curr):
        return True
    return _proposal_code_patch(prev) != _proposal_code_patch(curr)


def proposal_all_changes(
    prev: dict, curr: dict,
) -> list[tuple[str, str | None, str | None]]:
    """Return ordered change items between two proposal history entries.

    Each item is ``(label, old_text, new_text)`` where:
    - Both texts present  → render as a redline diff
    - Only new_text       → render new text in green
    - Only old_text       → render old text in red
    - Both ``None``       → label is a pre-formatted Rich markup line, render directly
    """
    items: list[tuple[str, str | None, str | None]] = []

    def _markup(line: str) -> None:
        items.append((line, None, None))

    def _sf_change(label: str, old: str, new: str) -> None:
        old_s = sanitize(old, 80) or "(empty)"
        new_s = sanitize(new, 80) or "(empty)"
        _markup(f"[bold]{label}:[/bold] [red]{old_s}[/red] → [green]{new_s}[/green]")

    def _text_diff(label: str, old: str, new: str) -> None:
        items.append((label, old, new))

    old_prompt = _proposal_prompt(prev)
    new_prompt = _proposal_prompt(curr)
    if old_prompt != new_prompt:
        _text_diff("Prompt", old_prompt, new_prompt)

    for key, label in _PROPOSAL_SF_FIELDS:
        if key in ("familiarity_explanation", "difficulty_explanation"):
            old = sf_value(prev.get(key))
            new = sf_value(curr.get(key))
            if old != new:
                _text_diff(label, old, new)

    if _proposal_trace_ref(prev) != _proposal_trace_ref(curr):
        old_ms = _proposal_run_elapsed_ms(prev)
        new_ms = _proposal_run_elapsed_ms(curr)
        old_dur = format_duration(old_ms) if old_ms else "?"
        new_dur = format_duration(new_ms) if new_ms else "?"
        _markup(f"[bold]Model run:[/bold] {old_dur} → {new_dur}")

    for key, label in _PROPOSAL_SF_FIELDS:
        if key in ("familiarity_explanation", "difficulty_explanation"):
            continue
        old = sf_value(prev.get(key))
        new = sf_value(curr.get(key))
        if old != new:
            _sf_change(label, old, new)

    prev_url = _proposal_repo_url(prev)
    curr_url = _proposal_repo_url(curr)
    if prev_url != curr_url:
        if prev_url:
            _markup(f"[bold]Repo URL:[/bold] [red]{sanitize(prev_url)}[/red] → [green]{sanitize(curr_url)}[/green]")
        else:
            _markup(f"[bold]Repo URL:[/bold] [green]{sanitize(curr_url)}[/green]")

    old_patch = _proposal_code_patch(prev)
    new_patch = _proposal_code_patch(curr)
    if old_patch != new_patch:
        old_files = set(_patch_filenames(old_patch))
        new_files = set(_patch_filenames(new_patch))
        added = len(new_files - old_files)
        removed = len(old_files - new_files)
        common = len(old_files & new_files)
        parts: list[str] = []
        if added:
            parts.append(f"[green]+{added}[/green]")
        if removed:
            parts.append(f"[red]-{removed}[/red]")
        if common:
            parts.append(f"{common} common")
        _markup(f"[bold]Code patch:[/bold] {', '.join(parts)} files")

    old_issues = _proposal_issues_value(prev)
    new_issues = _proposal_issues_value(curr)
    if old_issues != new_issues:
        _text_diff("Issues", old_issues, new_issues)

    rubric_lines = proposal_rubric_changes(
        extract_rubrics(prev.get("rubrics")),
        extract_rubrics(curr.get("rubrics")),
    )
    if rubric_lines:
        _markup("[bold]Rubrics:[/bold]")
        for line in rubric_lines:
            _markup(line)

    return items


def _patch_filenames(patch: str) -> list[str]:
    """Extract ordered filenames from a unified diff."""
    return re.findall(r"diff --git a/\S+ b/(\S+)", patch)




