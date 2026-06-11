"""Main Starfleet TUI application."""

from rich.markdown import Markdown as RichMarkdown
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
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

from sfctl import ranking
from sfctl.commands import NavigationProvider
from sfctl.config import get_web_url, load_config, update_config
from sfctl.constants import ARROW_DOWN, ARROW_UP, EM_DASH
from sfctl.models import Annotation, ModelData, ModelScores, ParsedContent
from sfctl.parsing import (
    bump_headings,
    clean_event_name,
    diff_line_ref,
    feedback_for_entry,
    format_event_line,
    format_history_entry,
    group_events,
    has_meaningful_changes,
    history_diff,
    history_justification,
    parse_content,
    rank_color,
    strip_diff_preamble,
    trace_type_color,
)
from sfctl.scoring import (
    annotations_path,
    justification_path,
    load_annotations,
    render_annotations_md,
    save_annotations,
    scores_from_annotations,
    scores_path,
)
from sfctl.screens import (
    DiffSearchModal,
    YankCommentModal,
    build_clipboard_text,
)
from sfctl.widgets import DiffDisplay, LazyCollapsible, trace_event_detail_widgets


class StarfleetApp(App):
    TITLE = "Starfleet Control"
    COMMANDS = {NavigationProvider}
    CSS_PATH = "app.tcss"

    current_model_index: reactive[int] = reactive(0)
    show_hidden: reactive[bool] = reactive(False)
    scores: reactive[list[ModelScores]] = reactive(list, always_update=True)

    def watch_scores(self, new_scores: list[ModelScores]) -> None:
        if not hasattr(self, "_overview_populated") or not self.is_running:
            return
        self._update_scoreboard()
        self._refresh_overview_annotations()
        for idx in range(len(new_scores)):
            self._refresh_vote_labels(idx)

    def watch_show_hidden(self, value: bool) -> None:
        if not hasattr(self, "_overview_populated"):
            return
        if value and self._task_email and self._task_email != EM_DASH:
            self.sub_title = f"Task {self.task_id}  |  {self._task_email}"
        else:
            self.sub_title = f"Task {self.task_id}"
        if self._overview_populated:
            for widget in self.query(".hidden-detail"):
                widget.display = value

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("1", "go_model(0)", "A", show=True),
        Binding("2", "go_model(1)", "B", show=True),
        Binding("3", "go_model(2)", "C", show=True),
        Binding("f", "go_to('overview')", "Overview", show=True),
        Binding("ctrl+e", "edit_justification", "Edit", show=True),
        Binding("y", "yank_file", "Yank", show=True),
        Binding("+", "vote_up", f"{ARROW_UP} Up", show=True),
        Binding("-", "vote_down", f"{ARROW_DOWN} Down", show=True),
        Binding("ctrl+f", "search_diffs", "Find File", show=True),
        Binding("r", "refresh_data", "Refresh"),
        Binding("ctrl+r", "reset_local", "Reset"),
        Binding("c", "copy_summary", "Copy"),
        Binding("tab", "next_tab", "Next Tab", show=False, priority=True),
        Binding("shift+tab", "prev_tab", "Prev Tab", show=False, priority=True),
        Binding("ctrl+d", "toggle_hidden", "", show=False),
        Binding("?", "help", "Help"),
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
        self._task_email = (task.get("actionHistory") or [{}])[0].get("userId", "")
        self.sub_title = f"Task {self.task_id}"

        config = load_config()
        if "theme" in config:
            self.theme = config["theme"]

    @staticmethod
    def _model_letter(index: int) -> str:
        return ranking.model_letter(index)

    @staticmethod
    def _model_id(index: int) -> str:
        return ranking.model_id(index)

    def nav_items(self) -> list[tuple[str, str]]:
        return ranking.nav_items(self.models)

    def diff_items(self) -> list[tuple[str, int, str]]:
        return ranking.diff_items(self.models)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        repo = self.parsed.repository
        prompt = bump_headings(self.parsed.current_prompt or EM_DASH)
        self.task_url = get_web_url(f"/tasks/{self.task_id}")
        with Vertical(id="info-bar"):
            yield Link(f"Task: {self.task_id}", url=self.task_url, id="task-bar")
            yield Static(self.rankings_summary(), id="scoreboard")
            if repo and repo != EM_DASH:
                yield Static(
                    f"[bold]Repo:[/bold] {repo.replace('[', '(').replace(']', ')')}", id="repo-bar"
                )
            with Collapsible(title="Prompt", collapsed=False, id="prompt-bar"):
                yield Static(RichMarkdown(prompt))

        yield Footer()

        initial = self._model_id(0) if self.models else "overview"
        self._populated_models = set()
        self._overview_populated = False

        with ContentSwitcher(initial=initial, id="main-switcher"):
            for idx in range(len(self.models)):
                mid = self._model_id(idx)
                with ScrollableContainer(id=mid):
                    yield Static(
                        f"[bold]{self._model_letter(idx)}[/bold]",
                        classes="view-header",
                        id=f"header-{mid}",
                    )

            with ScrollableContainer(id="overview"):
                pass  # populated lazily by _populate_overview()

    @staticmethod
    async def _mount_into(collapsible: Collapsible, *widgets: Static | Collapsible | DiffDisplay) -> None:
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
            Button(f"{ARROW_UP}", id=f"vote-up-{idx}-{context}", classes="vote-btn"),
            Static(sign, classes="vote-score", id=f"vote-label-{idx}-{context}"),
            Button(f"{ARROW_DOWN}", id=f"vote-down-{idx}-{context}", classes="vote-btn"),
            classes="vote-bar",
        )

    def _refresh_vote_labels(self, idx: int) -> None:
        if idx >= len(self.scores):
            return
        s = self.scores[idx]
        for context in ("overall", "response", "code"):
            label = self.query_one_optional(f"#vote-label-{idx}-{context}", Static)
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
        annotation = Annotation(context=context, sentiment=delta)
        self.add_annotation(idx, annotation)
        arrow = ARROW_UP if delta > 0 else ARROW_DOWN
        color = "green" if delta > 0 else "red"
        score = getattr(self.scores[idx], context, 0)
        sign = f"+{score}" if score > 0 else str(score)
        self.notify(f"[{color}]{arrow}[/] {self._model_letter(idx)} {context}: {sign}")

    async def _populate_model(self, idx: int) -> None:
        """Lazily compose a model view's content on first switch."""
        if idx in self._populated_models:
            return
        self._populated_models.add(idx)

        m = self.models[idx]
        mid = self._model_id(idx)
        letter = self._model_letter(idx)

        container = self.query_one(f"#{mid}", ScrollableContainer)

        grouped = group_events(m)
        total = sum(1 for e in m.tool_events if isinstance(e, dict))

        tabs = TabbedContent(id=f"tabs-{mid}")
        await container.mount(tabs)

        response_pane = TabPane("Response", id=f"tab-response-{mid}")
        await tabs.add_pane(response_pane)
        summary = self._model_summary_text(m)
        await response_pane.mount_all([
            self._vote_bar(idx, "response"),
            Static(RichMarkdown(summary)),
        ])

        trace_pane = TabPane(f"Trace ({total})", id=f"tab-trace-{mid}")
        await tabs.add_pane(trace_pane)
        if not grouped:
            await trace_pane.mount(Static("No tool events.", classes="status"))
        else:
            sorted_groups = sorted(grouped.items(), key=lambda x: -len(x[1]))
            trace_widgets: list[Static | LazyCollapsible] = []
            for ename, events in sorted_groups:
                ti = self._trace_type_index(ename)
                color = trace_type_color(ti)
                trace_widgets.append(
                    Static(
                        f"  [{color}]{ename}[/]  [dim]{len(events)}x[/]",
                        classes="trace-summary-row",
                    )
                )
            trace_c = LazyCollapsible.for_trace(
                title=f"Event Details ({total} events)",
                events=m.tool_events,
            )
            trace_widgets.append(trace_c)
            await trace_pane.mount_all(trace_widgets)

        diffs_pane = TabPane(f"Diffs ({len(m.file_diffs)})", id=f"tab-diffs-{mid}")
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

        container = self.query_one("#overview", ScrollableContainer)

        history = self.data.get("history", [])
        if not isinstance(history, list):
            history = [history]
        tabs = TabbedContent(id="tabs-overview")
        await container.mount(tabs)

        # -- Current (Draft) tab -- always first --
        current_pane = TabPane("Current", id="tab-current")
        await tabs.add_pane(current_pane)
        rendered = render_annotations_md(self.annotations, self.summary_text)
        await current_pane.mount_all([
            Static(self.rankings_summary(), id="just-rankings"),
            Markdown(rendered, id="justification-preview"),
            TextArea(
                self.summary_text,
                language="markdown",
                show_line_numbers=True,
                id="justification-editor",
            ),
        ])
        self.query_one("#justification-editor").display = False

        # -- History entry tabs (newest first) --
        tab_idx = 0
        if history:
            for orig_idx in range(len(history) - 1, -1, -1):
                entry = history[orig_idx]
                level = entry.get("reviewLevel", "?")
                changed = orig_idx == 0 or has_meaningful_changes(
                    history[orig_idx - 1], entry
                )
                entry_fb = feedback_for_entry(history, orig_idx)

                # Skip pure reviews with no feedback (nothing to show)
                if not changed and not entry_fb:
                    continue

                kind = "revision" if changed else "review"
                pane = TabPane(f"L{level} {kind}", id=f"tab-entry-{tab_idx}")
                await tabs.add_pane(pane)

                widgets_to_mount = [Static(format_history_entry(entry, orig_idx))]

                # Hidden email detail
                email = entry.get("email", "")
                if email:
                    email_w = Static(
                        f"[dim]Email:[/dim] {email.replace('[', '(').replace(']', ')')}",
                        classes="hidden-detail",
                    )
                    email_w.display = self.show_hidden
                    widgets_to_mount.append(email_w)

                # Feedback new in this entry (inline)
                for fb in entry_fb:
                    ts = str(fb.get("timestamp", ""))[:19]
                    fb_title = f"Feedback | {ts}"
                    if self.show_hidden:
                        fb_email = fb.get("email", "unknown")
                        fb_title = f"Feedback | {fb_email} | {ts}"
                    fb_c = Collapsible(title=fb_title, collapsed=False, classes="inner")
                    widgets_to_mount.append(fb_c)

                # Diff from previous entry (only for revisions)
                if changed and orig_idx > 0:
                    diff_text = history_diff(history[orig_idx - 1], entry)
                    if diff_text:
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
                        widgets_to_mount.append(
                            Static("No justification.", classes="status")
                        )

                await pane.mount_all(widgets_to_mount)

                # Mount feedback message content
                for fb_c_widget, fb in zip(
                    pane.query(".inner"), entry_fb, strict=False
                ):
                    message = fb.get("message", "No message.")
                    await fb_c_widget.query_one(Collapsible.Contents).mount(
                        Static(RichMarkdown(message))
                    )

                # Mount diff content into the collapsible
                if changed and orig_idx > 0:
                    diff_text = history_diff(history[orig_idx - 1], entry)
                    if diff_text:
                        diff_c = pane.query_one(".history-diff", Collapsible)
                        await diff_c.query_one(Collapsible.Contents).mount(
                            DiffDisplay(
                                diff_text, "overview", f"entry-{orig_idx-1}-{orig_idx}"
                            )
                        )

                tab_idx += 1

    async def on_mount(self) -> None:
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
            editor = self.query_one("#justification-editor", TextArea)
            self._save_summary(editor.text)
        except Exception:
            pass

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Auto-save and switch back to preview when leaving Current tab."""
        if (
            self._overview_populated
            and event.tabbed_content.id == "tabs-overview"
            and str(event.pane.id) != "tab-current"
        ):
            self._show_justification_preview()

    async def go_to(self, section_id: str) -> None:
        self.query_one("#main-switcher", ContentSwitcher).current = section_id
        if section_id == "overview":
            await self._populate_overview()
            return
        for i in range(len(self.models)):
            if self._model_id(i) == section_id:
                self.current_model_index = i
                await self._populate_model(i)
                return

    async def go_to_diff(self, model_index: int, filename: str) -> None:
        if model_index >= len(self.models):
            return
        mid = self._model_id(model_index)
        await self.go_to(mid)
        tabs = self.query_one(f"#tabs-{mid}", TabbedContent)
        tabs.active = f"tab-diffs-{mid}"
        diffs_pane = tabs.get_pane(f"tab-diffs-{mid}")
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
            await self.go_to(self._model_id(index))

    def _active_tabbed_content(self) -> TabbedContent | None:
        """Return the TabbedContent widget in the currently visible view."""
        switcher = self.query_one("#main-switcher", ContentSwitcher)
        current = switcher.current
        if not current:
            return None
        try:
            if current == "overview":
                return self.query_one("#tabs-overview", TabbedContent)
            return self.query_one(f"#tabs-{current}", TabbedContent)
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

    def action_toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.notify(f"Hidden details: {'on' if self.show_hidden else 'off'}")

    def set_theme(self, theme_name: str) -> None:
        self.theme = theme_name
        update_config(theme=theme_name)
        self.notify(f"Theme: {theme_name}")

    def _current_model(self) -> ModelData | None:
        if 0 <= self.current_model_index < len(self.models):
            return self.models[self.current_model_index]
        return None

    def action_search_diffs(self) -> None:
        m = self._current_model()
        if not m:
            self.notify("Navigate to a model first.", severity="warning")
            return
        if not m.file_diffs:
            self.notify("No diffs in this model.", severity="warning")
            return
        self.push_screen(DiffSearchModal(self.current_model_index, m.file_diffs, self))

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
        self.push_screen(
            YankCommentModal(
                self.current_model_index,
                focused.model_name,
                focused.filename,
                snippet,
                line_ref,
                self,
            )
        )

    def _detect_vote_context(self) -> str:
        if isinstance(self.focused, DiffDisplay):
            return "code"
        mid = self._model_id(self.current_model_index)
        try:
            tabs = self.query_one(f"#tabs-{mid}", TabbedContent)
            active = tabs.active
            if active == f"tab-response-{mid}":
                return "response"
            if active in (f"tab-trace-{mid}", f"tab-diffs-{mid}"):
                return "code"
        except Exception:
            pass
        return "overall"

    def _vote(self, delta: int) -> None:
        idx = self.current_model_index
        if idx < 0 or idx >= len(self.models):
            self.notify("Navigate to a model first.", severity="warning")
            return
        switcher = self.query_one("#main-switcher", ContentSwitcher)
        if not switcher.current or not str(switcher.current).startswith("model-"):
            self.notify("Navigate to a model first.", severity="warning")
            return
        context = self._detect_vote_context()
        annotation = Annotation(context=context, sentiment=delta)
        self.add_annotation(idx, annotation)
        score = getattr(self.scores[idx], context)
        sign = f"+{score}" if score > 0 else str(score)
        arrow = ARROW_UP if delta > 0 else ARROW_DOWN
        color = "green" if delta > 0 else "red"
        self.notify(f"[{color}]{arrow}[/] {self._model_letter(idx)} {context}: {sign}")

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
        return self.data.get("history", [])

    def rankings_summary(self) -> str:
        return ranking.rankings_summary(self.scores, self._history())

    def _update_scoreboard(self) -> None:
        try:
            board = self.query_one("#scoreboard", Static)
        except Exception:
            return
        board.update(self.rankings_summary())

        has_local_votes = any(s.any_nonzero() for s in self.scores)
        history = self._history()

        for idx in range(len(self.models)):
            letter = self._model_letter(idx)
            label = f"[bold]{letter}[/bold]"
            header = self.query_one_optional(f"#header-{self._model_id(idx)}", Static)
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
        """Refresh the overview preview with current annotations + summary."""
        if not self._overview_populated:
            return
        try:
            preview = self.query_one("#justification-preview", Markdown)
            rendered = render_annotations_md(self.annotations, self.summary_text)
            preview.update(rendered)
            rankings = self.query_one("#just-rankings", Static)
            rankings.update(self.rankings_summary())
        except Exception:
            pass

    async def action_edit_justification(self) -> None:
        """Navigate to overview, switch to Current tab, and activate the editor."""
        await self.go_to("overview")
        try:
            tabs = self.query_one("#tabs-overview", TabbedContent)
            tabs.active = "tab-current"
        except Exception:
            pass
        self._show_justification_editor()

    def _show_justification_editor(self) -> None:
        try:
            editor = self.query_one("#justification-editor", TextArea)
            preview = self.query_one("#justification-preview", Markdown)
        except Exception:
            return
        preview.display = False
        editor.display = True
        editor.focus()

    def _show_justification_preview(self) -> None:
        try:
            editor = self.query_one("#justification-editor", TextArea)
            preview = self.query_one("#justification-preview", Markdown)
        except Exception:
            return
        if editor.display:
            self._save_summary(editor.text)
            editor.display = False
            preview.display = True
            rendered = render_annotations_md(self.annotations, self.summary_text)
            preview.update(rendered)

    def on_key(self, event) -> None:
        """Handle escape from justification editor to switch back to preview."""
        if (
            event.key == "escape"
            and isinstance(self.focused, TextArea)
            and getattr(self.focused, "id", None) == "justification-editor"
        ):
            event.prevent_default()
            event.stop()
            self._show_justification_preview()

    _HELP_TEXT = (
        "[bold]Navigation[/bold]\n"
        "  1/2/3 models | f overview\n"
        "  tab/shift+tab cycle tabs\n\n"
        "[bold]Review[/bold]\n"
        "  +/- vote (context: diff=code, response=response, else=overall)\n"
        "  y   yank snippet (with sentiment & comment)\n\n"
        "[bold]Actions[/bold]\n"
        "  ctrl+e edit summary | ctrl+f find file | c copy\n"
        "  r refresh | ctrl+r reset | q quit"
    )

    _TUTORIAL_TEXT = (
        "[bold]Welcome to Starfleet Control[/bold]\n\n"
        "This is a code review TUI. You compare model outputs and\n"
        "build a structured review with scores and evidence.\n\n"
        "[bold]1. Navigate[/bold] -- press 1/2/3 to view models, f for overview\n"
        "[bold]2. Review[/bold]   -- press + or - to vote on what you see\n"
        "   Votes are context-aware: on a diff it scores code,\n"
        "   on a response it scores response quality\n"
        "[bold]3. Yank[/bold]     -- focus a diff, press y to capture a snippet\n"
        "   Add a comment and sentiment (+1/0/-1) to explain your score\n"
        "[bold]4. Summarize[/bold] -- press ctrl+e to write a free-text summary\n"
        "[bold]5. Export[/bold]   -- press c to copy your full review to clipboard\n\n"
        "Press ? anytime to see all shortcuts."
    )

    def action_help(self) -> None:
        self.notify(self._HELP_TEXT, timeout=15)

    def _maybe_show_tutorial(self) -> None:
        config = load_config()
        if config.get("tutorial_seen"):
            return
        update_config(tutorial_seen=True)
        self.notify(self._TUTORIAL_TEXT, timeout=30)

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
        text = build_clipboard_text(self)
        if not text.strip():
            self.notify("Nothing to copy.", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify("Rankings & justification copied to clipboard.")

    async def action_quit(self) -> None:
        self._save_summary_from_editor()
        self.exit()
