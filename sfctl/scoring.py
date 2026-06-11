"""Local scoring, annotation persistence, and justification rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sfctl.config import data_dir
from sfctl.models import Annotation, ModelScores


def _safe_task_id(task_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", task_id)



def scores_path(task_id: str) -> Path:
    return data_dir() / f"{_safe_task_id(task_id)}_scores.json"


def justification_path(task_id: str) -> Path:
    return data_dir() / f"{_safe_task_id(task_id)}.md"



def annotations_path(task_id: str) -> Path:
    return data_dir() / f"{_safe_task_id(task_id)}_annotations.json"



def scores_from_annotations(annotations: list[list[Annotation]]) -> list[ModelScores]:
    """Compute ModelScores per model from structured annotations."""
    scores: list[ModelScores] = []
    for model_anns in annotations:
        s = ModelScores()
        for a in model_anns:
            ctx = a.context if a.context in ("overall", "response", "code") else "overall"
            setattr(s, ctx, getattr(s, ctx) + a.sentiment)
        scores.append(s)
    return scores



def _migrate_legacy(task_id: str, num_models: int) -> tuple[list[list[Annotation]], str]:
    """Read old _scores.json + .md and convert to annotations + summary."""
    annotations: list[list[Annotation]] = [[] for _ in range(num_models)]

    # Migrate scores -> bare-sentiment annotations
    sp = scores_path(task_id)
    if sp.exists():
        saved = json.loads(sp.read_text())
        for k, v in saved.items():
            idx = int(k)
            if 0 <= idx < num_models:
                ms = ModelScores.from_dict(v)
                for ctx in ("overall", "response", "code"):
                    val = getattr(ms, ctx)
                    sentiment = 1 if val > 0 else -1
                    for _ in range(abs(val)):
                        annotations[idx].append(Annotation(context=ctx, sentiment=sentiment))

    # Migrate justification -> summary
    summary = ""
    jp = justification_path(task_id)
    if jp.exists():
        summary = jp.read_text(encoding="utf-8")
    return annotations, summary


def load_annotations(
    task_id: str, num_models: int, history: list | None = None
) -> tuple[list[list[Annotation]], str]:
    """Load annotations and summary for a task.

    Returns (per-model annotation lists, summary text).
    Falls back to legacy scores/justification if no annotations file exists.
    """
    path = annotations_path(task_id)
    if path.exists():
        data = json.loads(path.read_text())
        summary = data.get("summary", "")
        annotations: list[list[Annotation]] = []
        for i in range(num_models):
            raw = data.get(str(i), [])
            annotations.append([Annotation.from_dict(d) for d in raw])
        return annotations, summary

    # Check legacy files
    sp = scores_path(task_id)
    jp = justification_path(task_id)
    if sp.exists() or jp.exists():
        annotations, summary = _migrate_legacy(task_id, num_models)
        # If legacy justification is empty, try history
        if not summary.strip() and history:
            h = history if isinstance(history, list) else [history]
            if h:
                last_just = (h[-1].get("justification") or {}).get("value", "")
                if isinstance(last_just, str) and last_just.strip():
                    summary = last_just
        return annotations, summary

    # No local data at all -- try history for initial summary
    summary = ""
    if history:
        h = history if isinstance(history, list) else [history]
        if h:
            last_just = (h[-1].get("justification") or {}).get("value", "")
            if isinstance(last_just, str) and last_just.strip():
                summary = last_just
    return [[] for _ in range(num_models)], summary


def save_annotations(
    task_id: str, annotations: list[list[Annotation]], summary: str
) -> None:
    """Persist annotations and summary to disk."""
    data: dict = {}
    for i, model_anns in enumerate(annotations):
        data[str(i)] = [a.to_dict() for a in model_anns]
    data["summary"] = summary
    annotations_path(task_id).write_text(json.dumps(data, indent=2))



def _model_letter(index: int) -> str:
    return chr(65 + index)


def render_annotations_only(annotations: list[list[Annotation]]) -> str:
    """Render annotations (yanked snippets + tallies) as markdown, without summary."""
    parts: list[str] = []

    for i, model_anns in enumerate(annotations):
        if not model_anns:
            continue
        tallies: dict[str, int] = {}
        for a in model_anns:
            ctx = a.context if a.context in ("overall", "response", "code") else "overall"
            tallies[ctx] = tallies.get(ctx, 0) + a.sentiment
        tally_parts = []
        for ctx in ("code", "response", "overall"):
            v = tallies.get(ctx, 0)
            if v != 0:
                sign = f"+{v}" if v > 0 else str(v)
                tally_parts.append(f"{ctx}: {sign}")
        tally_str = f"  ({', '.join(tally_parts)})" if tally_parts else ""

        parts.append(f"## Model {_model_letter(i)}{tally_str}\n")

        for a in model_anns:
            if not a.filename and not a.snippet and not a.comment:
                continue
            sentiment_marker = {1: "(+1)", -1: "(-1)", 0: "(0)"}[a.sentiment]
            line = sentiment_marker
            if a.comment:
                line += f" {a.comment}"
            parts.append(line)
            if a.filename and a.line_ref:
                parts.append(f"`{a.filename}:{a.line_ref}`")
            elif a.filename:
                parts.append(f"`{a.filename}`")
            if a.snippet:
                parts.append(f"```diff\n{a.snippet}\n```")
            parts.append("")

    return "\n".join(parts)


def render_annotations_md(
    annotations: list[list[Annotation]], summary: str
) -> str:
    """Render all annotations + summary as readable markdown."""
    ann_text = render_annotations_only(annotations)
    parts: list[str] = []
    if ann_text.strip():
        parts.append(ann_text)
    if summary.strip():
        if parts:
            parts.append("---\n")
        parts.append("## Summary\n")
        parts.append(summary.strip())
        parts.append("")
    return "\n".join(parts)



def load_scores(task_id: str, num_models: int) -> list[ModelScores]:
    scores = [ModelScores() for _ in range(num_models)]
    path = scores_path(task_id)
    if path.exists():
        saved = json.loads(path.read_text())
        for k, v in saved.items():
            idx = int(k)
            if 0 <= idx < num_models:
                scores[idx] = ModelScores.from_dict(v)
    return scores


def save_scores(task_id: str, scores: list[ModelScores]) -> None:
    data = {str(i): s.to_dict() for i, s in enumerate(scores)}
    scores_path(task_id).write_text(json.dumps(data, indent=2))


def load_justification(task_id: str, history: list) -> str:
    path = justification_path(task_id)
    if path.exists():
        return path.read_text(encoding="utf-8")
    if not isinstance(history, list):
        history = [history]
    if history:
        last_just = (history[-1].get("justification") or {}).get("value", "")
        if isinstance(last_just, str) and last_just.strip():
            return last_just
    return ""


def save_justification(task_id: str, content: str) -> None:
    justification_path(task_id).write_text(content, encoding="utf-8")
