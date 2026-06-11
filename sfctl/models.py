"""Pydantic models for Starfleet API responses and app-level data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field


class BaseConfig(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, str_strip_whitespace=True)


class RankingItem(BaseConfig):
    id: str | None = None


class ValueWrapper(BaseConfig):
    value: list[RankingItem] | str | None = None


class ActionHistoryItem(BaseConfig):
    type: str | None = None
    timestamp: str | int | float | None = None
    fromLevel: float | None = None
    toLevel: float | None = None
    userId: str | None = None
    passQA: bool | None = None


class TaskResponse(BaseConfig):
    taskId: str | None = None
    status: str | None = None
    reviewLevel: float | None = None
    actionHistory: list[ActionHistoryItem] = Field(default_factory=list)


class FeedbackEntry(BaseConfig):
    reviewLevel: float | None = None
    email: str | None = None
    timestamp: str | int | float | None = None
    message: str | None = None
    score: int | None = None


class FeedbackResponse(BaseConfig):
    entries: list[FeedbackEntry] = Field(default_factory=list)


class HistoryEntry(BaseConfig):
    justification: ValueWrapper | None = None
    preference_ranking: ValueWrapper | None = None
    response_quality_ranking: ValueWrapper | None = None
    code_quality_ranking: ValueWrapper | None = None
    confidence: ValueWrapper | None = None
    email: str | None = None
    reviewLevel: float | None = None
    feedback: FeedbackResponse | None = None


class ContentItem(BaseConfig):
    type: str | None = None
    title: str | None = None
    text: str | None = None
    content: str | None = None
    items: list[dict] = Field(default_factory=list)


class ContentData(BaseConfig):
    items: list[ContentItem] = Field(default_factory=list)


class ContentResponse(BaseConfig):
    content: ContentData | None = None
    taskId: str | None = None



@dataclass(slots=True)
class FileDiff:
    """A single file's diff block extracted from a multi-file unified diff."""

    filename: str
    diff: str


@dataclass(slots=True)
class ModelData:
    """Parsed model trace data for one model in a task."""

    name: str
    diff: str
    trace_summary: str | None
    messages: list
    tool_events: list
    file_diffs: list[FileDiff] = field(default_factory=list)


@dataclass(slots=True)
class ParsedContent:
    """Parsed task content from the API."""

    task_id: str | None
    repository: str
    current_prompt: str
    models: list[ModelData] = field(default_factory=list)


@dataclass(slots=True)
class Annotation:
    """A single note about a model -- a yank, a vote, or both."""

    filename: str = ""
    line_ref: str = ""
    snippet: str = ""
    comment: str = ""
    context: str = "overall"  # "code" | "response" | "overall"
    sentiment: int = 0  # +1 / 0 / -1

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "line_ref": self.line_ref,
            "snippet": self.snippet,
            "comment": self.comment,
            "context": self.context,
            "sentiment": self.sentiment,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Annotation:
        return cls(
            filename=d.get("filename", ""),
            line_ref=d.get("line_ref", ""),
            snippet=d.get("snippet", ""),
            comment=d.get("comment", ""),
            context=d.get("context", "overall"),
            sentiment=d.get("sentiment", 0),
        )


@dataclass(slots=True)
class ModelScores:
    """Local voting scores for a single model."""

    overall: int = 0
    response: int = 0
    code: int = 0

    def total(self) -> int:
        return self.overall + self.response + self.code

    def any_nonzero(self) -> bool:
        return self.overall != 0 or self.response != 0 or self.code != 0

    def to_dict(self) -> dict[str, int]:
        return {"overall": self.overall, "response": self.response, "code": self.code}

    @classmethod
    def from_dict(cls, d: dict[str, int]) -> ModelScores:
        return cls(
            overall=d.get("overall", 0),
            response=d.get("response", 0),
            code=d.get("code", 0),
        )


class CookieProfile(NamedTuple):
    """A discovered browser cookie profile."""

    path: str
    label: str
    func: str
