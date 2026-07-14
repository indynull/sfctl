"""Handler for code-review (ranking) tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown as RichMarkdown
from textual.widgets import Markdown, Static, TextArea

from sfctl import ids, ranking
from sfctl.diff import parse_content
from sfctl.handlers.base import TaskHandler
from sfctl.history import has_meaningful_changes, history_justification

if TYPE_CHECKING:
    from textual.containers import ScrollableContainer
    from textual.widgets import TabPane

    from sfctl.models import ModelData, ParsedContent


class RankingHandler(TaskHandler):
    """Code review: 3-model ranking with diffs, traces, and vote bars."""

    def parse(self) -> tuple[ParsedContent, list[ModelData]]:
        parsed = parse_content(self.data.get("content", {}))
        return parsed, parsed.models

    @property
    def model_count(self) -> int:
        return len(self._app.models)

    @property
    def has_model_tabs(self) -> bool:
        return True

    @property
    def has_vote_bars(self) -> bool:
        return True

    def response_source(self, idx: int) -> str:
        models = self._app.models
        return models[idx].trace_summary or "" if idx < len(models) else ""

    async def populate_overview(self, pane: TabPane) -> None:
        app = self._app
        await pane.mount_all([
            Static(app.rankings_summary(), id=ids.JUST_RANKINGS),
            Markdown(
                app.review.summary or app._EMPTY_SUMMARY,
                id=ids.JUST_PREVIEW,
            ),
            TextArea(
                app.review.summary, language="markdown",
                show_line_numbers=True, id=ids.JUST_EDITOR,
            ),
        ])
        app.query_one(f"#{ids.JUST_EDITOR}").display = False

    async def populate_model(self, container: ScrollableContainer, idx: int) -> None:
        await self._app._build_model_tabs(
            container, idx,
            tabs_id=ids.model_tabs_id(container.id),
            resp_id=ids.tab_response_id(container.id),
            trace_id=ids.tab_trace_id(container.id),
            diffs_id=ids.tab_diffs_id(container.id),
            response_widget_id=f"response-text-{idx}",
            vote_bars=True,
        )

    async def populate_unified_model(
        self, scroll: ScrollableContainer, idx: int,
    ) -> None:
        mid = ids.model_id(idx)
        await self._app._build_model_tabs(
            scroll, idx,
            tabs_id=f"split-tabs-{mid}",
            resp_id=f"split-resp-{mid}",
            trace_id=f"split-trace-{mid}",
            diffs_id=f"split-diffs-{mid}",
            response_widget_id=f"split-response-{idx}",
            defer_tabs=True,
        )

    async def populate_unified_overview(self, pane: TabPane) -> None:
        history = self._app._get_history()
        latest = history[-1] if history else {}
        just = history_justification(latest)
        if just:
            await pane.mount_all([
                Static("[bold]Justification[/bold]"),
                Static(RichMarkdown(just)),
            ])

    def has_changes(self, prev: dict, curr: dict) -> bool:
        return has_meaningful_changes(prev, curr)

    def scoreboard_parts(self) -> list[str]:
        app = self._app
        rank_str = ranking.rankings_summary(app.scores, app.data.get("history", []))
        parts: list[str] = []
        if rank_str:
            parts.append(rank_str)
        return parts

    def hidden_actions(self) -> frozenset[str]:
        return frozenset({"go_model_proposal"})
