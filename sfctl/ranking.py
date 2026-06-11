"""Ranking computation and model identification helpers.

Pure functions -- no Textual imports, no filesystem access, no side effects.
"""

from __future__ import annotations

from sfctl.ids import model_id, model_letter
from sfctl.models import ModelData, ModelScores
from sfctl.parsing import bump_headings, get_full_ranking, rank_color

def nav_items(models: list[ModelData]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for i in range(len(models)):
        items.append((model_letter(i), model_id(i)))
    items.append(("Overview", "overview"))
    return items

def diff_items(models: list[ModelData]) -> list[tuple[str, int, str]]:
    items: list[tuple[str, int, str]] = []
    for i, m in enumerate(models):
        for fd in m.file_diffs:
            items.append((f"Diff: {model_letter(i)} / {fd.filename}", i, fd.filename))
    return items

def ranking_for_category(scores: list[ModelScores], category: str) -> str:
    scored = [(i, getattr(scores[i], category)) for i in range(len(scores))]
    if not any(s != 0 for _, s in scored):
        return ""
    ranked = sorted(scored, key=lambda x: -x[1])
    parts = []
    for rank, (i, score) in enumerate(ranked):
        sign = f"+{score}" if score > 0 else str(score)
        color = rank_color(rank, len(ranked))
        parts.append(f"[{color}]{model_letter(i)}({sign})[/]")
    return " > ".join(parts)

def model_rank(scores: list[ModelScores], index: int) -> int:
    totals = sorted(
        [(i, scores[i].total()) for i in range(len(scores))],
        key=lambda x: -x[1],
    )
    for rank, (i, _) in enumerate(totals):
        if i == index:
            return rank
    return 0

def _normalize_history(history: list | dict) -> list:
    if not isinstance(history, list):
        return [history]
    return history

def previous_ranking_summary(history: list | dict) -> str:
    history = _normalize_history(history)
    last = history[-1] if history else {}
    sections = []
    for key, label in [
        ("preference_ranking", "Overall"),
        ("response_quality_ranking", "Resp"),
        ("code_quality_ranking", "Code"),
    ]:
        ranking = get_full_ranking(last, key)
        if ranking:
            sections.append(f"[bold]{label}:[/bold] {ranking}")
    return "  |  ".join(sections)

def local_ranking_summary(scores: list[ModelScores]) -> str:
    sections = []
    for cat, label in [("overall", "Overall"), ("response", "Resp"), ("code", "Code")]:
        ranking = ranking_for_category(scores, cat)
        if ranking:
            sections.append(f"[bold]{label}:[/bold] {ranking}")
    return "  |  ".join(sections)

def rankings_summary(scores: list[ModelScores], history: list | dict) -> str:
    local = local_ranking_summary(scores)
    prev = previous_ranking_summary(history)
    if prev and local:
        return f"[dim]Last:[/dim] {prev}  ||  [bold]Yours:[/bold] {local}"
    if prev:
        return f"[dim]Last:[/dim] {prev}"
    if local:
        return local
    return ""

def previous_model_rank(history: list | dict, index: int) -> int | None:
    """Get the rank for a model from the most recent history entry (preference ranking)."""
    history = _normalize_history(history)
    last = history[-1] if history else {}
    value = (last.get("preference_ranking") or {}).get("value") or []
    for rank, item in enumerate(value):
        if isinstance(item, dict):
            item_id = item.get("id", "")
            letter = item_id.replace("model_", "").replace("model", "").strip("_").lower()
            if len(letter) == 1 and ord(letter) - ord("a") == index:
                return rank
    return None

def model_summary_text(m: ModelData) -> str:
    messages = m.messages or [{}]
    raw = (
        (messages[-1].get("content") if messages else None) or m.trace_summary or "_No summary_"
    )
    return bump_headings(raw, 4)
