"""History entry parsing, ranking display, and change detection."""

from __future__ import annotations

import re

from sfctl.formatting import rank_color, sanitize


def to_label(item_id: str) -> str:
    if not item_id:
        return ""
    cleaned = re.sub(r"^model[_ ]", "", item_id, flags=re.IGNORECASE).strip()
    return cleaned.upper() if len(cleaned) <= 2 else cleaned.title()


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
        f"[{rank_color(i, len(labels))}]{sanitize(label)}[/]" for i, label in enumerate(labels)
    ]
    return " > ".join(parts)


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
        header += f"  |  {sanitize(entry.get('email', 'unknown'))}"
    lines = [header]
    if confidence:
        lines.append(f"[dim]Confidence:[/dim] {sanitize(confidence)}")

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
    for key in ("preference_ranking", "response_quality_ranking", "code_quality_ranking"):
        if _ranking_label(prev, key) != _ranking_label(curr, key):
            return True
    if (prev.get("confidence") or {}).get("value", "") != (curr.get("confidence") or {}).get(
        "value", ""
    ):
        return True
    return _justification_value(prev).strip() != _justification_value(curr).strip()
