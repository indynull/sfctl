"""Task-type handler registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sfctl.handlers.base import TaskHandler
from sfctl.task_types import TaskType

if TYPE_CHECKING:
    from sfctl.app import StarfleetApp

__all__ = ["TaskHandler", "handler_for_type"]


def handler_for_type(
    task_type: TaskType, app: StarfleetApp, data: dict,
) -> TaskHandler:
    """Return the handler instance for the given task type."""
    if task_type == TaskType.PROJECT_PROPOSAL:
        from sfctl.handlers.proposal import ProposalHandler

        return ProposalHandler(app, data)
    if task_type == TaskType.ARENA_RANKING:
        from sfctl.handlers.arena import ArenaHandler

        return ArenaHandler(app, data)
    from sfctl.handlers.ranking import RankingHandler

    return RankingHandler(app, data)
