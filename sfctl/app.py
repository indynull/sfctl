"""Main Starfleet TUI application."""

from rich.markdown import Markdown as RichMarkdown
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
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
from sfctl.diff import parse_content, strip_diff_preamble
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
from sfctl.history import (
    feedback_for_entry,
    format_history_entry,
    has_meaningful_changes,
    history_justification,
    history_justification_texts,
    history_ranking_changes,
)
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
)
from sfctl.models import ModelData, ModelScores, ParsedContent, ProposalData
from sfctl.proposal import (
    format_proposal_entry,
    has_proposal_changes,
    parse_proposal,
    proposal_all_changes,
)
from sfctl.scoring import ReviewState
from sfctl.screens import HelpModal
from sfctl.search import SearchController
from sfctl.session import SessionHistory, TaskSession
from sfctl.task_types import TaskType, detect_task_type
from sfctl.voting import VotingController
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
        Binding("e", "toggle_collapse", "Fold", show=False),
        Binding("c", "copy_summary", "Copy", show=False),
        Binding("C", "copy_comments", "Copy Notes", show=False),
        Binding("y", "yank_file", "Yank", show=False),
        Binding("n", "add_comment", "Note", show=False),
        Binding("ctrl+n", "edit_comments", "Edit Notes", show=False),
        Binding("r", "refresh_data", "Refresh", show=False),
        Binding("ctrl+r", "reset_local", "Reset", show=False),
        Binding("?", "help", "Help", show=True),
        Binding("q", "quit", "Quit", show=True),
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

        self.review = ReviewState(self.task_id, len(self.models), self._get_history())
        self.scores: list[ModelScores] = self.review.scores
        self._populated_models: set[int] = set()
        self._overview_populated = False
        self._trace_type_map: dict[str, int] = {}
        self._voting = VotingController(self)
        self._search = SearchController(self)
        self._editor = EditorController(self)

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
                    f"[bold]Repo:[/bold] {sanitize(repo)}",
                    id=ids.REPO_BAR,
                )
            prompt = self.parsed.current_prompt or EM_DASH
            if self.task_type == TaskType.PROJECT_PROPOSAL and self.proposal:
                prompt = self.proposal.prompt or EM_DASH
            prompt = bump_headings(prompt)
            yield Static("[bold]Prompt[/bold]", id="prompt-label")
            with ScrollableContainer(id=ids.PROMPT_BAR):
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
                ContentSwitcher(initial=ids.OVERVIEW, id=ids.MAIN_SWITCHER),
            ):
                with ScrollableContainer(id=mid):
                    yield Static("[bold]Model[/bold]", classes="view-header", id=model_header_id(mid))
                with ScrollableContainer(id=ids.OVERVIEW):
                    pass
            return

        initial = ids.OVERVIEW
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

    async def _populate_model(self, idx: int) -> None:
        """Lazily compose a model view's content on first switch."""
        if idx in self._populated_models:
            return
        self._populated_models.add(idx)

        m = self.models[idx]
        mid = model_id(idx)
        letter = model_letter(idx)

        container = self.query_one(f"#{mid}", ScrollableContainer)
        total = len(m.tool_events)

        tabs = TabbedContent(id=model_tabs_id(mid))
        await container.mount(tabs)

        response_pane = TabPane("Response", id=tab_response_id(mid))
        await tabs.add_pane(response_pane)
        summary = self._model_summary_text(m)
        await response_pane.mount_all([
            self._voting.vote_bar(idx, "response"),
            Static(RichMarkdown(summary)),
        ])

        trace_pane = TabPane(f"Trace ({total})", id=tab_trace_id(mid))
        await tabs.add_pane(trace_pane)
        await self._mount_trace_content(trace_pane, m.tool_events)

        diffs_pane = TabPane(f"Diffs ({len(m.file_diffs)})", id=tab_diffs_id(mid))
        await tabs.add_pane(diffs_pane)
        diffs_widgets: list = [self._voting.vote_bar(idx, "code")]
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
                self.review.summary or self._EMPTY_SUMMARY,
                id=ids.JUST_PREVIEW,
            ),
            TextArea(
                self.review.summary, language="markdown",
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
        total_events = len(p.tool_events)

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
                duration_str += f" (actual: {format_duration(p.trace_elapsed_ms)})"
            meta_parts.append(f"[bold]Duration:[/bold] {duration_str}")
        elif p.trace_elapsed_ms:
            meta_parts.append(f"[bold]Duration:[/bold] {format_duration(p.trace_elapsed_ms)}")
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
            if is_proposal:
                header = format_proposal_entry(entry)
                if header:
                    widgets.append(Static(header))
            else:
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
        self._record_session()
        if self.task_type == TaskType.UNKNOWN:
            self.notify(f"Loaded task {self.task_id} (unsupported type)")
            self._maybe_show_tutorial()
            return
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            await self._populate_proposal()
            self.notify(f"Loaded proposal {self.task_id}")
            self._maybe_show_tutorial()
            return
        await self._populate_overview()
        if self.models:
            self.notify(f"Loaded task {self.task_id} ({len(self.models)} models)")
        else:
            self.notify(f"Loaded task {self.task_id}")
        self._update_scoreboard()
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

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if (
            self._overview_populated
            and event.tabbed_content.id == ids.TABS_OVERVIEW
            and str(event.pane.id) != ids.TAB_CURRENT
        ):
            self._editor.show_justification_preview()

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
        is_proposal = self.task_type == TaskType.PROJECT_PROPOSAL

        # Hide bindings that don't apply to this task type
        if is_proposal and action in (
            "go_model", "vote_up", "vote_down",
            "edit_justification", "copy_summary",
            "yank_file", "reset_local",
        ):
            return False
        if not is_proposal and action == "go_model_proposal":
            return False

        # Context-sensitive enable/disable
        if is_proposal:
            if action == "search_diffs":
                return self._is_on_model_view()
            return True

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
        await self._search.go_to_diff(model_index, filename, grep_line)

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
        self.notify("Reset to server state.")

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
                parts.append(f"[bold]{format_duration(p.trace_elapsed_ms)} actual[/bold]")
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

    async def action_edit_justification(self) -> None:
        await self._editor.edit_justification()

    def action_add_comment(self) -> None:
        self._editor.add_comment()

    def action_edit_comments(self) -> None:
        self._editor.edit_comments()

    def action_copy_comments(self) -> None:
        self._editor.copy_comments()

    def on_key(self, event) -> None:
        """Handle escape from justification editor to switch back to preview."""
        if event.key == "escape":
            self._editor.handle_escape_from_editor(event)

    _HELP_TEXT = (
        "[bold]Navigation[/bold]\n"
        "  1/2/3      switch to model A/B/C\n"
        "  0          overview (review, history, feedback)\n"
        "  tab        next tab within a view\n"
        "  shift+tab  previous tab\n"
        "  e          expand/collapse all in current tab\n\n"
        "[bold]Review[/bold]\n"
        "  +/-        vote (diff=code, response=response, else=overall)\n"
        "  y          yank diff snippet into justification\n"
        "  n          add a reviewer comment (note)\n\n"
        "[bold]Search[/bold]\n"
        "  ctrl+f     search files (repeat to toggle fuzzy/grep)\n"
        "  ctrl+g     search events (repeat to toggle fuzzy/grep)\n\n"
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
        self.review.reload(self.task_id, len(self.models), self._get_history())
        self.scores = self.review.scores
        self._trace_type_map = {}
        self.sub_title = f"Task {self.task_id} (refreshed)"
        self.refresh(recompose=True)
        self.call_later(self._post_refresh_populate)

    @work
    async def _post_refresh_populate(self) -> None:
        if self.task_type == TaskType.PROJECT_PROPOSAL:
            await self._populate_proposal_model()
        elif self.task_type != TaskType.UNKNOWN:
            await self._populate_overview()
        self._update_scoreboard()
        self.notify("Data refreshed.")

    def action_copy_summary(self) -> None:
        self._editor.copy_summary()

    async def action_quit(self) -> None:
        self._editor.save_summary_from_editor()
        self.exit()
