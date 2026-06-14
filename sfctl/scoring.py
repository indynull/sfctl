"""Local scoring, annotation persistence, and justification rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sfctl.config import data_dir
from sfctl.models import Annotation, ModelScores


def safe_task_id(task_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", task_id)


def scores_path(task_id: str) -> Path:
    return data_dir() / f"{safe_task_id(task_id)}_scores.json"


def justification_path(task_id: str) -> Path:
    return data_dir() / f"{safe_task_id(task_id)}.md"


def annotations_path(task_id: str) -> Path:
    return data_dir() / f"{safe_task_id(task_id)}_annotations.json"


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
    """Extract the justification from the latest history entry."""
    if not history:
        return ""
    h = history if isinstance(history, list) else [history]
    if not h:
        return ""
    last_just = (h[-1].get("justification") or {}).get("value", "")
    return last_just if isinstance(last_just, str) else ""


def load_annotations(
    task_id: str, num_models: int, history: list | None = None
) -> tuple[list[list[Annotation]], str, str]:
    """Load annotations, summary, and review comments for a task.

    Returns (per-model annotation lists, summary text, review comments).
    Falls back to legacy scores/justification if no annotations file exists.

    The summary always reflects the latest server justification when it has
    changed since the local copy was saved, so new revisions are picked up.
    """
    server_just = latest_server_justification(history)

    path = annotations_path(task_id)
    if path.exists():
        data = json.loads(path.read_text())
        local_summary = data.get("summary", "")
        prev_server = data.get("_server_justification", "")
        review_comments = data.get("review_comments", "")
        annotations: list[list[Annotation]] = []
        for i in range(num_models):
            raw = data.get(str(i), [])
            annotations.append([Annotation.from_dict(d) for d in raw])
        if server_just and (server_just != prev_server or not local_summary.strip()):
            return annotations, server_just, review_comments
        return annotations, local_summary, review_comments

    sp = scores_path(task_id)
    jp = justification_path(task_id)
    if sp.exists() or jp.exists():
        annotations, summary = _migrate_legacy(task_id, num_models)
        if not summary.strip() and server_just.strip():
            summary = server_just
        return annotations, summary, ""

    return [[] for _ in range(num_models)], server_just, ""


def save_annotations(
    task_id: str,
    annotations: list[list[Annotation]],
    summary: str,
    server_justification: str = "",
    review_comments: str = "",
) -> None:
    """Persist annotations, summary, and review comments to disk."""
    data: dict = {}
    for i, model_anns in enumerate(annotations):
        data[str(i)] = [a.to_dict() for a in model_anns]
    data["summary"] = summary
    data["_server_justification"] = server_justification
    data["review_comments"] = review_comments
    annotations_path(task_id).write_text(json.dumps(data, indent=2))


class ReviewState:
    """Encapsulates all persistent review state for a task.

    Owns annotations, summary, review comments, server justification, and
    computed scores. Provides load, save, and mutation methods.
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
        self.annotations, self.summary, self.comments = load_annotations(
            task_id, num_models, history,
        )
        self.server_justification = latest_server_justification(history)
        self.scores: list[ModelScores] = scores_from_annotations(self.annotations)

    def add_annotation(self, model_index: int, annotation: Annotation) -> None:
        """Append an annotation for a model, recompute scores, and persist."""
        if 0 <= model_index < len(self.annotations):
            self.annotations[model_index].append(annotation)
        self.scores = scores_from_annotations(self.annotations)
        self.persist()

    def set_summary(self, text: str) -> None:
        """Update the summary and persist."""
        self.summary = text
        self.persist()

    def set_comments(self, text: str) -> None:
        """Update review comments and persist."""
        self.comments = text
        self.persist()

    def persist(self) -> None:
        """Write current state to disk."""
        save_annotations(
            self.task_id,
            self.annotations,
            self.summary,
            self.server_justification,
            self.comments,
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
        self.annotations, self.summary, self.comments = load_annotations(
            self.task_id, num_models, history,
        )
        self.server_justification = latest_server_justification(history)
        self.scores = scores_from_annotations(self.annotations)

    def reload(self, task_id: str, num_models: int, history: list | None = None) -> None:
        """Reload state for a (possibly new) task ID."""
        self.task_id = task_id
        self.annotations, self.summary, self.comments = load_annotations(
            task_id, num_models, history,
        )
        self.server_justification = latest_server_justification(history)
        self.scores = scores_from_annotations(self.annotations)
