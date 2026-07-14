"""Arena ranking: clarity checklist and multi-field justifications."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sfctl.formatting import sanitize
from sfctl.history import get_full_ranking, history_ranking_changes, to_label

JUSTIFICATION_KEYS: list[tuple[str, str]] = [
    ("prompt_understanding", "Prompt understanding"),
    ("response_justification", "Response justification"),
    ("code_quality_justification", "Code justification"),
    ("overall_justification", "Overall justification"),
]

RANKING_KEYS: list[tuple[str, str]] = [
    ("preference_ranking", "Preference"),
    ("response_quality_ranking", "Response Quality"),
    ("code_quality_ranking", "Code Quality"),
]

_BRACKET_RULE = re.compile(r"\[([^\]]+)\]")


@dataclass(slots=True)
class ArenaMeta:
    """Task-level metadata for arena ranking."""

    label_map: dict[str, str] = field(default_factory=dict)
    model_ids: list[str] = field(default_factory=list)
    batch: str = ""
    dataset: str = ""
    anchor: str = ""
    rule_labels: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ArenaChecklist:
    """Parsed response-clarity checklist for one history entry."""

    col_headers: list[str]
    row_headers: list[str]
    cells: list[list[list[str]]]


def parse_arena_meta(data: dict) -> ArenaMeta:
    """Extract label map, model names, and checklist labels from task data."""
    content = data.get("content", {})
    meta = content.get("metadata") or {}
    label_map = {
        str(k): str(v) for k, v in (meta.get("label_map") or {}).items() if k and v
    }
    model_ids = [str(m) for m in (meta.get("models") or []) if m]
    rule_labels = _rule_labels_from_questions(content.get("questions") or [])
    return ArenaMeta(
        label_map=label_map,
        model_ids=model_ids,
        batch=str(meta.get("batch") or ""),
        dataset=str(meta.get("dataset") or ""),
        anchor=str(meta.get("anchor") or ""),
        rule_labels=rule_labels,
    )


def _rule_title(text: str) -> str:
    """Human-readable rule title from option text like '[O4] No bloated body'."""
    text = (text or "").strip()
    if not text:
        return ""
    m = _BRACKET_RULE.search(text)
    if m:
        rest = text[m.end() :].strip(" \t-:")
        return rest or m.group(1)
    return text


def _rule_labels_from_questions(questions: list) -> dict[str, str]:
    """Map choiceId (e.g. o4_violated) to rule title (e.g. No bloated body)."""
    labels: dict[str, str] = {}
    for q in questions:
        if q.get("questionId") != "response_clarity_checklist":
            continue
        for row in (q.get("data") or {}).get("rows") or []:
            opts = ((row.get("question") or {}).get("data") or {}).get("options") or []
            for opt in opts:
                cid = opt.get("choiceId") or ""
                if not cid:
                    continue
                title = _rule_title(opt.get("text") or "")
                labels[cid] = title or cid
        break
    return labels


def _cell_rules(value: object, rule_labels: dict[str, str]) -> list[str]:
    """Normalize a checklist cell value into display rule titles."""
    if value is None:
        return []
    raw: list = []
    if isinstance(value, dict):
        raw = value.get("value") or []
    elif isinstance(value, list):
        raw = value
    elif isinstance(value, str) and value:
        raw = [value]
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            cid = item.get("id") or item.get("choiceId") or ""
        else:
            cid = str(item) if item else ""
        if not cid:
            continue
        out.append(rule_labels.get(cid, cid))
    return out


def _choice_ids_from_cell(value: object) -> list[str]:
    """Extract raw choice ids from a checklist cell."""
    if value is None:
        return []
    raw: list = []
    if isinstance(value, dict):
        raw = value.get("value") or []
    elif isinstance(value, list):
        raw = value
    elif isinstance(value, str) and value:
        raw = [value]
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            cid = item.get("id") or item.get("choiceId") or ""
        else:
            cid = str(item) if item else ""
        if cid:
            out.append(cid)
    return out


def violated_choice_ids(entry: dict) -> list[str]:
    """All choice ids marked in the entry checklist (row-major order)."""
    raw = entry.get("response_clarity_checklist")
    if not isinstance(raw, dict):
        return []
    ids: list[str] = []
    for row in raw.get("cells") or []:
        if not isinstance(row, list):
            continue
        for cell in row:
            ids.extend(_choice_ids_from_cell(cell))
    return ids


def violated_choice_ids_for_model(entry: dict, model_idx: int) -> list[str]:
    """Choice ids violated by one model column (0=A, 1=B, 2=C)."""
    raw = entry.get("response_clarity_checklist")
    if not isinstance(raw, dict):
        return []
    ids: list[str] = []
    for row in raw.get("cells") or []:
        if not isinstance(row, list) or model_idx >= len(row):
            continue
        ids.extend(_choice_ids_from_cell(row[model_idx]))
    return ids


def checklist_from_entry(
    entry: dict,
    rule_labels: dict[str, str] | None = None,
) -> ArenaChecklist | None:
    """Parse response_clarity_checklist from a history entry."""
    raw = entry.get("response_clarity_checklist")
    if not isinstance(raw, dict):
        return None
    cols = [str(c) for c in (raw.get("colHeaders") or [])]
    rows = [str(r) for r in (raw.get("rowHeaders") or [])]
    raw_cells = raw.get("cells") or []
    labels = rule_labels or {}
    cells: list[list[list[str]]] = []
    for row in raw_cells:
        if not isinstance(row, list):
            cells.append([[] for _ in cols])
            continue
        row_out: list[list[str]] = []
        for c_idx in range(len(cols)):
            cell = row[c_idx] if c_idx < len(row) else None
            row_out.append(_cell_rules(cell, labels))
        cells.append(row_out)
    while len(cells) < len(rows):
        cells.append([[] for _ in cols])
    if not cols and not rows:
        return None
    return ArenaChecklist(col_headers=cols, row_headers=rows, cells=cells)


def _col_letter(header: str, idx: int) -> str:
    """Short column letter from a header like 'Model A'."""
    if header:
        letter = to_label(header.replace(" ", "_").lower())
        if letter:
            return letter
        stripped = header.replace("Model ", "").strip()
        if stripped:
            return stripped
    return chr(65 + idx)


def format_checklist_table(cl: ArenaChecklist):
    """Render checklist as a Rich Table with readable model columns."""
    from rich import box
    from rich.table import Table
    from rich.text import Text

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        pad_edge=True,
        padding=(0, 1),
        expand=False,
        collapse_padding=False,
    )
    table.add_column("Category", style="bold", min_width=12, no_wrap=True)
    for c_idx, h in enumerate(cl.col_headers):
        letter = _col_letter(h, c_idx)
        table.add_column(
            letter,
            min_width=18,
            max_width=36,
            overflow="fold",
            justify="center",
        )

    for r_idx, row_name in enumerate(cl.row_headers):
        row_cells = cl.cells[r_idx] if r_idx < len(cl.cells) else []
        cells: list[Text | str] = [row_name]
        for c_idx in range(len(cl.col_headers)):
            rules = row_cells[c_idx] if c_idx < len(row_cells) else []
            if rules:
                cells.append(Text("; ".join(rules), style="red"))
            else:
                cells.append(Text("—", style="dim"))
        table.add_row(*cells)
    return table


def format_checklist_markup(cl: ArenaChecklist) -> str:
    """Plain Rich-markup string of the checklist (tests / clipboard)."""
    if not cl.col_headers:
        return ""
    from rich.console import Console

    console = Console(width=120, force_terminal=False, no_color=True, highlight=False)
    with console.capture() as capture:
        console.print(format_checklist_table(cl))
    return capture.get().rstrip()


def checklist_violation_summary(cl: ArenaChecklist) -> str:
    """One-line per-model violation counts, e.g. 'A:0  B:0  C:1'."""
    if not cl.col_headers:
        return ""
    counts: list[str] = []
    for c_idx, h in enumerate(cl.col_headers):
        letter = _col_letter(h, c_idx)
        n = 0
        for row in cl.cells:
            if c_idx < len(row):
                n += len(row[c_idx])
        counts.append(f"{letter}:{n}")
    return "  ".join(counts)


def _text_field(entry: dict, key: str) -> str:
    val = (entry.get(key) or {}).get("value", "")
    return val.strip() if isinstance(val, str) else ""


def justification_sections(entry: dict) -> list[tuple[str, str]]:
    """Return non-empty (label, text) pairs from multi-field justifications."""
    sections: list[tuple[str, str]] = []
    for key, label in JUSTIFICATION_KEYS:
        text = _text_field(entry, key)
        if text:
            sections.append((label, text))
    return sections


def combined_justification(entry: dict) -> str:
    """Markdown combining all justification sections for display."""
    parts: list[str] = []
    for label, text in justification_sections(entry):
        parts.append(f"## {label}\n\n{text}")
    return "\n\n".join(parts)


def checklist_signature(entry: dict) -> str:
    """Stable signature of checklist cells for change detection."""
    raw = entry.get("response_clarity_checklist")
    if not isinstance(raw, dict):
        return ""
    cells = raw.get("cells") or []
    parts: list[str] = []
    for row in cells:
        if not isinstance(row, list):
            parts.append("|")
            continue
        row_parts: list[str] = []
        for cell in row:
            rules = _cell_rules(cell, {})
            row_parts.append(",".join(sorted(rules)))
        parts.append(";".join(row_parts))
    return "|".join(parts)


def has_arena_changes(prev: dict, curr: dict) -> bool:
    """Whether rankings, justifications, or checklist differ."""
    for key, _ in RANKING_KEYS:
        if get_full_ranking(prev, key) != get_full_ranking(curr, key):
            return True
    for key, _ in JUSTIFICATION_KEYS:
        if _text_field(prev, key) != _text_field(curr, key):
            return True
    return checklist_signature(prev) != checklist_signature(curr)


def arena_ranking_changes(prev: dict, curr: dict) -> list[str]:
    """Rich-markup lines for ranking changes (reuses classic ranking keys)."""
    return history_ranking_changes(prev, curr)


def arena_justification_diff_texts(prev: dict, curr: dict) -> tuple[str, str] | None:
    """Return (old, new) combined justification if they differ."""
    old = combined_justification(prev)
    new = combined_justification(curr)
    if old == new:
        return None
    return (old or "(none)", new or "(none)")


def arena_checklist_change_lines(
    prev: dict,
    curr: dict,
    rule_labels: dict[str, str] | None = None,
) -> list[str]:
    """Rich-markup lines summarizing checklist cell changes."""
    if checklist_signature(prev) == checklist_signature(curr):
        return []
    old_cl = checklist_from_entry(prev, rule_labels)
    new_cl = checklist_from_entry(curr, rule_labels)
    old_s = checklist_violation_summary(old_cl) if old_cl else "(none)"
    new_s = checklist_violation_summary(new_cl) if new_cl else "(none)"
    return [f"[bold]Checklist:[/]  {old_s}  [dim]\u2192[/]  {new_s}"]


def format_arena_history_meta(
    entry: dict,
    index: int,
    *,
    show_email: bool = False,
    rule_labels: dict[str, str] | None = None,
) -> str:
    """History entry header with rankings and checklist summary."""
    level = entry.get("reviewLevel", "?")
    header = f"[bold]Entry {index}[/bold]  |  Level {level}"
    if show_email:
        header += f"  |  {sanitize(entry.get('email', 'unknown'))}"
    lines = [header]
    for key, label in RANKING_KEYS:
        rl = get_full_ranking(entry, key)
        if rl:
            lines.append(f"[dim]{label}:[/dim] {rl}")
    cl = checklist_from_entry(entry, rule_labels)
    if cl:
        summary = checklist_violation_summary(cl)
        if summary:
            lines.append(f"[dim]Checklist:[/dim] {summary}")
    return "\n".join(lines)


def model_display_name(meta: ArenaMeta, idx: int, fallback: str = "") -> str:
    """Real model name from label_map / models list, else fallback."""
    letter = chr(65 + idx)
    if letter in meta.label_map:
        return meta.label_map[letter]
    if idx < len(meta.model_ids):
        return meta.model_ids[idx]
    return fallback
