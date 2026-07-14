"""Task type detection from raw API data."""

from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    CODE_REVIEW = "code_review"
    ARENA_RANKING = "arena_ranking"
    PROJECT_PROPOSAL = "project_proposal"
    UNKNOWN = "unknown"


def detect_task_type(data: dict) -> TaskType:
    """Detect task type from the raw API response data."""
    content = data.get("content", {})
    items = content.get("content", {}).get("items", [])
    item_titles = {i.get("title") for i in items}
    question_ids = {q.get("questionId") for q in content.get("questions", [])}

    # Arena ranking: code-review shape plus communication-quality checklist.
    if "Model Traces" in item_titles and "response_clarity_checklist" in question_ids:
        return TaskType.ARENA_RANKING

    if "Model Traces" in item_titles:
        return TaskType.CODE_REVIEW

    if "coding_question" in question_ids and "rubrics" in question_ids:
        return TaskType.PROJECT_PROPOSAL

    return TaskType.UNKNOWN
