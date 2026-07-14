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

    async def _ensure_deferred_tab(self, pane_id: str, tabs: TabbedContent) -> None:
        """Mount deferred tab content if this pane was still a placeholder.

        Unified model columns register Diffs/Trace as deferred; activating the
        tab alone is async, so search must materialize content before querying
        collapsibles.
        """
        deferred = self._app._deferred_tabs.pop(pane_id, None)
        if not deferred:
            return
        try:
            pane = tabs.get_pane(pane_id)
        except Exception:
            # Put it back if the pane is missing so a later activation can retry.
            self._app._deferred_tabs[pane_id] = deferred
            return
        kind = deferred[0]
        app = self._app
        if kind == "diffs":
            _, idx, vote_bars = deferred
            await app._mount_diffs_content(pane, idx, vote_bars)
        elif kind == "trace":
            _, idx, _vote_bars = deferred
            await app._mount_trace_content(pane, app.models[idx].tool_events)
        elif kind == "proposal-diffs" and app.proposal:
            await pane.mount_all([
                LazyCollapsible.for_diff(fd.filename, fd.diff, "S", classes="inner")
                for fd in app.proposal.file_diffs
            ])
        elif kind == "proposal-trace" and app.proposal:
            await app._mount_trace_content(
                pane,
                app.proposal.tool_events,
                model_id=app.proposal.model_id,
                bash_history=app.proposal.bash_history,
                setup_commands=app.proposal.setup_commands,
            )
        await app.workers.wait_for_complete()

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

    async def _navigate_to_model_column(self, model_index: int) -> str:
        """Focus the model column (unified or individual) and return model widget id."""
        mid = model_id(model_index)
        if self._in_unified():
            if self._app.task_type != TaskType.PROJECT_PROPOSAL:
                await self._app._focus_unified_model(model_index)
        else:
            await self._app.go_to(mid)
        return mid

    async def _expand_trace_event(self, event_index: int, events: list[dict]) -> None:
        if self._app.task_type == TaskType.PROJECT_PROPOSAL:
            model_index = 0
        else:
            if not self._app._current_model():
                return
            model_index = self._app.current_model_index
        mid = await self._navigate_to_model_column(model_index)
        resolved = self._resolve_tabs_and_container(mid)
        if not resolved:
            return
        tabs, container = resolved
        trace_tid = f"split-trace-{mid}" if self._in_unified() else tab_trace_id(mid)
        tabs.active = trace_tid
        await self._ensure_deferred_tab(trace_tid, tabs)
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
        mid = await self._navigate_to_model_column(model_index)
        resolved = self._resolve_tabs_and_container(mid)
        if not resolved:
            return
        tabs, container = resolved
        diffs_tid = f"split-diffs-{mid}" if self._in_unified() else tab_diffs_id(mid)
        tabs.active = diffs_tid
        # Unified (and some proposal) Diffs tabs are deferred until first open.
        await self._ensure_deferred_tab(diffs_tid, tabs)
        diffs_pane = tabs.get_pane(diffs_tid)
        for collapsible in diffs_pane.query(Collapsible):
            if str(collapsible.title) == filename:
                already_open = not collapsible.collapsed
                self._app._pending_grep = (collapsible, container, grep_line)
                if already_open:
                    self.flush_pending_grep()
                else:
                    # LazyCollapsible mounts DiffDisplay on expand; flush once ready.
                    collapsible.collapsed = False
                    if not self.flush_pending_grep():
                        # Message handlers may not have run yet — retry after refresh.
                        self._app.call_after_refresh(self.flush_pending_grep)
                return
        self._app._status(f"Diff not found: {filename}")

    def flush_pending_grep(self) -> bool:
        """Scroll to a pending grep target once DiffDisplay is mounted.

        Returns True if the jump completed. Keeps the pending request when the
        DiffDisplay is not mounted yet (expand still in flight).
        """
        pending = getattr(self._app, "_pending_grep", None)
        if not pending:
            return False
        collapsible, container, grep_line = pending
        displays = list(collapsible.query(DiffDisplay))
        if not displays:
            # Expand without content yet — keep pending for on_collapsible_expanded.
            return False
        self._app._pending_grep = None
        diff_display = displays[0]
        if grep_line:
            target = self._match_diff_line_index(diff_display, grep_line)
            if target is not None:
                diff_display.move_cursor((target, 0))
                # Scroll the caret into view inside the TextArea, then the column.
                try:
                    diff_display.scroll_cursor_visible(animate=False)
                except Exception:
                    try:
                        diff_display.scroll_to(0, target, animate=False)
                    except Exception:
                        pass
                try:
                    diff_display.focus()
                except Exception:
                    pass
        # Prefer scrolling to the cursor line, not just the top of the file widget.
        try:
            container.scroll_to_widget(diff_display, top=False, animate=False)
        except Exception:
            container.scroll_to_widget(diff_display, top=True, animate=False)
        return True

    @staticmethod
    def _deprefix_diff_line(line: str) -> str:
        """Strip a single leading +/- marker; keep indentation after it."""
        s = line.rstrip("\n")
        if s.startswith(("+++", "---")):
            return s
        if s[:1] in "+-":
            return s[1:]
        return s

    @classmethod
    def _match_diff_line_index(cls, diff_display: DiffDisplay, grep_line: str) -> int | None:
        """Map a grep hit (often a raw unified-diff line) onto DiffDisplay row index.

        Prefer exact / full-line matches so two lines that differ only by
        indentation (common in diffs) do not collapse to the first hit.
        """
        needle = grep_line.strip()
        if not needle:
            return None
        lines = [ln.rstrip("\n") for ln in diff_display.diff_text.splitlines()]

        for line_idx, line in enumerate(lines):
            if line.strip() == needle:
                return line_idx

        for line_idx, line in enumerate(lines):
            if needle in line.strip():
                return line_idx

        needle_body = cls._deprefix_diff_line(needle)
        if needle_body != needle:
            for line_idx, line in enumerate(lines):
                body = cls._deprefix_diff_line(line.strip())
                if body == needle_body or needle_body in body:
                    return line_idx
        return None
