"""Local scoring, annotation persistence, and justification rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sfctl.arena import (
    EDITABLE_JUSTIFICATION_KEYS,
    combine_justification_map,
    normalize_selections,
    selections_from_entry,
    serialize_selections,
    server_editable_justifications,
)
from sfctl.config import data_dir
from sfctl.history import as_history_list
from sfctl.models import Annotation, ModelScores

_JUST_KEYS = [key for key, _ in EDITABLE_JUSTIFICATION_KEYS]


def safe_task_id(task_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", task_id)


def scores_path(task_id: str) -> Path:
    return data_dir() / f"{safe_task_id(task_id)}_scores.json"


def justification_path(task_id: str) -> Path:
    return data_dir() / f"{safe_task_id(task_id)}.md"


def annotations_path(task_id: str) -> Path:
    return data_dir() / f"{safe_task_id(task_id)}_annotations.json"


def empty_justifications() -> dict[str, str]:
    return {key: "" for key in _JUST_KEYS}


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

    summary = ""
    jp = justification_path(task_id)
    if jp.exists():
        summary = jp.read_text(encoding="utf-8")
    return annotations, summary


def latest_server_justification(history: list | None) -> str:
    """Extract the classic single justification from the latest history entry."""
    if not history:
        return ""
    h = history if isinstance(history, list) else [history]
    if not h:
        return ""
    raw = h[-1].get("justification")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        last_just = raw.get("value", "")
        return last_just if isinstance(last_just, str) else ""
    return ""


def latest_server_multi_justifications(history: list | None) -> dict[str, str]:
    """Editable multi-field justifications from the latest history entry."""
    h = as_history_list(history)
    entry = h[-1] if h else None
    return server_editable_justifications(entry)


def _normalize_justifications(raw: object) -> dict[str, str]:
    out = empty_justifications()
    if not isinstance(raw, dict):
        return out
    for key in _JUST_KEYS:
        val = raw.get(key, "")
        out[key] = val if isinstance(val, str) else ""
    return out


def load_annotations(
    task_id: str, num_models: int, history: list | None = None
) -> tuple[list[list[Annotation]], str, str, dict[str, str]]:
    """Load annotations, summary, comments, and multi-field justifications.

    Returns
    (per-model annotation lists, summary text, review comments, justifications).

    Classic ranking uses *summary*. Arena uses *justifications* (response / code
    / overall). Server multi-field text seeds empty local sections; a changed
    classic server justification still refreshes *summary* as before.
    """
    server_just = latest_server_justification(history)
    server_multi = latest_server_multi_justifications(history)

    path = annotations_path(task_id)
    if path.exists():
        data = json.loads(path.read_text())
        local_summary = data.get("summary", "")
        prev_server = data.get("_server_justification", "")
        review_comments = data.get("review_comments", "")
        justifications = _normalize_justifications(data.get("justifications"))
        prev_multi = data.get("_server_multi_justifications") or {}
        annotations: list[list[Annotation]] = []
        for i in range(num_models):
            raw = data.get(str(i), [])
            annotations.append([Annotation.from_dict(d) for d in raw])
        for key in _JUST_KEYS:
            server_val = server_multi.get(key, "")
            prev_val = prev_multi.get(key, "") if isinstance(prev_multi, dict) else ""
            local_val = justifications.get(key, "")
            if server_val and (server_val != prev_val or not local_val.strip()):
                justifications[key] = server_val
        if server_just and (server_just != prev_server or not local_summary.strip()):
            return annotations, server_just, review_comments, justifications
        return annotations, local_summary, review_comments, justifications

    sp = scores_path(task_id)
    jp = justification_path(task_id)
    if sp.exists() or jp.exists():
        annotations, summary = _migrate_legacy(task_id, num_models)
        if not summary.strip() and server_just.strip():
            summary = server_just
        return annotations, summary, "", dict(server_multi)

    return [[] for _ in range(num_models)], server_just, "", dict(server_multi)


def save_annotations(
    task_id: str,
    annotations: list[list[Annotation]],
    summary: str,
    server_justification: str = "",
    review_comments: str = "",
    justifications: dict[str, str] | None = None,
    server_multi_justifications: dict[str, str] | None = None,
    checklist_selections: list[tuple[int, str]] | None = None,
    server_checklist_selections: list[tuple[int, str]] | None = None,
) -> None:
    """Persist annotations, summary, multi-justifications, checklist, and comments."""
    data: dict = {}
    for i, model_anns in enumerate(annotations):
        data[str(i)] = [a.to_dict() for a in model_anns]
    data["summary"] = summary
    data["_server_justification"] = server_justification
    data["review_comments"] = review_comments
    data["justifications"] = _normalize_justifications(justifications or {})
    data["_server_multi_justifications"] = _normalize_justifications(
        server_multi_justifications or {}
    )
    data["checklist_selections"] = serialize_selections(checklist_selections or [])
    data["_server_checklist_selections"] = serialize_selections(
        server_checklist_selections or []
    )
    annotations_path(task_id).write_text(json.dumps(data, indent=2))


def latest_server_checklist_selections(history: list | None) -> list[tuple[int, str]]:
    """Checklist (model, choice_id) pairs from the latest history entry."""
    h = as_history_list(history)
    return selections_from_entry(h[-1] if h else None)


def _load_checklist_selections(
    task_id: str, history: list | None
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Return (local_selections, server_selections) with seed-from-server rules."""
    server = latest_server_checklist_selections(history)
    path = annotations_path(task_id)
    if not path.exists():
        return list(server), list(server)
    data = json.loads(path.read_text())
    local = normalize_selections(data.get("checklist_selections"))
    prev_server = normalize_selections(data.get("_server_checklist_selections"))
    if server and (server != prev_server or not local):
        return list(server), list(server)
    return local, list(server)


