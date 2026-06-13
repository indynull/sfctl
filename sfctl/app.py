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
from sfctl.models import Annotation, ModelData, ModelScores, ParsedContent, ProposalData
from sfctl.parsing import (
    _format_duration,
    _sanitize,
    bump_headings,
    clean_event_name,
    diff_line_ref,
    feedback_for_entry,
    format_event_line,
    format_history_entry,
    format_timestamp,
    group_events,
    has_meaningful_changes,
    has_proposal_changes,
    history_justification,
    history_justification_texts,
    history_ranking_changes,
    parse_content,
    parse_proposal,
    proposal_all_changes,
    rank_color,
    strip_diff_preamble,
    trace_type_color,
)
from sfctl.scoring import (
    _latest_server_justification,
    annotations_path,
    justification_path,
    load_annotations,
    save_annotations,
    scores_from_annotations,
    scores_path,
)
from sfctl.screens import (
    DiffSearchModal,
    DiffSearchResult,
    EventSearchModal,
    HelpModal,
    YankCommentModal,
    build_clipboard_text,
)
from sfctl.task_types import TaskType, detect_task_type
from sfctl.widgets import DiffDisplay, LazyCollapsible, SplitHandle, trace_event_detail_widgets


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
        Binding("ctrl+g", "search_events", "Find Event", show=True),

        Binding("m", "go_model_proposal", "Model"),
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
        self.task_type = detect_task_type(data)
        self.task_id = data.get("task", {}).get("taskId") or task_arg

        # Type-specific parsing
        self.proposal: ProposalData | None = None
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            self.proposal = parse_proposal(self._get_history(), data.get("trace"))
            self.parsed = ParsedContent(
                task_id=self.task_id,
                repository=self.proposal.repo_url,
                current_prompt=self.proposal.prompt,
            )
            self.models: list[ModelData] = []
        else:
            self.parsed = parse_content(data.get("content", {}))
            self.task_id = self.parsed.task_id or self.task_id
            self.models = self.parsed.models

        self.annotations: list[list[Annotation]]
        self.summary_text: str
        self.annotations, self.summary_text = load_annotations(
            self.task_id, len(self.models), self._get_history()
        )
        self._server_justification = _latest_server_justification(self._get_history())
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

        config = load_config()
        if "theme" in config:
            self.theme = config["theme"]

    def _get_history(self) -> list:
        """Return history as a list, normalizing the single-entry dict case."""
        h = self.data.get("history", [])
        return [h] if not isinstance(h, list) else h

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
                    f"[bold]Repo:[/bold] {_sanitize(repo)}",
                    id=ids.REPO_BAR,
                )
            prompt = self.parsed.current_prompt or EM_DASH
            if self.task_type == TaskType.PROJECT_PROPOSAL and self.proposal:
                prompt = self.proposal.prompt or EM_DASH
            prompt = bump_headings(prompt)
            with (
                Collapsible(title="Prompt", collapsed=False, id=ids.PROMPT_BAR),
                ScrollableContainer(),
            ):
                yield Static(RichMarkdown(prompt))

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield from self._compose_info_bar()
        yield SplitHandle(ids.INFO_BAR, ids.CONTENT_AREA, id=ids.SPLIT_HANDLE)
        yield from self._compose_content_area()
        yield Footer()

    def _compose_content_area(self) -> ComposeResult:
        self._populated_models = set()
        self._overview_populated = False

        if self.task_type == TaskType.UNKNOWN:
            with (
                ScrollableContainer(id=ids.CONTENT_AREA),
                ScrollableContainer(id=ids.OVERVIEW),
            ):
                yield Static(
                    "[bold]Unsupported task type[/bold]\n\n"
                    "This task does not match a known layout. "
                    "Only the raw overview and history are available.",
                    classes="status",
                )
            return

        if self.task_type == TaskType.PROJECT_PROPOSAL:
            mid = model_id(0)
            with (
                Vertical(id=ids.CONTENT_AREA),
                ContentSwitcher(initial=mid, id=ids.MAIN_SWITCHER),
            ):
                with ScrollableContainer(id=mid):
                    yield Static("[bold]Model[/bold]", classes="view-header", id=model_header_id(mid))
                with ScrollableContainer(id=ids.OVERVIEW):
                    pass
            return

        initial = model_id(0) if self.models else ids.OVERVIEW
        with (
            Vertical(id=ids.CONTENT_AREA),
            ContentSwitcher(initial=initial, id=ids.MAIN_SWITCHER),
        ):
                for idx in range(len(self.models)):
                    mid = model_id(idx)
                    with ScrollableContainer(id=mid):
                        yield Static(
                            f"[bold]{model_letter(idx)}[/bold]",
                            classes="view-header",
                            id=model_header_id(mid),
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
                f"{_sanitize(cmd.get('command', ''), 200)}"
                for cmd in setup_commands
            )
            tw.append(Static(f"[bold]Setup ({len(setup_commands)}):[/bold]"))
            tw.append(Static(setup_lines))

        grouped = group_events(tool_events)
        if grouped:
            timed = [
                (name, evts, sum(e.get("wall_time") or 0 for e in evts))
                for name, evts in grouped.items()
            ]
            timed.sort(key=lambda x: -x[2])
            parts = []
            for ename, events, total_ms in timed:
                ti = self._trace_type_index(ename)
                color = trace_type_color(ti)
                time_str = f" {_format_duration(total_ms)}" if total_ms else ""
                parts.append(f"[{color}]{ename}[/] [dim]{len(events)}x{time_str}[/]")
            tw.append(Static("  ".join(parts)))
            tw.append(LazyCollapsible.for_trace(
                title=f"Event Details ({len(tool_events)} events)",
                events=tool_events,
            ))

        if bash_history:
            bh_lines = "\n".join(
                f"[dim]{format_timestamp(bh.get('timestamp', ''))}[/dim]  "
                f"{_sanitize(bh.get('command', ''), 200)}"
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
        total = sum(1 for e in m.tool_events if isinstance(e, dict))

        tabs = TabbedContent(id=model_tabs_id(mid))
        await container.mount(tabs)

        response_pane = TabPane("Response", id=tab_response_id(mid))
        await tabs.add_pane(response_pane)
        summary = self._model_summary_text(m)
        await response_pane.mount_all([
            self._vote_bar(idx, "response"),
            Static(RichMarkdown(summary)),
        ])

        trace_pane = TabPane(f"Trace ({total})", id=tab_trace_id(mid))
        await tabs.add_pane(trace_pane)
        await self._mount_trace_content(trace_pane, m.tool_events)

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
        history = self._get_history()
        tabs = TabbedContent(id=ids.TABS_OVERVIEW)
        await container.mount(tabs)

        current_pane = TabPane("Current", id=ids.TAB_CURRENT)
        await tabs.add_pane(current_pane)
        await current_pane.mount_all([
            Static(self.rankings_summary(), id=ids.JUST_RANKINGS),
            Markdown(
                self.summary_text or self._EMPTY_SUMMARY,
                id=ids.JUST_PREVIEW,
            ),
            TextArea(
                self.summary_text, language="markdown",
                show_line_numbers=True, id=ids.JUST_EDITOR,
            ),
        ])
        self.query_one(f"#{ids.JUST_EDITOR}").display = False

        if history:
            await self._populate_history_tabs(tabs, history)

    async def _populate_proposal_model(self) -> None:
        """Lazily compose the proposal model view (Response, Trace, Diffs)."""
        if 0 in self._populated_models or not self.proposal:
            return
        self._populated_models.add(0)
        p = self.proposal
        mid = model_id(0)

        container = self.query_one(f"#{mid}", ScrollableContainer)
        total_events = sum(1 for e in p.tool_events if isinstance(e, dict))

        tabs = TabbedContent(id=model_tabs_id(mid))
        await container.mount(tabs)

        if p.trace_summary:
            response_pane = TabPane("Response", id=tab_response_id(mid))
            await tabs.add_pane(response_pane)
            rw: list = []
            if p.model_id:
                rw.append(Static(f"[bold]Model:[/bold] {p.model_id}"))
            rw.append(Static(RichMarkdown(bump_headings(p.trace_summary, 4))))
            await response_pane.mount_all(rw)

        trace_pane = TabPane(f"Trace ({total_events})", id=tab_trace_id(mid))
        await tabs.add_pane(trace_pane)
        await self._mount_trace_content(
            trace_pane, p.tool_events,
            model_id=p.model_id, bash_history=p.bash_history,
            setup_commands=p.setup_commands,
        )

        if p.file_diffs:
            diffs_pane = TabPane(f"Diffs ({len(p.file_diffs)})", id=tab_diffs_id(mid))
            await tabs.add_pane(diffs_pane)
            await diffs_pane.mount_all([
                LazyCollapsible.for_diff(fd.filename, fd.diff, "S", classes="inner")
                for fd in p.file_diffs
            ])

    async def _populate_proposal(self) -> None:
        """Lazily compose the proposal overview panel."""
        if self._overview_populated or not self.proposal:
            return
        self._overview_populated = True
        p = self.proposal

        container = self.query_one(f"#{ids.OVERVIEW}", ScrollableContainer)
        tabs = TabbedContent(id=ids.TABS_OVERVIEW)
        await container.mount(tabs)

        overview_pane = TabPane("Current", id=ids.TAB_CURRENT)
        await tabs.add_pane(overview_pane)
        ow: list = []
        if p.repo_url:
            ow.append(Link(p.repo_url, url=p.repo_url))
        if p.repo_description:
            ow.append(Static(f"[dim]{p.repo_description}[/dim]"))
        meta_parts = []
        if p.domain:
            meta_parts.append(f"[bold]Domain:[/bold] {p.domain}")
        if p.duration:
            duration_str = p.duration
            if p.trace_elapsed_ms:
                duration_str += f" (actual: {_format_duration(p.trace_elapsed_ms)})"
            meta_parts.append(f"[bold]Duration:[/bold] {duration_str}")
        elif p.trace_elapsed_ms:
            meta_parts.append(f"[bold]Duration:[/bold] {_format_duration(p.trace_elapsed_ms)}")
        if p.solved:
            color = {"full": "green", "partial": "yellow", "no": "red"}.get(p.solved, "white")
            meta_parts.append(f"[bold]Solved:[/bold] [{color}]{p.solved}[/{color}]")
        if p.model_id:
            meta_parts.append(f"[bold]Model:[/bold] [dim]{p.model_id}[/dim]")
        if meta_parts:
            ow.append(Static("  |  ".join(meta_parts)))
        if p.familiarity:
            ow.append(Static("[bold]Understanding:[/bold]"))
            ow.append(Markdown(p.familiarity))
        if p.difficulty:
            ow.append(Static("[bold]Difficulty:[/bold]"))
            ow.append(Markdown(p.difficulty))
        if p.rubrics:
            ow.append(Static(f"[bold]Rubrics ({len(p.rubrics)}):[/bold]"))
            rubric_md = "\n".join(f"{i}. {r}" for i, r in enumerate(p.rubrics, 1))
            ow.append(Markdown(rubric_md))
        if p.issues:
            ow.append(Static("[bold]Issues:[/bold]"))
            ow.append(Markdown(p.issues))
            for comment in p.issue_comments:
                author = comment.get("createdBy", {}).get("email", "unknown")
                ts = format_timestamp(comment.get("createdAt", ""))
                ow.append(
                    Static(f"\n[dim]{author} ({ts}):[/dim]\n{comment.get('content', '')}")
                )
        await overview_pane.mount_all(ow)

        await self._populate_history_tabs(tabs, self._get_history(), tab_offset=20)

    async def _populate_history_tabs(
        self, tabs: TabbedContent, history: list, tab_offset: int = 0
    ) -> None:
        """Populate history entry tabs into a TabbedContent widget."""
        is_proposal = self.task_type == TaskType.PROJECT_PROPOSAL
        tab_idx = tab_offset

        for orig_idx in range(len(history) - 1, -1, -1):
            entry = history[orig_idx]
            prev = history[orig_idx - 1] if orig_idx > 0 else None
            entry_fb = feedback_for_entry(history, orig_idx)

            if prev is None:
                changed = True
            elif is_proposal:
                changed = has_proposal_changes(prev, entry)
            else:
                changed = has_meaningful_changes(prev, entry)

            if not changed and not entry_fb:
                continue

            level = entry.get("reviewLevel", "?")
            prev = history[orig_idx - 1] if orig_idx > 0 else None
            if entry.get("isEditAction") and prev is not None:
                kind = "edit"
                level = prev.get("reviewLevel", level)
            else:
                kind = "revision" if changed else "review"
            pane = TabPane(f"L{level} {kind}", id=tab_entry_id(tab_idx))
            await tabs.add_pane(pane)

            widgets: list = []
            if not is_proposal:
                widgets.append(Static(format_history_entry(entry, orig_idx)))

            for fb in entry_fb:
                ts = fb.get("timestamp", "")
                ts_label = format_timestamp(ts) if ts else "unknown"
                widgets.append(
                    Collapsible(title=f"Feedback | {ts_label}", collapsed=False, classes="inner")
                )

            diff_statics: list[Static] = []
            if changed and prev:
                if is_proposal:
                    field_changes = proposal_all_changes(prev, entry)
                    if field_changes:
                        diff_statics.append(Static("\n".join(field_changes)))
                else:
                    rc = history_ranking_changes(prev, entry)
                    jt = history_justification_texts(prev, entry)
                    if rc:
                        diff_statics.append(Static("\n".join(rc)))
                    if jt:
                        from redlines import Redlines

                        diff_statics.append(Static(Redlines(jt[0], jt[1]).output_rich))

            diff_c = None
            if diff_statics:
                title = "Changes"
                diff_c = Collapsible(title=title, collapsed=False, classes="history-diff")
                widgets.append(diff_c)

            if not is_proposal and changed:
                just = history_justification(entry)
                if just:
                    widgets.append(Static("[bold]Justification:[/bold]", classes="section-title"))
                    widgets.append(Markdown(just))
                else:
                    widgets.append(Static("No justification.", classes="status"))

            await pane.mount_all(widgets)

            for fb_c, fb in zip(pane.query(".inner"), entry_fb, strict=False):
                await fb_c.query_one(Collapsible.Contents).mount(
                    Static(RichMarkdown(fb.get("message", "No message.")))
                )

            if diff_c:
                await diff_c.query_one(Collapsible.Contents).mount_all(diff_statics)

            tab_idx += 1

    async def on_mount(self) -> None:
        if self.task_type == TaskType.UNKNOWN:
            self.notify(f"Loaded task {self.task_id} (unsupported type)")
            self._maybe_show_tutorial()
            return
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            await self._populate_proposal_model()
            self.notify(f"Loaded proposal {self.task_id}")
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
        return s is not None and s.startswith("model-")

    def _is_on_overview(self) -> bool:
        return self._current_section == ids.OVERVIEW

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            return action not in (
                "vote_up", "vote_down", "go_model",
                "edit_justification", "copy_summary",
            ) and (action != "search_diffs" or self._is_on_model_view())

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
            if self.task_type == TaskType.PROJECT_PROPOSAL:
                await self._populate_proposal()
            else:
                await self._populate_overview()
            return
        if self.task_type == TaskType.PROJECT_PROPOSAL and section_id == model_id(0):
            await self._populate_proposal_model()
            return
        for i in range(len(self.models)):
            if model_id(i) == section_id:
                self.current_model_index = i
                await self._populate_model(i)
                return

    async def go_to_diff(
        self, model_index: int, filename: str, grep_line: str | None = None,
    ) -> None:
        is_proposal = self.task_type == TaskType.PROJECT_PROPOSAL
        if not is_proposal and model_index >= len(self.models):
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
                if grep_line:
                    self.call_later(
                        lambda c=collapsible, gl=grep_line: self._scroll_to_grep_line(
                            c, container, gl
                        )
                    )
                else:
                    self.call_later(
                        lambda c=collapsible: container.scroll_to_widget(c, top=True, animate=False)
                    )
                break

    def _scroll_to_grep_line(
        self, collapsible: Collapsible, container: ScrollableContainer, grep_line: str,
    ) -> None:
        """After expanding a collapsible, scroll to the first DiffDisplay line matching grep_line."""
        for diff_display in collapsible.query(DiffDisplay):
            lines = diff_display.diff_text.splitlines()
            for line_idx, line in enumerate(lines):
                if grep_line.strip() in line.strip():
                    diff_display.scroll_to(0, line_idx, animate=False)
                    diff_display.move_cursor((line_idx, 0))
                    container.scroll_to_widget(diff_display, top=True, animate=False)
                    return
        container.scroll_to_widget(collapsible, top=True, animate=False)

    async def action_go_to(self, section_id: str) -> None:
        await self.go_to(section_id)

    async def action_go_model(self, index: int) -> None:
        if 0 <= index < len(self.models):
            await self.go_to(model_id(index))

    async def action_go_model_proposal(self) -> None:
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            await self.go_to(model_id(0))

    def _active_tabbed_content(self) -> TabbedContent | None:
        """Return the TabbedContent widget in the currently visible view."""
        section = self._current_section
        if not section:
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
        idx = self.current_model_index
        return self.models[idx] if 0 <= idx < len(self.models) else None

    def action_search_diffs(self) -> None:
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            self._search_proposal_diffs()
            return
        m = self._current_model()
        if not m:
            self.notify("Navigate to a model first.", severity="warning")
            return
        if not m.file_diffs:
            self.notify("No diffs in this model.", severity="warning")
            return

        async def _on_result(result: DiffSearchResult | None) -> None:
            if result:
                await self.go_to_diff(result.model_index, result.filename, result.grep_line)

        self.push_screen(DiffSearchModal(self.current_model_index, m.file_diffs), _on_result)

    def _search_proposal_diffs(self) -> None:
        if not self.proposal or not self.proposal.file_diffs:
            self.notify("No diffs in this proposal.", severity="warning")
            return

        async def _on_result(result: DiffSearchResult | None) -> None:
            if result:
                await self.go_to_diff(result.model_index, result.filename, result.grep_line)

        self.push_screen(DiffSearchModal(0, self.proposal.file_diffs), _on_result)

    def action_search_events(self) -> None:
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            events = self.proposal.tool_events if self.proposal else []
        else:
            m = self._current_model()
            events = m.tool_events if m else []
        dict_events = [e for e in events if isinstance(e, dict)]
        if not dict_events:
            self.notify("No trace events.", severity="warning")
            return

        async def _on_result(event_index: int | None) -> None:
            if event_index is None:
                return
            await self._expand_trace_event(event_index, dict_events)

        self.push_screen(EventSearchModal(dict_events), _on_result)

    async def _expand_trace_event(self, event_index: int, events: list[dict]) -> None:
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            mid = model_id(0)
            await self.go_to(mid)
            tabs = self.query_one(f"#{model_tabs_id(mid)}", TabbedContent)
            tabs.active = tab_trace_id(mid)
            target_pane = tabs.get_pane(tab_trace_id(mid))
        else:
            m = self._current_model()
            if not m:
                return
            mid = model_id(self.current_model_index)
            await self.go_to(mid)
            tabs = self.query_one(f"#{model_tabs_id(mid)}", TabbedContent)
            tabs.active = tab_trace_id(mid)
            target_pane = tabs.get_pane(tab_trace_id(mid))

        trace_collapsibles = [
            c for c in target_pane.query(Collapsible)
            if "trace-event-c" in (c.classes or set())
        ]
        if not trace_collapsibles:
            for c in target_pane.query(LazyCollapsible):
                if c.lazy.events and not c.lazy.populated:
                    c.collapsed = False
                    await self.workers.wait_for_complete()
                    trace_collapsibles = [
                        cc for cc in target_pane.query(Collapsible)
                        if "trace-event-c" in (cc.classes or set())
                    ]
                    break

        if 0 <= event_index < len(trace_collapsibles):
            target = trace_collapsibles[event_index]
            target.collapsed = False
            self.call_later(lambda t=target: t.scroll_visible())



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
        idx = self.current_model_index
        if not self._is_on_model_view() or idx >= len(self.models):
            return
        self._apply_vote(idx, context=self._detect_vote_context(), delta=delta)

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

    def rankings_summary(self) -> str:
        if self.task_type == TaskType.PROJECT_PROPOSAL and self.proposal:
            p = self.proposal
            parts = []
            if p.solved:
                color = {"full": "green", "partial": "yellow", "no": "red"}.get(p.solved, "white")
                parts.append(f"[{color}]{p.solved}[/{color}]")
            if p.duration:
                parts.append(f"[dim]{p.duration}[/dim]")
            if p.trace_elapsed_ms:
                parts.append(f"[bold]{_format_duration(p.trace_elapsed_ms)} actual[/bold]")
            if p.rubrics:
                parts.append(f"[dim]{len(p.rubrics)} rubrics[/dim]")
            return "  |  ".join(parts) if parts else ""
        return ranking.rankings_summary(self.scores, self.data.get("history", []))

    def _update_scoreboard(self) -> None:
        try:
            board = self.query_one(f"#{ids.SCOREBOARD}", Static)
        except Exception:
            return
        board.update(self.rankings_summary())

        has_local_votes = any(s.any_nonzero() for s in self.scores)
        history = self.data.get("history", [])

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
        save_annotations(self.task_id, self.annotations, self.summary_text, self._server_justification)

    def _save_summary(self, text: str) -> None:
        """Update the summary text and persist."""
        self.summary_text = text
        save_annotations(self.task_id, self.annotations, self.summary_text, self._server_justification)

    def _refresh_overview_annotations(self) -> None:
        """Refresh the overview summary and rankings."""
        if not self._overview_populated:
            return
        try:
            rankings = self.query_one(f"#{ids.JUST_RANKINGS}", Static)
            rankings.update(self.rankings_summary())
            preview = self.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
            preview.update(self.summary_text or self._EMPTY_SUMMARY)
        except Exception:
            pass

    async def action_edit_justification(self) -> None:
        """Navigate to overview/current tab and activate the editor."""
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
            preview.update(self.summary_text or self._EMPTY_SUMMARY)
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
        "[bold]Search[/bold]\n"
        "  ctrl+f     search files (repeat to toggle fuzzy/grep)\n"
        "  ctrl+g     search events (repeat to toggle fuzzy/grep)\n\n"
        "[bold]Actions[/bold]\n"
        "  ctrl+e     edit summary\n"
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
        self.task_type = detect_task_type(new_data)
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            self.proposal = parse_proposal(self._get_history(), new_data.get("trace"))
        else:
            self.parsed = parse_content(new_data.get("content", {}))
            self.models = self.parsed.models
        self.task_id = (
            new_data.get("task", {}).get("taskId") or self.parsed.task_id or self.task_id
        )
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
