"""Main Starfleet TUI application."""

from __future__ import annotations

import time

from rich.markdown import Markdown as RichMarkdown
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.events import Click
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Collapsible,
    ContentSwitcher,
    Footer,
    Header,
    Link,
    Static,
    TabbedContent,
    TabPane,
)

from sfctl import ids, ranking
from sfctl.commands import NavigationProvider
from sfctl.config import get_web_url, load_config, update_config
from sfctl.constants import ARROW_DOWN, ARROW_UP, EM_DASH
from sfctl.diff import strip_diff_preamble
from sfctl.editor import EditorController
from sfctl.formatting import (
    bump_headings,
    clean_event_name,
    format_duration,
    format_event_line,
    format_timestamp,
    group_events,
    rank_color,
    sanitize,
    trace_type_color,
)
from sfctl.handlers import TaskHandler, handler_for_type
from sfctl.history import feedback_for_entry
from sfctl.ids import (
    Context,
    model_header_id,
    model_id,
    model_letter,
    model_tabs_id,
    tab_entry_id,
)
from sfctl.models import ModelData, ModelScores
from sfctl.scoring import ReviewState
from sfctl.screens import HelpModal
from sfctl.search import SearchController
from sfctl.session import SessionHistory, TaskSession
from sfctl.task_types import TaskType, detect_task_type
from sfctl.voting import VotingController
from sfctl.widgets import DiffDisplay, LazyCollapsible, SplitHandle, trace_event_detail_widgets

_MAX_CHUNK = 4500


def _system_language() -> str:
    """Return the 2-letter language code from the system locale, defaulting to 'en'.

    SFCTL_LANG takes priority (e.g. SFCTL_LANG=de), then standard locale vars.
    """
    import os

    for var in ("SFCTL_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        if val and val != "C" and val != "POSIX":
            return val.split("_")[0].split(".")[0].lower()
    return "en"


def _translate_text(text: str, translator_cls: type, target: str = "en") -> str:
    """Translate text, chunking at paragraph boundaries."""
    translator = translator_cls(source="auto", target=target)
    if len(text) <= _MAX_CHUNK:
        return translator.translate(text)
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) + 2 > _MAX_CHUNK and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    translated: list[str] = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(0.3)
        translated.append(translator.translate(chunk))
    return "\n\n".join(translated)


