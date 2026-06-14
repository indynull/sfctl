"""Proposal task parsing and change detection."""

from __future__ import annotations

import json
import re

from sfctl.diff import extract_file_diffs, parse_json_field
from sfctl.formatting import sanitize
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


def _variant_to_snake(variant: str) -> str:
    """Convert a PascalCase or Title Case string to ``snake_case``."""
    result = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", variant).lower()
    return re.sub(r"\s+", "_", result)


def tool_name_from_input(raw_input: str | dict) -> str:
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
) -> tuple[str, list[TraceEvent], list[dict], int | None]:
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
        from sfctl.diff import dicts_to_trace_events

        return (
            items,
            dicts_to_trace_events(parse_json_field(trace.get("toolEvents"))),
            parse_json_field(trace.get("messages")),
            None,
        )
    if not isinstance(items, list):
        return "", [], [], None

    tool_events: list[TraceEvent] = []
    messages: list[dict] = []
    summary = ""
    pending_ev: TraceEvent | None = None
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
        if pending_ev and ts and pending_ev.timestamp and ts > pending_ev.timestamp:
            pending_ev.wall_time = ts - pending_ev.timestamp
            pending_ev = None
        role = item.get("role", "")
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

    sessions = cq.get("sessions", [])
    repo_url = sessions[0].get("githubLink", "") if sessions else ""

    rollout = cq.get("rollouts", {}).get("A") or cq.get("rolloutA") or cq.get("rollout") or {}

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

    trace_summary, tool_events, messages, trace_elapsed_ms = _parse_proposal_trace(trace)

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


def has_proposal_changes(prev: dict, curr: dict) -> bool:
    """Check if any Current-tab field changed between two proposal history entries."""
    if extract_rubrics(prev.get("rubrics")) != extract_rubrics(curr.get("rubrics")):
        return True
    for key, _ in _PROPOSAL_SF_FIELDS:
        if sf_value(prev.get(key)) != sf_value(curr.get(key)):
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
            lines.append(f"[bold]Repo URL:[/bold] [red]{sanitize(prev_url)}[/red] → [green]{sanitize(curr_url)}[/green]")
        else:
            lines.append(f"[bold]Repo URL:[/bold] [green]{sanitize(curr_url)}[/green]")
    for key, label in _PROPOSAL_SF_FIELDS:
        old = sf_value(prev.get(key))
        new = sf_value(curr.get(key))
        if old != new:
            old_s = sanitize(old, 80) or "(empty)"
            new_s = sanitize(new, 80) or "(empty)"
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
        extract_rubrics(prev.get("rubrics")),
        extract_rubrics(curr.get("rubrics")),
    )
    if rubric_lines:
        lines.append("[bold]Rubrics:[/bold]")
        lines.extend(rubric_lines)
    return lines
