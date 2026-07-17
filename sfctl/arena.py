"""Arena ranking: clarity checklist and multi-field justifications."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sfctl.formatting import sanitize
from sfctl.history import get_full_ranking, history_ranking_changes, to_label

JUSTIFICATION_KEYS: list[tuple[str, str]] = [
    ("prompt_understanding", "Prompt Understanding"),
    ("response_justification", "Response Justification"),
    ("code_quality_justification", "Code Justification"),
    ("overall_justification", "Overall Justification"),
]

EDITABLE_JUSTIFICATION_KEYS: list[tuple[str, str]] = [
    ("response_justification", "Response Quality"),
    ("code_quality_justification", "Code Quality"),
    ("overall_justification", "Overall"),
]

RANKING_KEYS: list[tuple[str, str]] = [
    ("preference_ranking", "Preference"),
    ("response_quality_ranking", "Response Quality"),
    ("code_quality_ranking", "Code Quality"),
]

_BRACKET_RULE = re.compile(r"\[([^\]]+)\]")


@dataclass(slots=True)
class ChecklistRule:
    """One code-quality rule option from the task question catalog."""

    choice_id: str
    title: str
    category: str


@dataclass(slots=True)
class ArenaMeta:
    """Task-level metadata for arena ranking."""

    label_map: dict[str, str] = field(default_factory=dict)
    model_ids: list[str] = field(default_factory=list)
    batch: str = ""
    dataset: str = ""
    anchor: str = ""
    rule_labels: dict[str, str] = field(default_factory=dict)
    checklist_catalog: list[ChecklistRule] = field(default_factory=list)


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
    questions = content.get("questions") or []
    catalog = parse_checklist_catalog(questions)
    rule_labels = {r.choice_id: r.title for r in catalog}
    if not rule_labels:
        rule_labels = _rule_labels_from_questions(questions)
    return ArenaMeta(
        label_map=label_map,
        model_ids=model_ids,
        batch=str(meta.get("batch") or ""),
        dataset=str(meta.get("dataset") or ""),
        anchor=str(meta.get("anchor") or ""),
        rule_labels=rule_labels,
        checklist_catalog=catalog,
    )


def parse_checklist_catalog(questions: list) -> list[ChecklistRule]:
    """Full code-quality rule catalog from task questions."""
    out: list[ChecklistRule] = []
    for q in questions:
        if q.get("questionId") != "response_clarity_checklist":
            continue
        for row in (q.get("data") or {}).get("rows") or []:
            category = str(row.get("header") or "").strip() or "Other"
            opts = ((row.get("question") or {}).get("data") or {}).get("options") or []
            for opt in opts:
                cid = str(opt.get("choiceId") or "").strip()
                if not cid:
                    continue
                title = _rule_title(opt.get("text") or "") or cid
                out.append(ChecklistRule(choice_id=cid, title=title, category=category))
        break
    return out


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
    """Unwrap a justification field from history.

    Server payloads usually use ``{"_sf_rich": true, "value": "..."}``, but
    some entries ship a bare string (or omit the field). Accept both.
    """
    raw = entry.get(key)
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        val = raw.get("value", "")
        if isinstance(val, list):
            return ", ".join(str(v) for v in val).strip()
        if isinstance(val, str):
            return val.strip()
        return str(val).strip() if val else ""
    return ""


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


def server_editable_justifications(entry: dict | None) -> dict[str, str]:
    """Map editable justification keys to server text for one history entry."""
    if not entry:
        return {key: "" for key, _ in EDITABLE_JUSTIFICATION_KEYS}
    return {key: _text_field(entry, key) for key, _ in EDITABLE_JUSTIFICATION_KEYS}


def combine_justification_map(justifications: dict[str, str]) -> str:
    """Markdown combining local multi-field justifications for clipboard/export."""
    parts: list[str] = []
    for key, label in EDITABLE_JUSTIFICATION_KEYS:
        text = (justifications.get(key) or "").strip()
        if text:
            parts.append(f"## {label}\n\n{text}")
    return "\n\n".join(parts)


def empty_justification_hint(key: str) -> str:
    """Markdown placeholder shown when a local editable section is empty."""
    hints = {
        "response_justification": (
            "*No response notes yet — **Ctrl+E** to edit (moves through sections), "
            "**v** to mark code quality rules.*"
        ),
        "code_quality_justification": (
            "*No code notes yet — **Ctrl+E** to edit, "
            "**y** on a diff to copy a snippet.*"
        ),
        "overall_justification": (
            "*No overall notes yet — **Ctrl+E** to edit.*"
        ),
    }
    return hints.get(key, "*Empty — press **Ctrl+E** to write.*")


def section_header_hint(key: str) -> str:
    """Dim keyboard-hint suffix for an editable section title."""
    _ = key
    return "click to edit · Ctrl+E cycles · Esc saves"


def append_violation_note(
    response_text: str,
    *,
    model_letter: str,
    rule_label: str,
    why: str,
) -> str:
    """Insert a labeled violation note under a model heading in response text.

    Layout::

        ### Model A
        #### No bloated body
        optional why text
    """
    letter = (model_letter or "?").strip().upper()[:1] or "?"
    rule = (rule_label or "Violation").strip()
    why_s = (why or "").strip()
    model_h = f"### Model {letter}"
    rule_h = f"#### {rule}"
    note_block = f"{rule_h}\n\n{why_s}\n" if why_s else f"{rule_h}\n"

    text = (response_text or "").rstrip()
    if not text:
        return f"{model_h}\n\n{note_block}".rstrip() + "\n"

    marker = f"### Model {letter}"
    idx = text.find(marker)
    if idx < 0:
        return text + f"\n\n{model_h}\n\n{note_block}".rstrip() + "\n"

    rest_start = idx + len(marker)
    next_model = re.search(r"\n### Model [A-Z]\b", text[rest_start:])
    if next_model:
        insert_at = rest_start + next_model.start()
        before, after = text[:insert_at].rstrip(), text[insert_at:]
        return before + f"\n\n{note_block}\n" + after.lstrip("\n")
    return text.rstrip() + f"\n\n{note_block}".rstrip() + "\n"


def list_checklist_violations(
    entry: dict,
    rule_labels: dict[str, str] | None = None,
) -> list[tuple[int, str, str]]:
    """Flat list of (model_idx, choice_id, rule_title) from checklist cells."""
    labels = rule_labels or {}
    pairs = selections_from_entry(entry)
    out: list[tuple[int, str, str]] = []
    for model_idx, cid in pairs:
        out.append((model_idx, cid, labels.get(cid, cid)))
    return out


def selections_from_entry(entry: dict | None) -> list[tuple[int, str]]:
    """(model_idx, choice_id) pairs from a history checklist, order preserved."""
    if not entry:
        return []
    raw = entry.get("response_clarity_checklist")
    if not isinstance(raw, dict):
        return []
    out: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for row in raw.get("cells") or []:
        if not isinstance(row, list):
            continue
        for c_idx, cell in enumerate(row):
            for cid in _choice_ids_from_cell(cell):
                key = (c_idx, cid)
                if key in seen:
                    continue
                seen.add(key)
                out.append(key)
    return out


def normalize_selections(raw: object) -> list[tuple[int, str]]:
    """Normalize persisted selection payloads to (model_idx, choice_id) pairs."""
    if not isinstance(raw, list):
        return []
    out: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for item in raw:
        model_idx: int | None = None
        cid = ""
        if isinstance(item, dict):
            try:
                model_idx = int(item.get("model", item.get("model_idx", -1)))
            except (TypeError, ValueError):
                model_idx = None
            cid = str(item.get("choice_id") or item.get("id") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                model_idx = int(item[0])
            except (TypeError, ValueError):
                model_idx = None
            cid = str(item[1] or "").strip()
        if model_idx is None or model_idx < 0 or not cid:
            continue
        key = (model_idx, cid)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def serialize_selections(selections: list[tuple[int, str]]) -> list[dict]:
    """JSON-friendly form of local checklist selections."""
    return [{"model": m, "choice_id": c} for m, c in selections]


def checklist_from_selections(
    selections: list[tuple[int, str]],
    catalog: list[ChecklistRule],
    *,
    n_models: int = 3,
    rule_labels: dict[str, str] | None = None,
) -> ArenaChecklist | None:
    """Build a display checklist from local selections + the rule catalog."""
    if not catalog and not selections:
        return None
    labels = rule_labels or {r.choice_id: r.title for r in catalog}
    categories: list[str] = []
    for r in catalog:
        if r.category not in categories:
            categories.append(r.category)
    for _, cid in selections:
        if cid not in labels and "Other" not in categories:
            categories.append("Other")
            break
    if not categories:
        categories = ["Other"]

    n = max(1, n_models)
    grid: dict[str, list[list[str]]] = {
        cat: [[] for _ in range(n)] for cat in categories
    }
    cat_for_cid = {r.choice_id: r.category for r in catalog}
    for model_idx, cid in selections:
        if model_idx < 0 or model_idx >= n:
            continue
        cat = cat_for_cid.get(cid, "Other")
        if cat not in grid:
            grid[cat] = [[] for _ in range(n)]
            if cat not in categories:
                categories.append(cat)
        title = labels.get(cid, cid)
        if title not in grid[cat][model_idx]:
            grid[cat][model_idx].append(title)

    cells = [grid[cat] for cat in categories]
    cols = [f"Model {chr(65 + i)}" for i in range(n)]
    return ArenaChecklist(col_headers=cols, row_headers=categories, cells=cells)


def selections_with_titles(
    selections: list[tuple[int, str]],
    rule_labels: dict[str, str] | None = None,
) -> list[tuple[int, str, str]]:
    """(model_idx, choice_id, title) for chip display."""
    labels = rule_labels or {}
    return [(m, c, labels.get(c, c)) for m, c in selections]


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
    if set(selections_from_entry(prev)) != set(selections_from_entry(curr)):
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


def _selection_label(
    model_idx: int,
    choice_id: str,
    rule_labels: dict[str, str] | None,
) -> str:
    """``A: No bloated body`` for history change lines."""
    labels = rule_labels or {}
    letter = chr(65 + model_idx) if 0 <= model_idx < 26 else str(model_idx)
    title = labels.get(choice_id, choice_id)
    return f"{letter}: {sanitize(title)}"


def arena_checklist_change_lines(
    prev: dict,
    curr: dict,
    rule_labels: dict[str, str] | None = None,
) -> list[str]:
    """Rich-markup lines for CQ violation selection changes.

    Rankings show old → new values; justifications show a redline. Checklist
    changes used to be only count summaries (``A:1 → A:1``), which hid rule
    swaps. Prefer explicit per-model add/remove of marked rules, with a count
    summary when totals move.
    """
    old_pairs = set(selections_from_entry(prev))
    new_pairs = set(selections_from_entry(curr))
    sig_changed = checklist_signature(prev) != checklist_signature(curr)
    if old_pairs == new_pairs and not sig_changed:
        return []

    lines: list[str] = []
    old_cl = checklist_from_entry(prev, rule_labels)
    new_cl = checklist_from_entry(curr, rule_labels)
    old_s = checklist_violation_summary(old_cl) if old_cl else "(none)"
    new_s = checklist_violation_summary(new_cl) if new_cl else "(none)"
    if old_s != new_s:
        lines.append(
            f"[bold]Checklist:[/]  {old_s or '[dim](none)[/]'}  "
            f"[dim]\u2192[/]  {new_s or '[dim](none)[/]'}"
        )
    else:
        lines.append("[bold]Checklist:[/]  selections changed")

    added = sorted(new_pairs - old_pairs, key=lambda p: (p[0], p[1]))
    removed = sorted(old_pairs - new_pairs, key=lambda p: (p[0], p[1]))
    for model_idx, cid in added:
        lab = _selection_label(model_idx, cid, rule_labels)
        lines.append(f"  [green]+[/] {lab}")
    for model_idx, cid in removed:
        lab = _selection_label(model_idx, cid, rule_labels)
        lines.append(f"  [red]-[/] {lab}")

    # Signature-only change (structure/order) without id-level add/remove.
    if not added and not removed and sig_changed:
        lines.append("  [dim](cell structure changed)[/]")
    return lines


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