class StarfleetApp(App):
    TITLE = "Starfleet Control"
    COMMANDS = {NavigationProvider}
    CSS_PATH = "app.tcss"

    _EMPTY_SUMMARY = "*No summary yet -- press ctrl+e to write one, y to yank snippets.*"

    current_model_index: reactive[int] = reactive(0)
    scores: reactive[list[ModelScores]] = reactive(list, always_update=True)

    def watch_scores(self, new_scores: list[ModelScores]) -> None:
        if not hasattr(self, "_overview_populated") or not self.is_running:
            return
        self._update_scoreboard()
        self._editor.refresh_overview_annotations()
        for idx in range(len(new_scores)):
            self._voting.refresh_vote_labels(idx)

    BINDINGS = [
        Binding("0", "go_to('overview')", "Overview", show=True),
        Binding("1", "go_model(0)", "A", show=True),
        Binding("2", "go_model(1)", "B", show=True),
        Binding("3", "go_model(2)", "C", show=True),

        Binding("m", "go_model_proposal", "Model", show=True),
        Binding("+", "vote_up", f"{ARROW_UP} Up", show=True),
        Binding("-", "vote_down", f"{ARROW_DOWN} Down", show=True),
        Binding("ctrl+f", "search_diffs", "Find", show=True),
        Binding("ctrl+g", "search_events", "Events", show=False),
        Binding("ctrl+e", "edit_justification", "Edit", show=False),
        Binding("e", "toggle_collapse", "Toggle Fold", show=False),
        Binding("f", "toggle_maximize", "Toggle Focus", show=True),
        Binding("c", "copy_summary", "Copy", show=False),
        Binding("C", "copy_comments", "Copy Notes", show=False),
        Binding("y", "yank_file", "Yank", show=False),
        Binding("n", "add_comment", "Note", show=False),
        Binding("ctrl+n", "edit_comments", "Edit Notes", show=False),
        Binding("u", "split_view", "Toggle Split", show=True),
        Binding("t", "translate", "Toggle Translate", show=True),
        Binding("r", "refresh_data", "Refresh", show=False),
        Binding("ctrl+r", "reset_local", "Reset", show=False),
        Binding("@", "toggle_emails", "Toggle Emails", show=False),
        Binding("w", "toggle_response_width", "80-col", show=True),
        Binding("?", "help", "Help", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("tab", "next_tab", "Next Tab", show=False, priority=True),
        Binding("shift+tab", "prev_tab", "Prev Tab", show=False, priority=True),
    ]

    def __init__(
        self,
        task_arg: str,
        data: dict,
        cookies: dict[str, str] | None = None,
    ):
        super().__init__()
        self.task_arg = task_arg
        self.data = data
        self.cookies = cookies
        self.task_type = detect_task_type(data)
        self.task_id = data.get("task", {}).get("taskId") or task_arg

        from sfctl.models import ProposalData

        self.proposal: ProposalData | None = None
        self.handler: TaskHandler = handler_for_type(self.task_type, self, data)
        self.parsed, self.models = self.handler.parse()
        self.task_id = self.parsed.task_id or self.task_id

        self.review = ReviewState(self.task_id, len(self.models), self._get_history())
        self.scores: list[ModelScores] = self.review.scores
        self._populated_models: set[int] = set()
        self._overview_populated = False
        self._trace_type_map: dict[str, int] = {}
        self._voting = VotingController(self)
        self._search = SearchController(self)
        self._editor = EditorController(self)
        self._show_emails = False
        self._translated: dict[int | str, str] = {}
        self._translate_on = False
        self._translate_lang: str | None = None
        self._split_populated = False
        self._split_focus: int | None = None
        self._deferred_tabs: dict[str, tuple] = {}
        self._maximized = False
        self._status_timer = None

        self._update_sub_title()

        config = load_config()
        if "theme" in config:
            self.theme = config["theme"]

    def _update_sub_title(self) -> None:
        parts = [f"Task {self.task_id}"]
        if self._show_emails:
            task = self.data.get("task", {})
            email = (task.get("actionHistory") or [{}])[0].get("userId", "")
            if email and email != EM_DASH:
                parts.append(email)
        if self._translate_lang:
            parts.append(f"lang:{self._translate_lang}")
        self.sub_title = "  |  ".join(parts)

    def _get_history(self) -> list:
        """Return the history list from the task data."""
        return self.data.get("history", [])

    def nav_items(self) -> list[tuple[str, str]]:
        return ranking.nav_items(self.models)

    def diff_items(self) -> list[tuple[str, int, str]]:
        return ranking.diff_items(self.models)

    def _compose_info_bar(self) -> ComposeResult:
        """Compose the shared top info bar."""
        repo = self.parsed.repository
        self.task_url = get_web_url(f"/tasks/{self.task_id}")
        with Vertical(id=ids.INFO_BAR):
            yield Link(f"Task: {self.task_id}", url=self.task_url, id=ids.TASK_BAR)
            yield Static(self.rankings_summary(), id=ids.SCOREBOARD)
            if repo and repo != EM_DASH:
                yield Static(
                    f"[bold]Repo:[/bold] {sanitize(repo)}",
                    id=ids.REPO_BAR,
                )
            prompt = self.handler.prompt_source() or EM_DASH
            prompt = bump_headings(prompt)
            yield Static("[bold]Prompt[/bold]", id="prompt-label")
            with ScrollableContainer(id=ids.PROMPT_BAR):
                yield Static(RichMarkdown(prompt), id="prompt-text")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield from self._compose_info_bar()
        yield SplitHandle(ids.INFO_BAR, ids.CONTENT_AREA, id=ids.SPLIT_HANDLE)
        yield from self._compose_content_area()
        yield Static("", id=ids.STATUS_BAR)
        yield Footer()

    def _compose_content_area(self) -> ComposeResult:
        self._populated_models = set()
        self._overview_populated = False

        if self.task_type == TaskType.UNKNOWN:
            with (
                Vertical(id=ids.CONTENT_AREA),
                ContentSwitcher(initial=ids.OVERVIEW, id=ids.MAIN_SWITCHER),
            ):
                with ScrollableContainer(id=ids.OVERVIEW):
                    yield Static(
                        "[bold]Unsupported task type[/bold]\n\n"
                        "This task does not match a known layout. "
                        "Only the raw overview and history are available.",
                        classes="status",
                    )
            return

        n = self.handler.model_count
        with (
            Vertical(id=ids.CONTENT_AREA),
            ContentSwitcher(initial=ids.OVERVIEW, id=ids.MAIN_SWITCHER),
        ):
            for idx in range(n):
                mid = model_id(idx)
                with ScrollableContainer(id=mid):
                    yield Static(
                        self.handler.model_header_label(idx),
                        classes="view-header",
                        id=model_header_id(mid),
                    )

            if self.handler.supports_split:
                with Vertical(id=ids.UNIFIED_VIEW):
                    with Horizontal(classes="unified-responses"):
                        for idx in range(n):
                            mid = model_id(idx)
                            with Vertical(
                                id=f"split-{mid}", classes="split-col",
                            ):
                                yield Static(
                                    self.handler.model_header_label(idx),
                                    classes="view-header",
                                    id=f"split-header-{mid}",
                                )
                                yield ScrollableContainer(
                                    id=f"split-scroll-{mid}",
                                    classes="split-scroll",
                                )
                    yield ScrollableContainer(
                        id="unified-overview",
                        classes="unified-overview",
                    )

            with ScrollableContainer(id=ids.OVERVIEW):
                pass

    @staticmethod
    async def _mount_into(
        collapsible: Collapsible, *widgets: Static | Collapsible | DiffDisplay
    ) -> None:
        """Mount widgets into a Collapsible's Contents container."""
        contents = collapsible.query_one(Collapsible.Contents)
        await contents.mount_all(widgets)

    def _trace_type_index(self, name: str) -> int:
        if name not in self._trace_type_map:
            self._trace_type_map[name] = len(self._trace_type_map)
        return self._trace_type_map[name] % 10

    async def _mount_trace_content(
        self,
        pane: TabPane,
        tool_events: list,
        summary: str = "",
        model_id: str = "",
        bash_history: list[dict] | None = None,
        setup_commands: list[dict] | None = None,
    ) -> None:
        """Mount trace event widgets into a TabPane (shared by code review and proposal)."""
        tw: list = []
        if model_id:
            tw.append(Static(f"[bold]Model:[/bold] {model_id}"))
        if summary:
            tw.append(Static(RichMarkdown(bump_headings(summary, 4))))

        if setup_commands:
            setup_lines = "\n".join(
                f"[dim]{format_timestamp(cmd.get('timestamp', ''))}[/dim]  "
                f"{sanitize(cmd.get('command', ''), 200)}"
                for cmd in setup_commands
            )
            tw.append(Static(f"[bold]Setup ({len(setup_commands)}):[/bold]"))
            tw.append(Static(setup_lines))

        grouped = group_events(tool_events)
        if grouped:
            timed = [
                (name, evts, sum(e.wall_time or 0 for e in evts))
                for name, evts in grouped.items()
            ]
            timed.sort(key=lambda x: -x[2])
            parts = []
            for ename, events, total_ms in timed:
                ti = self._trace_type_index(ename)
                color = trace_type_color(ti)
                time_str = f" {format_duration(total_ms)}" if total_ms else ""
                parts.append(f"[{color}]{ename}[/] [dim]{len(events)}x{time_str}[/]")
            tw.append(Static("  ".join(parts)))
            tw.append(LazyCollapsible.for_trace(
                title=f"Event Details ({len(tool_events)} events)",
                events=tool_events,
            ))

        if bash_history:
            bh_lines = "\n".join(
                f"[dim]{format_timestamp(bh.get('timestamp', ''))}[/dim]  "
                f"{sanitize(bh.get('command', ''), 200)}"
                for bh in bash_history
            )
            bh_c = Collapsible(
                title=f"Bash History ({len(bash_history)})", collapsed=True, classes="bash-history"
            )
            tw.append(bh_c)

        if not tw:
            tw.append(Static("No trace data.", classes="status"))
        await pane.mount_all(tw)

        if bash_history:
            bh_c = pane.query_one(".bash-history", Collapsible)
            await bh_c.query_one(Collapsible.Contents).mount(Static(bh_lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._voting.handle_button(event.button.id or "")

    def _model_header_label(self, idx: int) -> str:
        return self.handler.model_header_label(idx)

    async def _build_model_tabs(
        self,
        container,
        idx: int,
        *,
        tabs_id: str,
        resp_id: str,
        trace_id: str,
        diffs_id: str,
        response_widget_id: str,
        vote_bars: bool = False,
        defer_tabs: bool = False,
    ) -> None:
        m = self.models[idx]
        tabs = TabbedContent(id=tabs_id)
        await container.mount(tabs)
        resp_pane = TabPane("Response", id=resp_id)
        await tabs.add_pane(resp_pane)
        summary = self.handler.prepare_response_text(idx, self._model_summary_text(m))
        resp_widgets: list = []
        if vote_bars:
            resp_widgets.append(self._voting.vote_bar(idx, "response"))
        chrome_prefix = "split-" if response_widget_id.startswith("split-") else ""
        resp_widgets.extend(
            self.handler.response_chrome_widgets(idx, id_prefix=chrome_prefix)
        )
        wrap_id = f"{chrome_prefix}response-wrap-{idx}"
        wrap = Vertical(
            id=wrap_id,
            classes=self.handler.response_wrap_classes(),
        )
        body = Static(
            RichMarkdown(summary),
            id=response_widget_id,
            classes=self.handler.response_body_classes(),
        )
        resp_widgets.append(wrap)
        await resp_pane.mount_all(resp_widgets)
        await wrap.mount(body)
        trace_pane = TabPane(f"Trace ({len(m.tool_events)})", id=trace_id)
        await tabs.add_pane(trace_pane)
        diffs_pane = TabPane(f"Diffs ({len(m.file_diffs)})", id=diffs_id)
        await tabs.add_pane(diffs_pane)
        if defer_tabs:
            self._deferred_tabs[trace_id] = ("trace", idx, vote_bars)
            self._deferred_tabs[diffs_id] = ("diffs", idx, vote_bars)
        else:
            await self._mount_trace_content(trace_pane, m.tool_events)
            await self._mount_diffs_content(diffs_pane, idx, vote_bars)

    async def _mount_diffs_content(
        self, pane: TabPane, idx: int, vote_bars: bool = False,
    ) -> None:
        m = self.models[idx]
        letter = model_letter(idx)
        dw: list = []
        if vote_bars:
            dw.append(self._voting.vote_bar(idx, "code"))
        if m.file_diffs:
            dw.extend(
                LazyCollapsible.for_diff(fd.filename, fd.diff, letter, classes="inner")
                for fd in m.file_diffs
            )
        elif m.diff.strip():
            dw.append(DiffDisplay(strip_diff_preamble(m.diff), letter, f"diff-{pane.id}"))
        else:
            dw.append(Static("No diff available.", classes="status"))
        await pane.mount_all(dw)

    async def _populate_model(self, idx: int) -> None:
        if idx in self._populated_models:
            return
        self._populated_models.add(idx)
        mid = model_id(idx)
        container = self.query_one(f"#{mid}", ScrollableContainer)
        header = container.query_one(f"#{model_header_id(mid)}", Static)
        header.update(self._model_header_label(idx))
        await self.handler.populate_model(container, idx)
        # Re-apply rank colors (header text update must not leave plain primary).
        self._update_scoreboard()

    async def _populate_overview(self) -> None:
        """Lazily compose the overview panel on first switch."""
        if self._overview_populated:
            return
        self._overview_populated = True

        container = self.query_one(f"#{ids.OVERVIEW}", ScrollableContainer)
        history = self._get_history()
        tabs = TabbedContent(id=ids.TABS_OVERVIEW)
        await container.mount(tabs)

        current_pane = TabPane("Current", id=ids.TAB_CURRENT)
        await tabs.add_pane(current_pane)
        await self.handler.populate_overview(current_pane)

        for label, tab_id, deferred_key in self.handler.extra_overview_tabs():
            extra_pane = TabPane(label, id=tab_id)
            await tabs.add_pane(extra_pane)
            self._deferred_tabs[tab_id] = deferred_key

        if history:
            await self._register_history_tabs(tabs, history)



    async def _register_history_tabs(
        self, tabs: TabbedContent, history: list, tab_offset: int = 0,
    ) -> None:
        """Create empty history tab shells and defer their content to activation."""
        tab_idx = tab_offset

        for orig_idx in range(len(history) - 1, -1, -1):
            entry = history[orig_idx]
            prev = history[orig_idx - 1] if orig_idx > 0 else None
            entry_fb = feedback_for_entry(history, orig_idx)

            changed = True if prev is None else self.handler.has_changes(prev, entry)

            if not changed and not entry_fb:
                continue

            level = entry.get("reviewLevel", "?")
            prev = history[orig_idx - 1] if orig_idx > 0 else None
            if entry.get("isEditAction") and prev is not None:
                kind = "edit"
                level = prev.get("reviewLevel", level)
            else:
                kind = "revision" if changed else "review"
            tid = tab_entry_id(tab_idx)
            pane = TabPane(f"L{level} {kind}", id=tid)
            await tabs.add_pane(pane)
            self._deferred_tabs[tid] = ("history", orig_idx, changed)
            tab_idx += 1

    async def _populate_history_entry(self, pane: TabPane, orig_idx: int, changed: bool) -> None:
        """Populate a single history tab on first activation."""
        history = self._get_history()
        entry = history[orig_idx]
        prev = history[orig_idx - 1] if orig_idx > 0 else None
        entry_fb = feedback_for_entry(history, orig_idx)

        widgets: list = []
        widgets.append(self.handler.history_header(entry, orig_idx))

        for fb in entry_fb:
            ts = fb.get("timestamp", "")
            ts_label = format_timestamp(ts) if ts else "unknown"
            fb_email = fb.get("email", "")
            if self._show_emails and fb_email:
                fb_title = f"Feedback | {ts_label} | {fb_email}"
            else:
                fb_title = f"Feedback | {ts_label}"
            c = Collapsible(title=fb_title, collapsed=False, classes="inner feedback-entry")
            c._fb_email = fb_email
            c._fb_ts_label = ts_label
            widgets.append(c)

        diff_statics = self.handler.history_diff_widgets(prev, entry) if changed else []

        diff_c = None
        if diff_statics:
            diff_c = Collapsible(title="Changes", collapsed=False, classes="history-diff")
            widgets.append(diff_c)

        widgets.extend(self.handler.history_detail_widgets(entry, changed))

        await pane.mount_all(widgets)

        for fb_c, fb in zip(pane.query(".inner"), entry_fb, strict=False):
            await fb_c.query_one(Collapsible.Contents).mount(
                Static(RichMarkdown(fb.get("message", "No message.")))
            )

        if diff_c:
            await diff_c.query_one(Collapsible.Contents).mount_all(diff_statics)

    def _status(self, msg: str) -> None:
        try:
            self.query_one(f"#{ids.STATUS_BAR}", Static).update(msg)
        except Exception:
            return
        if self._status_timer is not None:
            self._status_timer.stop()
        self._status_timer = self.set_timer(4, self._clear_status)

    def _clear_status(self) -> None:
        try:
            self.query_one(f"#{ids.STATUS_BAR}", Static).update("")
        except Exception:
            pass

    async def on_mount(self) -> None:
        self._record_session()
        if self.task_type == TaskType.UNKNOWN:
            self._status(f"Loaded task {self.task_id} (unsupported type)")
            self._maybe_show_tutorial()
            return
        await self._populate_overview()
        # Apply rank colors from history/local scores once the DOM is up.
        self._update_scoreboard()
        n = self.handler.model_count
        self._status(f"Loaded task {self.task_id}" + (f" ({n} models)" if n else ""))
        self._maybe_show_tutorial()

    def _record_session(self) -> None:
        """Record this task visit in local session history."""
        session = TaskSession(
            task_id=self.task_id,
            task_type=self.task_type.value,
            repository=self.parsed.repository or "",
        )
        SessionHistory().record(session)

    async def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
        c = event.collapsible
        if not isinstance(c, LazyCollapsible):
            return
        lazy = c.lazy

        # Lazy diff loading
        if lazy.diff is not None:
            diff_text = lazy.diff
            lazy.diff = None  # consume
            await self._mount_into(c, DiffDisplay(diff_text, lazy.letter, lazy.filename))
            self._search.flush_pending_grep()
            return

        # Lazy trace event loading
        if lazy.events and not lazy.populated:
            lazy.populated = True
            dict_events = list(lazy.events)
            collapsibles: list[Collapsible] = []
            for ev in dict_events:
                ev_name = clean_event_name(ev.name)
                type_cls = f"trace-t{self._trace_type_index(ev_name)}"
                inner_c = Collapsible(
                    title=format_event_line(ev),
                    collapsed=True,
                    classes=f"trace-event-c inner {type_cls}",
                )
                collapsibles.append(inner_c)
            # Mount first so Contents exists, then populate details
            await c.query_one(Collapsible.Contents).mount_all(collapsibles)
            for inner_c, ev in zip(collapsibles, dict_events, strict=True):
                detail = trace_event_detail_widgets(ev)
                if detail:
                    await self._mount_into(inner_c, *detail)

    async def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if (
            self._overview_populated
            and event.tabbed_content.id == ids.TABS_OVERVIEW
            and str(event.pane.id) != ids.TAB_CURRENT
        ):
            self._editor.show_justification_preview()
        pane_id = str(event.pane.id)
        deferred = self._deferred_tabs.pop(pane_id, None)
        if deferred:
            kind = deferred[0]
            if kind == "trace":
                _, idx, vote_bars = deferred
                await self._mount_trace_content(event.pane, self.models[idx].tool_events)
            elif kind == "diffs":
                _, idx, vote_bars = deferred
                await self._mount_diffs_content(event.pane, idx, vote_bars)
            elif kind == "history":
                _, orig_idx, changed = deferred
                await self._populate_history_entry(event.pane, orig_idx, changed)
            elif kind == "proposal-trace" and self.proposal:
                await self._mount_trace_content(
                    event.pane, self.proposal.tool_events,
                    model_id=self.proposal.model_id,
                    bash_history=self.proposal.bash_history,
                    setup_commands=self.proposal.setup_commands,
                )
            elif kind == "proposal-diffs" and self.proposal:
                await event.pane.mount_all([
                    LazyCollapsible.for_diff(fd.filename, fd.diff, "S", classes="inner")
                    for fd in self.proposal.file_diffs
                ])

    @property
    def _current_section(self) -> str | None:
        """Return the active section ID from the content switcher, or None."""
        try:
            c = self.query_one(f"#{ids.MAIN_SWITCHER}", ContentSwitcher).current
            return str(c) if c is not None else None
        except Exception:
            return None

    def _is_on_model_view(self) -> bool:
        s = self._current_section
        if s == ids.UNIFIED_VIEW:
            return self._split_focus is not None and self._split_focus >= 0
        return s is not None and s.startswith("model-")

    def _is_on_overview(self) -> bool:
        if self._current_section == ids.UNIFIED_VIEW:
            return self._split_focus == -1
        return self._current_section == ids.OVERVIEW

    def _split_model_count(self) -> int:
        return self.handler.model_count

    def _update_split_focus(self) -> None:
        for idx in range(self._split_model_count()):
            try:
                col = self.query_one(f"#split-{model_id(idx)}", Vertical)
                if self._split_focus == idx:
                    col.add_class("split-active")
                else:
                    col.remove_class("split-active")
            except Exception:
                pass
        try:
            ov = self.query_one("#unified-overview", ScrollableContainer)
            if self._split_focus == -1:
                ov.add_class("split-active")
            else:
                ov.remove_class("split-active")
        except Exception:
            pass

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in self.handler.hidden_actions():
            return False
        if action == "go_model" and parameters:
            idx = parameters[0]
            if isinstance(idx, int) and idx >= len(self.models):
                return False

        handler_result = self.handler.check_action(action, parameters)
        if handler_result is not None:
            return handler_result

        if action == "split_view":
            return self.handler.supports_split
        if action in ("vote_up", "vote_down", "search_diffs"):
            return self._is_on_model_view()
        if action == "translate":
            return True
        if action == "edit_justification":
            return self._is_on_overview()
        if action == "toggle_response_width":
            return self.task_type == TaskType.ARENA_RANKING
        if action == "toggle_collapse":
            return self._is_on_model_view() or self._is_on_overview()
        return True

    async def go_to(self, section_id: str) -> None:
        if self.task_type == TaskType.UNKNOWN:
            return
        if self._current_section == ids.UNIFIED_VIEW and section_id == ids.OVERVIEW:
            self._split_focus = -1
            self._update_split_focus()
            if self._maximized:
                self._apply_split_maximize_display()
            self.refresh_bindings()
            try:
                self.query_one("#unified-overview", ScrollableContainer).focus()
            except Exception:
                pass
            return
        if self._current_section == ids.UNIFIED_VIEW and section_id.startswith("model-"):
            for i in range(self._split_model_count()):
                if model_id(i) == section_id:
                    await self._focus_unified_model(i)
                    return
            return
        if self._current_section == ids.UNIFIED_VIEW and section_id != ids.UNIFIED_VIEW:
            if self._maximized:
                self._restore_maximize()
            self._split_focus = None
        self.query_one(f"#{ids.MAIN_SWITCHER}", ContentSwitcher).current = section_id
        self.refresh_bindings()
        if section_id == ids.OVERVIEW:
            await self._populate_overview()
            try:
                self.query_one(f"#{ids.OVERVIEW}", ScrollableContainer).focus()
            except Exception:
                pass
            return
        for i in range(self.handler.model_count):
            if model_id(i) == section_id:
                self.current_model_index = i
                await self._populate_model(i)
                try:
                    self.query_one(f"#{model_id(i)}", ScrollableContainer).focus()
                except Exception:
                    pass
                return

    async def go_to_diff(
        self, model_index: int, filename: str, grep_line: str | None = None,
    ) -> None:
        await self._search.go_to_diff(model_index, filename, grep_line)

    async def action_go_to(self, section_id: str) -> None:
        await self.go_to(section_id)

    async def action_split_view(self) -> None:
        if not self.handler.supports_split:
            return
        if self._current_section == ids.UNIFIED_VIEW:
            if self._maximized:
                self._restore_maximize()
            self._split_focus = None
            self.query_one(f"#{ids.MAIN_SWITCHER}", ContentSwitcher).current = ids.OVERVIEW
            self.refresh_bindings()
            return
        await self._populate_unified_view()
        self._split_focus = 0
        self.query_one(f"#{ids.MAIN_SWITCHER}", ContentSwitcher).current = ids.UNIFIED_VIEW
        self._update_split_focus()
        self.refresh_bindings()

    async def _populate_unified_view(self) -> None:
        if self._split_populated:
            return
        self._split_populated = True

        for idx in range(self.handler.model_count):
            mid = model_id(idx)
            header = self.query_one(f"#split-header-{mid}", Static)
            header.update(self._model_header_label(idx))
            scroll = self.query_one(f"#split-scroll-{mid}", ScrollableContainer)
            await self.handler.populate_unified_model(scroll, idx)
        self._update_scoreboard()

        overview = self.query_one("#unified-overview", ScrollableContainer)
        history = self._get_history()
        tabs = TabbedContent(id="unified-overview-tabs")
        await overview.mount(tabs)

        current_pane = TabPane("Current", id="unified-ov-current")
        await tabs.add_pane(current_pane)
        await self.handler.populate_unified_overview(current_pane)

        if history:
            await self._register_history_tabs(tabs, history, tab_offset=30)

    def _prompt_source(self) -> str:
        return self.handler.prompt_source()

    def _response_source(self, idx: int) -> str:
        return self.handler.response_source(idx)

    def _response_display_text(self, idx: int, raw: str) -> str:
        """Apply handler presentation (e.g. CQ viewport markers) to response text."""
        return self.handler.prepare_response_text(idx, raw)

    def _apply_translations(self) -> None:
        swap = self.call_from_thread
        if "prompt" in self._translated:
            swap(self._swap_widget, "prompt-text", self._translated["prompt"], 0)
        for idx in range(self.handler.model_count):
            if idx in self._translated:
                body = self._response_display_text(idx, self._translated[idx])
                swap(self._swap_widget, f"response-text-{idx}", body)
                swap(self._swap_widget, f"split-response-{idx}", body)
        self.handler.apply_translated_extras(self._translated)

    def _restore_originals(self) -> None:
        swap = self.call_from_thread
        prompt = self._prompt_source()
        if prompt.strip():
            swap(self._swap_widget, "prompt-text", prompt, 0)
        for idx in range(self.handler.model_count):
            if idx < len(self.models):
                raw = self._model_summary_text(self.models[idx])
            else:
                raw = self._response_source(idx)
            body = self._response_display_text(idx, raw)
            swap(self._swap_widget, f"response-text-{idx}", body)
            swap(self._swap_widget, f"split-response-{idx}", body)
        self.handler.restore_extras()

    @work(thread=True)
    def action_translate(self) -> None:
        if self._translate_on:
            self._translate_on = False
            self._translate_lang = None
            self._restore_originals()
            self.call_from_thread(self._update_sub_title)
            self.call_from_thread(self._status, "Originals restored")
            return

        target = _system_language()
        self.call_from_thread(self._status, f"Translating to {target}...")
        try:
            from deep_translator import GoogleTranslator

            prompt = self._prompt_source()
            if prompt.strip() and "prompt" not in self._translated:
                self._translated["prompt"] = _translate_text(prompt, GoogleTranslator, target)
            for idx in range(self.handler.model_count):
                if idx not in self._translated:
                    source = self._response_source(idx)
                    if source.strip():
                        self._translated[idx] = _translate_text(
                            source, GoogleTranslator, target,
                        )
                    else:
                        self._translated[idx] = ""
            for key, text in self.handler.translatable_extras():
                if key not in self._translated:
                    self._translated[key] = _translate_text(text, GoogleTranslator, target)
        except Exception as e:
            self.call_from_thread(self._status, f"Translation failed: {e}")
            return

        self._translate_on = True
        self._translate_lang = target
        self._apply_translations()
        self.call_from_thread(self._update_sub_title)
        self.call_from_thread(self._status, f"Translated to {target}")

    def copy_to_clipboard(self, text: str) -> None:
        import base64
        import os
        import shutil
        import subprocess

        self._clipboard = text
        for cmd in ("pbcopy", "xclip -selection clipboard",
                     "xsel --clipboard --input", "wl-copy"):
            prog = cmd.split()[0]
            if shutil.which(prog):
                try:
                    subprocess.run(
                        cmd.split(), input=text.encode(), check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    return
                except subprocess.SubprocessError:
                    pass
        b64 = base64.b64encode(text.encode()).decode()
        osc = f"\x1b]52;c;{b64}\a"
        if os.environ.get("TMUX"):
            osc = f"\x1bPtmux;\x1b{osc}\x1b\\"
        if self._driver:
            self._driver.write(osc)

    def _swap_widget(self, widget_id: str, text: str, heading_level: int = 4) -> None:
        try:
            self.query_one(f"#{widget_id}", Static).update(
                RichMarkdown(bump_headings(text, heading_level))
            )
        except Exception:
            pass

    async def _focus_unified_model(self, index: int) -> None:
        """Focus a model column in the unified view (honours maximize)."""
        self._split_focus = index
        self.current_model_index = index
        self._update_split_focus()
        if self._maximized:
            self._apply_split_maximize_display()
        self.refresh_bindings()
        mid = model_id(index)
        try:
            self.query_one(f"#split-scroll-{mid}", ScrollableContainer).focus()
        except Exception:
            pass

    def _ensure_content_for_model_nav(self) -> None:
        """Make model content visible when navigating with 1/2/3 while maximized."""
        if not self._maximized:
            return
        try:
            # Info-bar maximize hides the content area — show it for model nav.
            self.query_one(f"#{ids.CONTENT_AREA}").display = True
            self.query_one(f"#{ids.INFO_BAR}").display = False
            self.query_one(f"#{ids.SPLIT_HANDLE}").display = False
        except Exception:
            pass

    async def action_go_model(self, index: int) -> None:
        """Focus model A/B/C consistently in normal, unified, and maximized views."""
        if not (0 <= index < len(self.models)):
            return
        if self._current_section == ids.UNIFIED_VIEW:
            await self._focus_unified_model(index)
            return
        self._ensure_content_for_model_nav()
        keep_max = self._maximized
        await self.go_to(model_id(index))
        if keep_max:
            # Stay maximized on the chosen model (info bar remains hidden).
            try:
                self.query_one(f"#{ids.INFO_BAR}").display = False
                self.query_one(f"#{ids.SPLIT_HANDLE}").display = False
                self.query_one(f"#{ids.CONTENT_AREA}").display = True
            except Exception:
                pass
            self._maximized = True

    async def action_go_model_proposal(self) -> None:
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            await self.go_to(model_id(0))

    def _active_tabbed_content(self) -> TabbedContent | None:
        """Return the TabbedContent widget in the currently visible view."""
        section = self._current_section
        if not section:
            return None
        if section == ids.UNIFIED_VIEW:
            if self._split_focus is not None and self._split_focus >= 0:
                mid = model_id(self._split_focus)
                try:
                    return self.query_one(f"#split-tabs-{mid}", TabbedContent)
                except Exception:
                    return None
            if self._split_focus == -1:
                try:
                    return self.query_one("#unified-overview-tabs", TabbedContent)
                except Exception:
                    return None
            return None
        tid = ids.TABS_OVERVIEW if section == ids.OVERVIEW else model_tabs_id(section)
        try:
            return self.query_one(f"#{tid}", TabbedContent)
        except Exception:
            return None

    def _active_tabs_widget(self):
        from textual.widgets import Tabs

        tc = self._active_tabbed_content()
        if tc:
            try:
                return tc.query_one(Tabs)
            except Exception:
                pass
        return None

    def action_next_tab(self) -> None:
        t = self._active_tabs_widget()
        if t:
            t.action_next_tab()

    def action_prev_tab(self) -> None:
        t = self._active_tabs_widget()
        if t:
            t.action_previous_tab()

    def _focus_in_info_bar(self) -> bool:
        widget = self.focused
        while widget is not None and widget is not self:
            if widget.id == ids.INFO_BAR:
                return True
            widget = widget.parent
        return False

    def action_toggle_maximize(self) -> None:
        if self._maximized:
            self._restore_maximize()
            return
        if self._focus_in_info_bar():
            self._maximize_info()
        elif self._current_section == ids.UNIFIED_VIEW:
            self._maximize_split()
        else:
            self._maximize_normal()

    def _maximize_info(self) -> None:
        try:
            self.query_one(f"#{ids.CONTENT_AREA}").display = False
            self.query_one(f"#{ids.SPLIT_HANDLE}").display = False
        except Exception:
            return
        self._maximized = True

    def _maximize_normal(self) -> None:
        try:
            self.query_one(f"#{ids.INFO_BAR}").display = False
            self.query_one(f"#{ids.SPLIT_HANDLE}").display = False
        except Exception:
            return
        self._maximized = True

    def _apply_split_maximize_display(self) -> None:
        """Show only the focused unified column/overview while maximized."""
        focus = self._split_focus
        if focus is None:
            return
        for idx in range(self._split_model_count()):
            try:
                col = self.query_one(f"#split-{model_id(idx)}", Vertical)
                col.display = idx == focus
            except Exception:
                pass
        try:
            responses = self.query_one(".unified-responses", Horizontal)
            responses.display = focus >= 0
        except Exception:
            pass
        try:
            ov = self.query_one("#unified-overview", ScrollableContainer)
            ov.display = focus == -1
        except Exception:
            pass

    def _maximize_split(self) -> None:
        if self._split_focus is None:
            return
        try:
            self.query_one(f"#{ids.INFO_BAR}").display = False
            self.query_one(f"#{ids.SPLIT_HANDLE}").display = False
        except Exception:
            pass
        self._apply_split_maximize_display()
        self._maximized = True

    def _restore_maximize(self) -> None:
        try:
            self.query_one(f"#{ids.INFO_BAR}").display = True
            self.query_one(f"#{ids.CONTENT_AREA}").display = True
            self.query_one(f"#{ids.SPLIT_HANDLE}").display = True
        except Exception:
            pass
        try:
            self.query_one(".unified-responses", Horizontal).display = True
        except Exception:
            pass
        for idx in range(self._split_model_count()):
            try:
                self.query_one(f"#split-{model_id(idx)}", Vertical).display = True
            except Exception:
                pass
        try:
            self.query_one("#unified-overview", ScrollableContainer).display = True
        except Exception:
            pass
        self._maximized = False

    def action_toggle_collapse(self) -> None:
        tc = self._active_tabbed_content()
        if tc is None:
            return
        try:
            pane = tc.get_pane(tc.active)
        except Exception:
            return
        collapsibles = list(pane.query(Collapsible))
        if not collapsibles:
            return
        # If any are expanded, collapse all; otherwise expand all
        any_expanded = any(not c.collapsed for c in collapsibles)
        for c in collapsibles:
            c.collapsed = any_expanded

    def set_theme(self, theme_name: str) -> None:
        self.theme = theme_name
        update_config(theme=theme_name)
        self._status(f"Theme: {theme_name}")

    def _current_model(self) -> ModelData | None:
        idx = self.current_model_index
        return self.models[idx] if 0 <= idx < len(self.models) else None

    def action_search_diffs(self) -> None:
        self._search.search_diffs()

    def action_search_events(self) -> None:
        self._search.search_events()

    def action_yank_file(self) -> None:
        self._editor.yank_file()

    def on_diff_display_vote_requested(self, event: DiffDisplay.VoteRequested) -> None:
        idx = self.current_model_index
        if idx < 0 or idx >= len(self.models):
            return
        self._voting.apply_vote(idx, Context.CODE, event.delta)

    def on_diff_display_yank_requested(self, event: DiffDisplay.YankRequested) -> None:
        self._editor.yank_file()

    def add_annotation(self, model_index: int, annotation: object) -> None:
        """Public API for adding annotations — delegates to VotingController."""
        self._voting.add_annotation(model_index, annotation)

    def _detect_vote_context(self) -> str:
        return self._voting.detect_vote_context()

    def _save_summary(self, text: str) -> None:
        self._editor.save_summary(text)

    def _refresh_overview_annotations(self) -> None:
        self._editor.refresh_overview_annotations()

    def action_vote_up(self) -> None:
        self._voting.vote(1)

    def action_vote_down(self) -> None:
        self._voting.vote(-1)

    def action_reset_local(self) -> None:
        self.review.reset(len(self.models), self._get_history())
        self.scores = self.review.scores
        self._editor.refresh_overview_annotations()
        self._status("Reset to server state.")

    def _session_ms(self) -> int:
        history = self.data.get("history", [])
        entry = history[-1] if history else {}
        ms = 0
        for s in (entry.get("finalUserTaskSessionTimes") or []):
            try:
                ms += int(s.get("endTime", 0)) - int(s.get("startTime", 0))
            except (ValueError, TypeError):
                pass
        return ms

    def rankings_summary(self) -> str:
        lead = self._session_ms()
        lead_part = f"[bold]Lead: {format_duration(lead)}[/bold]" if lead > 0 else ""
        parts: list[str] = []
        parts.extend(self.handler.scoreboard_parts())
        if lead_part:
            parts.append(lead_part)
        return "  |  ".join(parts)

    def _update_scoreboard(self) -> None:
        try:
            board = self.query_one(f"#{ids.SCOREBOARD}", Static)
        except Exception:
            return
        board.update(self.rankings_summary())
        self._apply_model_header_colors()

    def _apply_model_header_colors(self) -> None:
        """Tint model headers green/yellow/red from local votes or last ranking."""
        has_local_votes = any(s.any_nonzero() for s in self.scores)
        history = self.data.get("history", [])
        n = len(self.models)

        for idx in range(n):
            label = self.handler.model_header_label(idx)
            bg = None
            if has_local_votes:
                rank = ranking.model_rank(self.scores, idx)
                if rank is not None:
                    bg = rank_color(rank, n)
            else:
                prev_rank = ranking.previous_model_rank(history, idx)
                if prev_rank is not None:
                    bg = rank_color(prev_rank, n)
            mid = model_id(idx)
            for hid in (model_header_id(mid), f"split-header-{mid}"):
                h = self.query_one_optional(f"#{hid}", Static)
                if h is None:
                    continue
                h.update(label)
                if bg is not None:
                    h.styles.background = bg
                else:
                    h.styles.background = None

    @staticmethod
    def _model_summary_text(m: ModelData) -> str:
        return ranking.model_summary_text(m)

    async def action_edit_justification(self) -> None:
        await self._editor.edit_justification()

    def action_add_comment(self) -> None:
        self._editor.add_comment()

    def action_edit_comments(self) -> None:
        self._editor.edit_comments()

    def action_copy_comments(self) -> None:
        self._editor.copy_comments()

    def on_click(self, event: Click) -> None:
        if self._current_section != ids.UNIFIED_VIEW:
            return
        widget = event.widget
        while widget is not None and widget is not self:
            wid = widget.id or ""
            if wid == "unified-overview":
                if self._split_focus != -1:
                    self._split_focus = -1
                    self._update_split_focus()
                    self.refresh_bindings()
                    widget.focus()
                return
            if wid.startswith("split-") and hasattr(widget, "has_class") and widget.has_class("split-col"):
                for idx in range(self._split_model_count()):
                    if wid == f"split-{model_id(idx)}":
                        if self._split_focus != idx:
                            self._split_focus = idx
                            self.current_model_index = idx
                            self._update_split_focus()
                            self.refresh_bindings()
                            try:
                                self.query_one(
                                    f"#split-scroll-{model_id(idx)}", ScrollableContainer,
                                ).focus()
                            except Exception:
                                pass
                        return
            widget = widget.parent

    def on_key(self, event) -> None:
        """Escape exits editors and view modes (maximize, response width, ...)."""
        if event.key != "escape":
            return
        if self._editor.handle_escape_from_editor(event):
            return
        if self._exit_view_modes():
            event.prevent_default()
            event.stop()

    def _exit_view_modes(self) -> bool:
        """Leave transient view modes. Returns True if anything was exited."""
        restored: list[str] = []
        if self._maximized:
            self._restore_maximize()
            restored.append("fullscreen")
        from sfctl.handlers.arena import ArenaHandler

        if isinstance(self.handler, ArenaHandler) and self.handler.clear_response_width():
            restored.append("80-col preview")
        if not restored:
            return False
        self._status("Restored " + ", ".join(restored))
        self.refresh_bindings()
        return True

    _HELP_TEXT = (
        "[bold]Navigation[/bold]\n"
        "  1/2/3      switch to model A/B/C\n"
        "  0          overview (review, history, feedback)\n"
        "  tab        next tab within a view\n"
        "  shift+tab  previous tab\n"
        "  e          toggle expand/collapse all in current tab\n\n"
        "[bold]Review[/bold]\n"
        "  +/-        vote (diff=code, response=response, else=overall)\n"
        "  y          yank diff snippet into justification\n"
        "  n          add a reviewer comment (note)\n\n"
        "[bold]Search[/bold]\n"
        "  ctrl+f     search files (repeat to toggle fuzzy/grep)\n"
        "  ctrl+g     search events (repeat to toggle fuzzy/grep)\n\n"
        "[bold]View[/bold]\n"
        "  u          toggle unified side-by-side view\n"
        "  f          toggle maximize / restore focused pane\n"
        "  t          toggle translate to system locale\n"
        "  w          preview response at 80-column terminal width (arena)\n"
        "             centers the response in a framed 80-col box so you can\n"
        "             judge wrapping/density as in a typical terminal; Esc exits\n"
        "  escape     exit editor / fullscreen / 80-col preview\n\n"
        "[bold]Actions[/bold]\n"
        "  ctrl+e     edit summary\n"
        "  ctrl+n     edit comments\n"
        "  c          copy review to clipboard\n"
        "  C          copy comments to clipboard\n"
        "  r          refresh data from API\n"
        "  ctrl+r     reset local annotations and scores\n"
        "  q          quit"
    )

    _TUTORIAL_TEXT = (
        "[bold]Welcome to Starfleet Control[/bold]\n\n"
        "You're reviewing model outputs for a coding task.\n"
        "Three models (A/B/C) each attempted the same prompt.\n"
        "Your job: compare their work and build a structured review.\n\n"
        "[bold]Navigate[/bold]\n"
        "  1/2/3  switch between model views\n"
        "  0      open the overview (your review, history, feedback)\n"
        "  tab    cycle tabs within a view (Response / Trace / Diffs)\n\n"
        "[bold]Score[/bold]\n"
        "  +/-    vote on the current context\n"
        "         on a diff tab -> scores code quality\n"
        "         on response tab -> scores response quality\n"
        "         otherwise -> scores overall\n"
        "  The scoreboard updates live with your rankings.\n\n"
        "[bold]Justification[/bold]\n"
        "  y      yank a diff snippet into your justification\n"
        "         select lines first, or yank the whole file\n"
        "  ctrl+e edit your justification (overview tab)\n"
        "  c      copy review to clipboard\n\n"
        "Press ? for the full shortcut reference.\n"
        "Press escape to dismiss."
    )

    def action_toggle_emails(self) -> None:
        from sfctl.history import format_history_entry

        self._show_emails = not self._show_emails
        history = self._get_history()
        for widget in self.query(".history-meta"):
            idx = getattr(widget, "_entry_idx", None)
            if idx is not None and idx < len(history):
                widget.update(format_history_entry(history[idx], idx, show_email=self._show_emails))
        for widget in self.query(".comment-meta"):
            email = getattr(widget, "_comment_email", "unknown")
            ts = getattr(widget, "_comment_ts", "")
            content = getattr(widget, "_comment_content", "")
            author = email if self._show_emails else "reviewer"
            widget.update(f"\n[dim]{author} ({ts}):[/dim]\n{content}")
        for widget in self.query(".feedback-entry"):
            fb_email = getattr(widget, "_fb_email", "")
            ts_label = getattr(widget, "_fb_ts_label", "")
            if fb_email:
                if self._show_emails:
                    widget.title = f"Feedback | {ts_label} | {fb_email}"
                else:
                    widget.title = f"Feedback | {ts_label}"
        self._update_sub_title()
        self._status("Emails visible" if self._show_emails else "Emails hidden")

    def action_help(self) -> None:
        self.push_screen(HelpModal(self._HELP_TEXT, "Keyboard Shortcuts"))

    def action_toggle_response_width(self) -> None:
        """Toggle 80-column terminal-width preview on model responses (arena)."""
        from sfctl.cq_viewport import RESPONSE_TERMINAL_WIDTH
        from sfctl.handlers.arena import ArenaHandler

        if not isinstance(self.handler, ArenaHandler):
            return
        narrow = self.handler.toggle_response_width()
        if narrow:
            self._status(
                f"80-col preview on — response framed at {RESPONSE_TERMINAL_WIDTH} "
                "columns (Esc or w to exit)"
            )
        else:
            self._status("80-col preview off — response uses full pane width")

    def _maybe_show_tutorial(self) -> None:
        config = load_config()
        if config.get("tutorial_seen"):
            return
        update_config(tutorial_seen=True)
        self.push_screen(HelpModal(self._TUTORIAL_TEXT, "Welcome"))

    @work(thread=True)
    def action_refresh_data(self) -> None:
        from sfctl.api import fetch_data

        if not self.cookies:
            self.call_from_thread(self._status, "No session available.")
            return
        self.call_from_thread(self._status, "Refreshing...")
        try:
            new_data = fetch_data(self.task_arg, self.cookies)
        except Exception as e:
            self.call_from_thread(self._status, f"Refresh failed: {e}")
            return
        self.data = new_data
        self.task_type = detect_task_type(new_data)
        self.handler = handler_for_type(self.task_type, self, new_data)
        self.parsed, self.models = self.handler.parse()
        self.task_id = (
            new_data.get("task", {}).get("taskId") or self.parsed.task_id or self.task_id
        )
        self.review.reload(self.task_id, len(self.models), self._get_history())
        self.scores = self.review.scores
        self._trace_type_map = {}
        self._update_sub_title()
        self.refresh(recompose=True)
        self.call_later(self._post_refresh_populate)

    @work
    async def _post_refresh_populate(self) -> None:
        if self.task_type != TaskType.UNKNOWN:
            await self._populate_overview()
        self._update_scoreboard()
        self._status("Data refreshed.")

    def action_copy_summary(self) -> None:
        self._editor.copy_summary()

    async def action_quit(self) -> None:
        self._editor.save_summary_from_editor()
        self.exit()
