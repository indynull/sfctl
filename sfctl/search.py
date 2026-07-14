"""Search controller — diff/event search and grep navigation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import ScrollableContainer
from textual.widgets import Collapsible, TabbedContent

from sfctl import ids
from sfctl.ids import model_id, model_tabs_id, tab_diffs_id, tab_trace_id
from sfctl.screens import DiffSearchModal, DiffSearchResult, EventSearchModal
from sfctl.task_types import TaskType
from sfctl.widgets import DiffDisplay, LazyCollapsible

if TYPE_CHECKING:
    from sfctl.app import StarfleetApp


class SearchController:
    """Composition helper that owns file/event search and grep navigation."""

    def __init__(self, app: StarfleetApp) -> None:
        self._app = app

    def _in_unified(self) -> bool:
        return self._app._current_section == ids.UNIFIED_VIEW

    def _resolve_tabs_and_container(
        self, mid: str,
    ) -> tuple[TabbedContent, ScrollableContainer] | None:
        if self._in_unified():
            try:
                tabs = self._app.query_one(f"#split-tabs-{mid}", TabbedContent)
                container = self._app.query_one(
                    f"#split-scroll-{mid}", ScrollableContainer,
                )
                return tabs, container
            except Exception:
                return None
        try:
            tabs = self._app.query_one(f"#{model_tabs_id(mid)}", TabbedContent)
            container = self._app.query_one(f"#{mid}", ScrollableContainer)
            return tabs, container
        except Exception:
            return None

    def search_diffs(self) -> None:
        if self._app.task_type == TaskType.PROJECT_PROPOSAL:
            self._search_proposal_diffs()
            return
        m = self._app._current_model()
        if not m:
            self._app._status("Navigate to a model first.")
            return
        if not m.file_diffs:
            self._app._status("No diffs in this model.")
            return

        async def _on_result(result: DiffSearchResult | None) -> None:
            if result:
                await self.go_to_diff(result.model_index, result.filename, result.grep_line)

        self._app.push_screen(
            DiffSearchModal(self._app.current_model_index, m.file_diffs), _on_result,
        )

    def _search_proposal_diffs(self) -> None:
        if not self._app.proposal or not self._app.proposal.file_diffs:
            self._app._status("No diffs in this proposal.")
            return

        async def _on_result(result: DiffSearchResult | None) -> None:
            if result:
                await self.go_to_diff(result.model_index, result.filename, result.grep_line)

        self._app.push_screen(DiffSearchModal(0, self._app.proposal.file_diffs), _on_result)

    def search_events(self) -> None:
        if self._app.task_type == TaskType.PROJECT_PROPOSAL:
            events = self._app.proposal.tool_events if self._app.proposal else []
        else:
            m = self._app._current_model()
            events = m.tool_events if m else []
        dict_events = list(events)
        if not dict_events:
            self._app._status("No trace events.")
            return

        async def _on_result(event_index: int | None) -> None:
            if event_index is None:
                return
            await self._expand_trace_event(event_index, dict_events)

        self._app.push_screen(EventSearchModal(dict_events), _on_result)

    async def _expand_trace_event(self, event_index: int, events: list[dict]) -> None:
        if self._app.task_type == TaskType.PROJECT_PROPOSAL:
            mid = model_id(0)
        else:
            m = self._app._current_model()
            if not m:
                return
            mid = model_id(self._app.current_model_index)
        if not self._in_unified():
            await self._app.go_to(mid)
        resolved = self._resolve_tabs_and_container(mid)
        if not resolved:
            return
        tabs, container = resolved
        trace_tid = f"split-trace-{mid}" if self._in_unified() else tab_trace_id(mid)
        tabs.active = trace_tid
        target_pane = tabs.get_pane(trace_tid)

        trace_collapsibles = [
            c for c in target_pane.query(Collapsible)
            if "trace-event-c" in (c.classes or set())
        ]
        if not trace_collapsibles:
            for c in target_pane.query(LazyCollapsible):
                if c.lazy.events and not c.lazy.populated:
                    c.collapsed = False
                    await self._app.workers.wait_for_complete()
                    trace_collapsibles = [
                        cc for cc in target_pane.query(Collapsible)
                        if "trace-event-c" in (cc.classes or set())
                    ]
                    break

        if 0 <= event_index < len(trace_collapsibles):
            target = trace_collapsibles[event_index]
            target.collapsed = False
            self._app.call_later(
                lambda: container.scroll_to_widget(target, top=True, animate=False)
            )

    async def go_to_diff(
        self, model_index: int, filename: str, grep_line: str | None = None,
    ) -> None:
        is_proposal = self._app.task_type == TaskType.PROJECT_PROPOSAL
        if not is_proposal and model_index >= len(self._app.models):
            return
        mid = model_id(model_index)
        if not self._in_unified():
            await self._app.go_to(mid)
        resolved = self._resolve_tabs_and_container(mid)
        if not resolved:
            return
        tabs, container = resolved
        diffs_tid = f"split-diffs-{mid}" if self._in_unified() else tab_diffs_id(mid)
        tabs.active = diffs_tid
        diffs_pane = tabs.get_pane(diffs_tid)
        for collapsible in diffs_pane.query(Collapsible):
            if str(collapsible.title) == filename:
                already_open = not collapsible.collapsed
                self._app._pending_grep = (collapsible, container, grep_line)
                collapsible.collapsed = False
                if already_open:
                    self.flush_pending_grep()
                break

    def flush_pending_grep(self) -> None:
        """Execute a pending grep scroll when the DiffDisplay is already mounted."""
        pending = getattr(self._app, "_pending_grep", None)
        if not pending:
            return
        collapsible, container, grep_line = pending
        self._app._pending_grep = None
        for diff_display in collapsible.query(DiffDisplay):
            if grep_line:
                lines = diff_display.diff_text.splitlines()
                for line_idx, line in enumerate(lines):
                    if grep_line.strip() in line.strip():
                        diff_display.scroll_to(0, line_idx, animate=False)
                        diff_display.move_cursor((line_idx, 0))
                        break
            container.scroll_to_widget(diff_display, top=True, animate=False)
            return
        container.scroll_to_widget(collapsible, top=True, animate=False)
