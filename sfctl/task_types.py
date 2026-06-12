"""Task type detection from raw API data."""

from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    CODE_REVIEW = "code_review"
    PROJECT_PROPOSAL = "project_proposal"
    UNKNOWN = "unknown"


def detect_task_type(data: dict) -> TaskType:
    """Detect task type from the raw API response data."""
    content = data.get("content", {})
    questions = content.get("questions", [])
    question_ids = {q.get("questionId") for q in questions if isinstance(q, dict)}
    items = content.get("content", {}).get("items", [])
    item_titles = {i.get("title") for i in items if isinstance(i, dict)}

    if "Model Traces" in item_titles:
        return TaskType.CODE_REVIEW

    if "coding_question" in question_ids and "rubrics" in question_ids:
        return TaskType.PROJECT_PROPOSAL

    return TaskType.UNKNOWN
