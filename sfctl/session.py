"""Local task session history — tracks which tasks the user has opened."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sfctl.config import data_dir


@dataclass(slots=True)
class TaskSession:
    """A single recorded visit to a task."""

    task_id: str
    task_type: str
    repository: str = ""
    opened_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> TaskSession:
        return cls(
            task_id=d["task_id"],
            task_type=d.get("task_type", ""),
            repository=d.get("repository", ""),
            opened_at=d.get("opened_at", ""),
        )


class SessionHistory:
    """Append-only JSONL store of task sessions.

    Each line is a JSON object representing a single TaskSession.
    Designed for a future ``sync_from_server(api_client)`` method that
    merges local history with a server-side task-worked-on list.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or data_dir() / "session_history.jsonl"

    def record(self, session: TaskSession) -> None:
        """Append a session to the history file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(session.to_dict()) + "\n")

    def _load_all(self) -> list[TaskSession]:
        if not self._path.exists():
            return []
        sessions: list[TaskSession] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                sessions.append(TaskSession.from_dict(json.loads(line)))
        return sessions

    def recent(self, limit: int = 50) -> list[TaskSession]:
        """Return the most recent sessions, newest first."""
        all_sessions = self._load_all()
        all_sessions.reverse()
        return all_sessions[:limit]

    def for_project(self, repo: str) -> list[TaskSession]:
        """Return sessions for a specific repository, newest first."""
        matches = [s for s in self._load_all() if s.repository == repo]
        matches.reverse()
        return matches

    def for_task(self, task_id: str) -> list[TaskSession]:
        """Return all sessions for a specific task ID, newest first."""
        matches = [s for s in self._load_all() if s.task_id == task_id]
        matches.reverse()
        return matches
