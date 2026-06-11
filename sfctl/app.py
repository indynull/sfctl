"""Main Starfleet TUI application."""

from rich.markdown import Markdown as RichMarkdown
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Button,
    Collapsible,
    ContentSwitcher,
    Footer,
    Header,
    Link,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from sfctl import ids, ranking
from sfctl.commands import NavigationProvider
from sfctl.config import get_web_url, load_config, update_config
from sfctl.constants import ARROW_DOWN, ARROW_UP, EM_DASH
from sfctl.ids import (
    Context,
    model_header_id,
    model_id,
    model_letter,
    model_tabs_id,
    tab_diffs_id,
    tab_entry_id,
    tab_response_id,
    tab_trace_id,
    vote_down_id,
    vote_label_id,
    vote_up_id,
)
from sfctl.models import Annotation, ModelData, ModelScores, ParsedContent
from sfctl.parsing import (
    bump_headings,
    clean_event_name,
    diff_line_ref,
    feedback_for_entry,
    format_event_line,
    format_history_entry,
    format_timestamp,
    group_events,
    has_meaningful_changes,
    history_justification,
    history_justification_texts,
    history_ranking_changes,
    parse_content,
    rank_color,
    strip_diff_preamble,
    trace_type_color,
)
from sfctl.scoring import (
    annotations_path,
    justification_path,
    load_annotations,
    save_annotations,
    scores_from_annotations,
    scores_path,
)
from sfctl.screens import (
    DiffSearchModal,
    HelpModal,
    YankCommentModal,
    build_clipboard_text,
)
from sfctl.task_types import TaskType, detect_task_type
from sfctl.widgets import DiffDisplay, LazyCollapsible, trace_event_detail_widgets