class ReviewState:
    """Encapsulates all persistent review state for a task.

    Owns annotations, summary (classic ranking), multi-field justifications
    (arena), local code-quality checklist selections, review comments, and scores.
    """

    def __init__(
        self,
        task_id: str,
        num_models: int,
        history: list | None = None,
    ) -> None:
        self.task_id = task_id
        self.annotations: list[list[Annotation]]
        self.summary: str
        self.comments: str
        self.justifications: dict[str, str]
        (
            self.annotations,
            self.summary,
            self.comments,
            self.justifications,
        ) = load_annotations(task_id, num_models, history)
        self.server_justification = latest_server_justification(history)
        self.server_multi_justifications = latest_server_multi_justifications(history)
        self.checklist_selections: list[tuple[int, str]]
        self.server_checklist_selections: list[tuple[int, str]]
        (
            self.checklist_selections,
            self.server_checklist_selections,
        ) = _load_checklist_selections(task_id, history)
        self.scores: list[ModelScores] = scores_from_annotations(self.annotations)

    def add_annotation(self, model_index: int, annotation: Annotation) -> None:
        """Append an annotation for a model, recompute scores, and persist."""
        if 0 <= model_index < len(self.annotations):
            self.annotations[model_index].append(annotation)
        self.scores = scores_from_annotations(self.annotations)
        self.persist()

    def set_summary(self, text: str) -> None:
        """Update the classic single summary and persist."""
        self.summary = text
        self.persist()

    def set_justification(self, key: str, text: str) -> None:
        """Update one arena multi-field justification and persist."""
        if key not in self.justifications:
            self.justifications[key] = ""
        self.justifications[key] = text
        self.persist()

    def set_comments(self, text: str) -> None:
        """Update review comments and persist."""
        self.comments = text
        self.persist()

    def justification_text(self, key: str) -> str:
        return self.justifications.get(key, "")

    def combined_justifications(self) -> str:
        """Markdown of all non-empty multi-field justifications."""
        return combine_justification_map(self.justifications)

    def has_checklist_selection(self, model_idx: int, choice_id: str) -> bool:
        return (model_idx, choice_id) in self.checklist_selections

    def toggle_checklist_selection(self, model_idx: int, choice_id: str) -> bool:
        """Toggle a code-quality mark. Returns True if selected, False if cleared."""
        key = (model_idx, choice_id)
        if key in self.checklist_selections:
            self.checklist_selections = [
                p for p in self.checklist_selections if p != key
            ]
            self.persist()
            return False
        self.checklist_selections = [*self.checklist_selections, key]
        self.persist()
        return True

    def set_checklist_selection(
        self, model_idx: int, choice_id: str, selected: bool
    ) -> None:
        key = (model_idx, choice_id)
        has = key in self.checklist_selections
        if selected and not has:
            self.checklist_selections = [*self.checklist_selections, key]
            self.persist()
        elif not selected and has:
            self.checklist_selections = [
                p for p in self.checklist_selections if p != key
            ]
            self.persist()

    def persist(self) -> None:
        """Write current state to disk."""
        save_annotations(
            self.task_id,
            self.annotations,
            self.summary,
            self.server_justification,
            self.comments,
            self.justifications,
            self.server_multi_justifications,
            self.checklist_selections,
            self.server_checklist_selections,
        )

    def reset(self, num_models: int, history: list | None = None) -> None:
        """Reset to server state, clearing local data files."""
        for path in (
            annotations_path(self.task_id),
            scores_path(self.task_id),
            justification_path(self.task_id),
        ):
            if path.exists():
                path.unlink()
        (
            self.annotations,
            self.summary,
            self.comments,
            self.justifications,
        ) = load_annotations(self.task_id, num_models, history)
        self.server_justification = latest_server_justification(history)
        self.server_multi_justifications = latest_server_multi_justifications(history)
        (
            self.checklist_selections,
            self.server_checklist_selections,
        ) = _load_checklist_selections(self.task_id, history)
        self.scores = scores_from_annotations(self.annotations)

    def reload(self, task_id: str, num_models: int, history: list | None = None) -> None:
        """Reload state for a (possibly new) task ID."""
        self.task_id = task_id
        (
            self.annotations,
            self.summary,
            self.comments,
            self.justifications,
        ) = load_annotations(task_id, num_models, history)
        self.server_justification = latest_server_justification(history)
        self.server_multi_justifications = latest_server_multi_justifications(history)
        (
            self.checklist_selections,
            self.server_checklist_selections,
        ) = _load_checklist_selections(task_id, history)
        self.scores = scores_from_annotations(self.annotations)