class StarfleetApp(App):
    TITLE = "Starfleet Control"
    COMMANDS = {NavigationProvider}
    CSS_PATH = "app.tcss"

    current_model_index: reactive[int] = reactive(0)
    scores: reactive[list[ModelScores]] = reactive(list, always_update=True)

    def watch_scores(self, new_scores: list[ModelScores]) -> None:
        if not hasattr(self, "_overview_populated") or not self.is_running:
            return
        self._update_scoreboard()
        self._refresh_overview_annotations()
        for idx in range(len(new_scores)):
            self._refresh_vote_labels(idx)

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("1", "go_model(0)", "A", show=True),
        Binding("2", "go_model(1)", "B", show=True),
        Binding("3", "go_model(2)", "C", show=True),
        Binding("0", "go_to('overview')", "Overview", show=True),
        Binding("ctrl+e", "edit_justification", "Edit", show=True),
        Binding("+", "vote_up", f"{ARROW_UP} Up", show=True),
        Binding("-", "vote_down", f"{ARROW_DOWN} Down", show=True),
        Binding("ctrl+f", "search_diffs", "Find File", show=True),
        Binding("c", "copy_summary", "Copy", show=True),
        Binding("e", "toggle_collapse", "Fold", show=True),
        Binding("?", "help", "Help", show=True),
        Binding("y", "yank_file", "Yank"),
        Binding("r", "refresh_data", "Refresh"),
        Binding("ctrl+r", "reset_local", "Reset"),
        Binding("tab", "next_tab", "Next Tab", show=False, priority=True),
        Binding("shift+tab", "prev_tab", "Prev Tab", show=False, priority=True),
    ]

    def __init__(self, task_arg: str, data: dict, cookies: dict[str, str] | None = None):
        super().__init__()
        self.task_arg = task_arg
        self.data = data
        self.cookies = cookies
        self.parsed: ParsedContent = parse_content(data.get("content", {}))
        self.task_id = data.get("task", {}).get("taskId") or self.parsed.task_id or task_arg
        self.models: list[ModelData] = self.parsed.models
        history = data.get("history", [])
        self.annotations: list[list[Annotation]]
        self.summary_text: str
        self.annotations, self.summary_text = load_annotations(
            self.task_id, len(self.models), history
        )
        self.scores: list[ModelScores] = scores_from_annotations(self.annotations)
        self._populated_models: set[int] = set()
        self._overview_populated = False
        self._trace_type_map: dict[str, int] = {}

        task = data.get("task", {})
        email = (task.get("actionHistory") or [{}])[0].get("userId", "")
        if email and email != EM_DASH:
            self.sub_title = f"Task {self.task_id}  |  {email}"
        else:
            self.sub_title = f"Task {self.task_id}"

        self.task_type = detect_task_type(data)

        config = load_config()
        if "theme" in config:
            self.theme = config["theme"]

    def nav_items(self) -> list[tuple[str, str]]:
        return ranking.nav_items(self.models)

    def diff_items(self) -> list[tuple[str, int, str]]:
        return ranking.diff_items(self.models)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        repo = self.parsed.repository
        prompt = bump_headings(self.parsed.current_prompt or EM_DASH)
        self.task_url = get_web_url(f"/tasks/{self.task_id}")
        with Vertical(id=ids.INFO_BAR):
            yield Link(f"Task: {self.task_id}", url=self.task_url, id=ids.TASK_BAR)
            yield Static(self.rankings_summary(), id=ids.SCOREBOARD)
            if repo and repo != EM_DASH:
                yield Static(
                    f"[bold]Repo:[/bold] {repo.replace('[', '(').replace(']', ')')}",
                    id=ids.REPO_BAR,
                )
            with (
                Collapsible(title="Prompt", collapsed=False, id=ids.PROMPT_BAR),
                ScrollableContainer(),
            ):
                yield Static(RichMarkdown(prompt))

        yield Footer()

        self._populated_models = set()
        self._overview_populated = False

        if self.task_type == TaskType.UNKNOWN:
            with ScrollableContainer(id=ids.OVERVIEW):
                yield Static(
                    "[bold]Unsupported task type[/bold]\n\n"
                    "This task does not match a known layout. "
                    "Only the raw overview and history are available.",
                    classes="status",
                )
            return

        initial = model_id(0) if self.models else ids.OVERVIEW
        with ContentSwitcher(initial=initial, id=ids.MAIN_SWITCHER):
            for idx in range(len(self.models)):
                mid = model_id(idx)
                with ScrollableContainer(id=mid):
                    yield Static(
                        f"[bold]{model_letter(idx)}[/bold]",
                        classes="view-header",
                        id=model_header_id(mid),
                    )

            with ScrollableContainer(id=ids.OVERVIEW):
                pass  # populated lazily by _populate_overview()

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

    def _vote_bar(self, idx: int, context: str) -> Horizontal:
        """Small inline up/down vote buttons for a model tab."""
        s = self.scores[idx]
        score = getattr(s, context, 0)
        sign = f"+{score}" if score > 0 else str(score)
        return Horizontal(
            Button(f"{ARROW_UP}", id=vote_up_id(idx, context), classes="vote-btn"),
            Static(sign, classes="vote-score", id=vote_label_id(idx, context)),
            Button(f"{ARROW_DOWN}", id=vote_down_id(idx, context), classes="vote-btn"),
            classes="vote-bar",
        )

    def _refresh_vote_labels(self, idx: int) -> None:
        if idx >= len(self.scores):
            return
        s = self.scores[idx]
        for context in Context:
            label = self.query_one_optional(f"#{vote_label_id(idx, context)}", Static)
            if label:
                score = getattr(s, context, 0)
                sign = f"+{score}" if score > 0 else str(score)
                label.update(sign)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if not btn_id.startswith("vote-"):
            return
        parts = btn_id.split("-")
        if len(parts) < 4:
            return
        direction = parts[1]
        idx = int(parts[2])
        context = "-".join(parts[3:])
        delta = 1 if direction == "up" else -1
        self._apply_vote(idx, context, delta)

    async def _populate_model(self, idx: int) -> None:
        """Lazily compose a model view's content on first switch."""
        if idx in self._populated_models:
            return
        self._populated_models.add(idx)

        m = self.models[idx]
        mid = model_id(idx)
        letter = model_letter(idx)

        container = self.query_one(f"#{mid}", ScrollableContainer)

        grouped = group_events(m)
        total = sum(1 for e in m.tool_events if isinstance(e, dict))

        tabs = TabbedContent(id=model_tabs_id(mid))
        await container.mount(tabs)

        response_pane = TabPane("Response", id=tab_response_id(mid))
        await tabs.add_pane(response_pane)
        summary = self._model_summary_text(m)
        await response_pane.mount_all(
            [
                self._vote_bar(idx, "response"),
                Static(RichMarkdown(summary)),
            ]
        )

        trace_pane = TabPane(f"Trace ({total})", id=tab_trace_id(mid))
        await tabs.add_pane(trace_pane)
        if not grouped:
            await trace_pane.mount(Static("No tool events.", classes="status"))
        else:
            sorted_groups = sorted(grouped.items(), key=lambda x: -len(x[1]))
            summary_parts = []
            for ename, events in sorted_groups:
                ti = self._trace_type_index(ename)
                color = trace_type_color(ti)
                summary_parts.append(f"[{color}]{ename}[/] [dim]{len(events)}x[/]")
            trace_widgets: list[Static | LazyCollapsible] = [
                Static("  " + "  ".join(summary_parts), classes="trace-summary-row"),
            ]
            trace_c = LazyCollapsible.for_trace(
                title=f"Event Details ({total} events)",
                events=m.tool_events,
            )
            trace_widgets.append(trace_c)
            await trace_pane.mount_all(trace_widgets)

        diffs_pane = TabPane(f"Diffs ({len(m.file_diffs)})", id=tab_diffs_id(mid))
        await tabs.add_pane(diffs_pane)
        diffs_widgets: list = [self._vote_bar(idx, "code")]
        if m.file_diffs:
            diffs_widgets.extend(
                LazyCollapsible.for_diff(fd.filename, fd.diff, letter, classes="inner")
                for fd in m.file_diffs
            )
        elif m.diff.strip():
            diffs_widgets.append(DiffDisplay(strip_diff_preamble(m.diff), letter, "full-diff"))
        else:
            diffs_widgets.append(Static("No diff available.", classes="status"))
        await diffs_pane.mount_all(diffs_widgets)

    async def _populate_overview(self) -> None:
        """Lazily compose the overview panel on first switch."""
        if self._overview_populated:
            return
        self._overview_populated = True

        container = self.query_one(f"#{ids.OVERVIEW}", ScrollableContainer)

        history = self.data.get("history", [])
        if not isinstance(history, list):
            history = [history]
        tabs = TabbedContent(id=ids.TABS_OVERVIEW)
        await container.mount(tabs)

        current_pane = TabPane("Current", id=ids.TAB_CURRENT)
        await tabs.add_pane(current_pane)
        widgets = [
            Static(self.rankings_summary(), id=ids.JUST_RANKINGS),
            Markdown(
                self.summary_text
                or "*No summary yet -- press ctrl+e to write one, y to yank snippets.*",
                id=ids.JUST_PREVIEW,
            ),
        ]
        widgets.append(
            TextArea(
                self.summary_text,
                language="markdown",
                show_line_numbers=True,
                id=ids.JUST_EDITOR,
            )
        )
        await current_pane.mount_all(widgets)
        self.query_one(f"#{ids.JUST_EDITOR}").display = False

        # -- History entry tabs (newest first) --
        tab_idx = 0
        if history:
            for orig_idx in range(len(history) - 1, -1, -1):
                entry = history[orig_idx]
                level = entry.get("reviewLevel", "?")
                changed = orig_idx == 0 or has_meaningful_changes(history[orig_idx - 1], entry)
                entry_fb = feedback_for_entry(history, orig_idx)

                # Skip pure reviews with no feedback (nothing to show)
                if not changed and not entry_fb:
                    continue

                kind = "revision" if changed else "review"
                pane = TabPane(f"L{level} {kind}", id=tab_entry_id(tab_idx))
                await tabs.add_pane(pane)

                widgets_to_mount: list[Widget] = [Static(format_history_entry(entry, orig_idx))]

                # Feedback new in this entry (inline)
                for fb in entry_fb:
                    ts = fb.get("timestamp", "")
                    ts_label = format_timestamp(ts) if ts else "unknown"
                    fb_c = Collapsible(
                        title=f"Feedback | {ts_label}", collapsed=False, classes="inner"
                    )
                    widgets_to_mount.append(fb_c)

                # Diff from previous entry (only for revisions)
                if changed and orig_idx > 0:
                    ranking_changes = history_ranking_changes(history[orig_idx - 1], entry)
                    just_texts = history_justification_texts(history[orig_idx - 1], entry)
                    if ranking_changes or just_texts:
                        diff_c = Collapsible(
                            title="Changes",
                            collapsed=False,
                            classes="history-diff",
                        )
                        widgets_to_mount.append(diff_c)

                # Justification (only for revisions -- reviews have same text)
                if changed:
                    just = history_justification(entry)
                    if just:
                        widgets_to_mount.append(
                            Static("[bold]Justification:[/bold]", classes="section-title")
                        )
                        widgets_to_mount.append(Markdown(just))
                    else:
                        widgets_to_mount.append(Static("No justification.", classes="status"))

                await pane.mount_all(widgets_to_mount)

                # Mount feedback message content
                for fb_c_widget, fb in zip(pane.query(".inner"), entry_fb, strict=False):
                    message = fb.get("message", "No message.")
                    await fb_c_widget.query_one(Collapsible.Contents).mount(
                        Static(RichMarkdown(message))
                    )

                # Mount diff content into the collapsible
                if changed and orig_idx > 0 and (ranking_changes or just_texts):
                    diff_c = pane.query_one(".history-diff", Collapsible)
                    diff_widgets: list[Static] = []
                    if ranking_changes:
                        diff_widgets.append(Static("\n".join(ranking_changes)))
                    if just_texts:
                        from redlines import Redlines

                        diff_widgets.append(
                            Static(Redlines(just_texts[0], just_texts[1]).output_rich)
                        )
                    await diff_c.query_one(Collapsible.Contents).mount_all(diff_widgets)

                tab_idx += 1

    async def on_mount(self) -> None:
        if self.task_type == TaskType.UNKNOWN:
            self.notify(f"Loaded task {self.task_id} (unsupported type)")
            self._maybe_show_tutorial()
            return
        if self.models:
            await self._populate_model(self.current_model_index)
            self.notify(f"Loaded task {self.task_id} ({len(self.models)} models)")
        else:
            await self._populate_overview()
            self.notify(f"Loaded task {self.task_id}")
        self._update_scoreboard()
        self._maybe_show_tutorial()

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
            return

        # Lazy trace event loading
        if lazy.events and not lazy.populated:
            lazy.populated = True
            dict_events = [ev for ev in lazy.events if isinstance(ev, dict)]
            collapsibles: list[Collapsible] = []
            for ev in dict_events:
                ev_name = clean_event_name(str(ev.get("name", "")))
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

    def _save_summary_from_editor(self) -> None:
        """Save summary text from the inline editor if it exists."""
        try:
            editor = self.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            self._save_summary(editor.text)
        except Exception:
            pass

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if (
            self._overview_populated
            and event.tabbed_content.id == ids.TABS_OVERVIEW
            and str(event.pane.id) != ids.TAB_CURRENT
        ):
            self._show_justification_preview()

    def _is_on_model_view(self) -> bool:
        """True when a model panel is active in the content switcher."""
        try:
            current = self.query_one(f"#{ids.MAIN_SWITCHER}", ContentSwitcher).current
            return current is not None and str(current).startswith("model-")
        except Exception:
            return False

    def _is_on_overview(self) -> bool:
        """True when the overview panel is active in the content switcher."""
        try:
            return bool(
                self.query_one(f"#{ids.MAIN_SWITCHER}", ContentSwitcher).current == ids.OVERVIEW
            )
        except Exception:
            return False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("vote_up", "vote_down", "search_diffs"):
            return self._is_on_model_view()
        if action in ("edit_justification", "copy_summary"):
            return self._is_on_overview()
        if action == "toggle_collapse":
            return self._is_on_model_view() or self._is_on_overview()
        return True

    async def go_to(self, section_id: str) -> None:
        if self.task_type == TaskType.UNKNOWN:
            return
        self.query_one(f"#{ids.MAIN_SWITCHER}", ContentSwitcher).current = section_id
        self.refresh_bindings()
        if section_id == ids.OVERVIEW:
            await self._populate_overview()
            return
        for i in range(len(self.models)):
            if model_id(i) == section_id:
                self.current_model_index = i
                await self._populate_model(i)
                return

    async def go_to_diff(self, model_index: int, filename: str) -> None:
        if model_index >= len(self.models):
            return
        mid = model_id(model_index)
        await self.go_to(mid)
        tabs = self.query_one(f"#{model_tabs_id(mid)}", TabbedContent)
        tabs.active = tab_diffs_id(mid)
        diffs_pane = tabs.get_pane(tab_diffs_id(mid))
        container = self.query_one(f"#{mid}", ScrollableContainer)
        for collapsible in diffs_pane.query(Collapsible):
            if str(collapsible.title) == filename:
                collapsible.collapsed = False
                self.call_later(lambda c=collapsible: container.scroll_to_center(c))
                break

    async def action_go_to(self, section_id: str) -> None:
        await self.go_to(section_id)

    async def action_go_model(self, index: int) -> None:
        if 0 <= index < len(self.models):
            await self.go_to(model_id(index))

    def _active_tabbed_content(self) -> TabbedContent | None:
        """Return the TabbedContent widget in the currently visible view."""
        if self.task_type == TaskType.UNKNOWN:
            return None
        switcher = self.query_one("#main-switcher", ContentSwitcher)
        current = switcher.current
        if not current:
            return None
        try:
            if current == ids.OVERVIEW:
                return self.query_one(f"#{ids.TABS_OVERVIEW}", TabbedContent)
            return self.query_one(f"#{model_tabs_id(current)}", TabbedContent)
        except Exception:
            return None

    def _active_tabs_widget(self):
        """Return the Tabs widget from the active TabbedContent, if any."""
        from textual.widgets import Tabs

        tc = self._active_tabbed_content()
        if tc:
            try:
                return tc.query_one(Tabs)
            except Exception:
                pass
        return None

    def action_next_tab(self) -> None:
        tabs = self._active_tabs_widget()
        if tabs:
            tabs.action_next_tab()

    def action_prev_tab(self) -> None:
        tabs = self._active_tabs_widget()
        if tabs:
            tabs.action_previous_tab()

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
        self.notify(f"Theme: {theme_name}")

    def _current_model(self) -> ModelData | None:
        if 0 <= self.current_model_index < len(self.models):
            model: ModelData = self.models[self.current_model_index]
            return model
        return None

    def action_search_diffs(self) -> None:
        m = self._current_model()
        if not m:
            self.notify("Navigate to a model first.", severity="warning")
            return
        if not m.file_diffs:
            self.notify("No diffs in this model.", severity="warning")
            return

        async def _on_result(result: tuple[int, str] | None) -> None:
            if result:
                await self.go_to_diff(result[0], result[1])

        self.push_screen(DiffSearchModal(self.current_model_index, m.file_diffs), _on_result)

    def action_yank_file(self) -> None:
        focused = self.focused
        if not isinstance(focused, DiffDisplay):
            self.notify("Focus a diff first (click or tab into it).", severity="warning")
            return
        selected = focused.selected_text.strip()
        snippet = selected if selected else focused.diff_text
        if not snippet.strip():
            self.notify("No diff content to yank.", severity="warning")
            return
        if selected:
            sel = focused.selection
            start_idx = min(sel.start[0], sel.end[0])
            end_idx = max(sel.start[0], sel.end[0])
            line_ref = diff_line_ref(focused.diff_text, start_idx, end_idx)
        else:
            line_ref = diff_line_ref(focused.diff_text, 0, len(focused.diff_text.splitlines()) - 1)
        filename = focused.filename

        def _on_result(result: tuple[int, str] | None) -> None:
            if result:
                _, block = result
                if self.summary_text and not self.summary_text.endswith("\n"):
                    self.summary_text += "\n"
                self.summary_text += block
                self._save_summary(self.summary_text)
                self._refresh_overview_annotations()
                self.notify(f"Yanked snippet from {filename}")

        self.push_screen(
            YankCommentModal(
                self.current_model_index,
                focused.model_name,
                filename,
                snippet,
                line_ref,
            ),
            _on_result,
        )

    def on_diff_display_vote_requested(self, event: DiffDisplay.VoteRequested) -> None:
        idx = self.current_model_index
        if idx < 0 or idx >= len(self.models):
            return
        self._apply_vote(idx, Context.CODE, event.delta)

    def on_diff_display_yank_requested(self, event: DiffDisplay.YankRequested) -> None:
        self.action_yank_file()

    def _detect_vote_context(self) -> str:
        mid = model_id(self.current_model_index)
        try:
            tabs = self.query_one(f"#{model_tabs_id(mid)}", TabbedContent)
            active = tabs.active
            if active == tab_response_id(mid):
                return Context.RESPONSE
            if active in (tab_trace_id(mid), tab_diffs_id(mid)):
                return Context.CODE
        except Exception:
            pass
        return Context.OVERALL

    def _apply_vote(self, idx: int, context: str, delta: int) -> None:
        annotation = Annotation(context=context, sentiment=delta)
        self.add_annotation(idx, annotation)
        score = getattr(self.scores[idx], context)
        sign = f"+{score}" if score > 0 else str(score)
        arrow = ARROW_UP if delta > 0 else ARROW_DOWN
        color = "green" if delta > 0 else "red"
        self.notify(f"[{color}]{arrow}[/] {model_letter(idx)} {context}: {sign}")

    def _vote(self, delta: int) -> None:
        if self.task_type == TaskType.UNKNOWN:
            self.notify("Voting not available for this task type.", severity="warning")
            return
        idx = self.current_model_index
        if idx < 0 or idx >= len(self.models):
            self.notify("Navigate to a model first.", severity="warning")
            return
        switcher = self.query_one(f"#{ids.MAIN_SWITCHER}", ContentSwitcher)
        if not switcher.current or not str(switcher.current).startswith("model-"):
            self.notify("Navigate to a model first.", severity="warning")
            return
        context = self._detect_vote_context()
        self._apply_vote(idx, context, delta)

    def action_vote_up(self) -> None:
        self._vote(1)

    def action_vote_down(self) -> None:
        self._vote(-1)

    def action_reset_local(self) -> None:
        self.annotations = [[] for _ in range(len(self.models))]
        self.summary_text = ""
        for path in (
            annotations_path(self.task_id),
            scores_path(self.task_id),
            justification_path(self.task_id),
        ):
            if path.exists():
                path.unlink()
        self.scores = [ModelScores() for _ in range(len(self.models))]
        self.notify("Local annotations and scores reset.")

    def _history(self) -> list | dict:
        result: list | dict = self.data.get("history", [])
        return result

    def rankings_summary(self) -> str:
        return ranking.rankings_summary(self.scores, self._history())

    def _update_scoreboard(self) -> None:
        try:
            board = self.query_one(f"#{ids.SCOREBOARD}", Static)
        except Exception:
            return
        board.update(self.rankings_summary())

        has_local_votes = any(s.any_nonzero() for s in self.scores)
        history = self._history()

        for idx in range(len(self.models)):
            letter = model_letter(idx)
            label = f"[bold]{letter}[/bold]"
            header = self.query_one_optional(f"#{model_header_id(model_id(idx))}", Static)
            if header:
                header.update(label)
                if has_local_votes:
                    rank = ranking.model_rank(self.scores, idx)
                    header.styles.background = rank_color(rank, len(self.models))
                else:
                    prev_rank = ranking.previous_model_rank(history, idx)
                    if prev_rank is not None:
                        header.styles.background = rank_color(prev_rank, len(self.models))
                    else:
                        header.styles.background = None

    @staticmethod
    def _model_summary_text(m: ModelData) -> str:
        return ranking.model_summary_text(m)

    def add_annotation(self, model_index: int, annotation: Annotation) -> None:
        """Append an annotation for a model, persist, and refresh UI."""
        if 0 <= model_index < len(self.annotations):
            self.annotations[model_index].append(annotation)
        self.scores = scores_from_annotations(self.annotations)
        save_annotations(self.task_id, self.annotations, self.summary_text)

    def _save_summary(self, text: str) -> None:
        """Update the summary text and persist."""
        self.summary_text = text
        save_annotations(self.task_id, self.annotations, self.summary_text)

    def _refresh_overview_annotations(self) -> None:
        """Refresh the overview summary and rankings."""
        if not self._overview_populated:
            return
        try:
            rankings = self.query_one(f"#{ids.JUST_RANKINGS}", Static)
            rankings.update(self.rankings_summary())
            preview = self.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
            preview.update(
                self.summary_text
                or "*No summary yet -- press ctrl+e to write one, y to yank snippets.*"
            )
        except Exception:
            pass

    async def action_edit_justification(self) -> None:
        """Navigate to overview, switch to Current tab, and activate the editor."""
        await self.go_to("overview")
        try:
            tabs = self.query_one(f"#{ids.TABS_OVERVIEW}", TabbedContent)
            tabs.active = ids.TAB_CURRENT
        except Exception:
            pass
        self._show_justification_editor()

    def _show_justification_editor(self) -> None:
        try:
            editor = self.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            preview = self.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
        except Exception:
            return
        editor.text = self.summary_text
        preview.display = False
        editor.display = True
        editor.focus()

    def _show_justification_preview(self) -> None:
        try:
            editor = self.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            preview = self.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
        except Exception:
            return
        if editor.display:
            self._save_summary(editor.text)
            editor.display = False
            preview.update(
                self.summary_text
                or "*No summary yet -- press ctrl+e to write one, y to yank snippets.*"
            )
            preview.display = True

    def on_key(self, event) -> None:
        """Handle escape from justification editor to switch back to preview."""
        if (
            event.key == "escape"
            and isinstance(self.focused, TextArea)
            and getattr(self.focused, "id", None) == ids.JUST_EDITOR
        ):
            event.prevent_default()
            event.stop()
            self._show_justification_preview()

    _HELP_TEXT = (
        "[bold]Navigation[/bold]\n"
        "  1/2/3      switch to model A/B/C\n"
        "  0          overview (review, history, feedback)\n"
        "  tab        next tab within a view\n"
        "  shift+tab  previous tab\n"
        "  e          expand/collapse all in current tab\n\n"
        "[bold]Review[/bold]\n"
        "  +/-        vote (diff=code, response=response, else=overall)\n"
        "  y          yank diff snippet into justification\n\n"
        "[bold]Actions[/bold]\n"
        "  ctrl+e     edit summary\n"
        "  ctrl+f     fuzzy file search in current model\n"
        "  c          copy review to clipboard\n"
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

    def action_help(self) -> None:
        self.push_screen(HelpModal(self._HELP_TEXT, "Keyboard Shortcuts"))

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
            self.notify("No session available.", severity="warning")
            return
        self.notify("Refreshing...")
        new_data = fetch_data(self.task_arg, self.cookies)
        self.data = new_data
        self.parsed = parse_content(new_data.get("content", {}))
        self.models = self.parsed.models
        self.task_id = new_data.get("task", {}).get("taskId") or self.parsed.task_id or self.task_id
        self.sub_title = f"Task {self.task_id} (refreshed)"
        self.refresh(recompose=True)
        self.notify("Data refreshed.")

    def action_copy_summary(self) -> None:
        text = build_clipboard_text(
            self.task_id,
            self.rankings_summary(),
            self.summary_text,
        )
        if not text.strip():
            self.notify("Nothing to copy.", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify("Rankings & justification copied to clipboard.")

    async def action_quit(self) -> None:
        self._save_summary_from_editor()
        self.exit()
