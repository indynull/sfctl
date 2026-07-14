"""Handler for project-proposal tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown as RichMarkdown
from textual.widgets import Link, Static, TabbedContent, TabPane

from sfctl import ids
from sfctl.formatting import bump_headings, format_timestamp, sanitize
from sfctl.handlers.base import TaskHandler
from sfctl.models import ParsedContent
from sfctl.proposal import (
    format_proposal_meta,
    has_proposal_changes,
    parse_proposal,
    proposal_all_changes,
    proposal_field_summary,
    solved_markup,
)
from sfctl.widgets import LazyCollapsible

if TYPE_CHECKING:
    from textual.containers import ScrollableContainer
    from textual.widgets import TabPane as TabPaneType

    from sfctl.models import ModelData


class ProposalHandler(TaskHandler):
    """Project proposal: single model with rubrics, issues, trace, diffs."""

    def parse(self) -> tuple[ParsedContent, list[ModelData]]:
        history = self.data.get("history", [])
        proposal = parse_proposal(history, self.data.get("trace"))
        self._app.proposal = proposal
        parsed = ParsedContent(
            task_id=self._app.task_id,
            repository=proposal.repo_url,
            current_prompt=proposal.prompt,
        )
        return parsed, []

    @property
    def model_count(self) -> int:
        return 1

    @property
    def has_model_tabs(self) -> bool:
        return True

    @property
    def supports_split(self) -> bool:
        return True

    def prompt_source(self) -> str:
        p = self._app.proposal
        return p.prompt if p else ""

    def response_source(self, idx: int) -> str:
        p = self._app.proposal
        return p.trace_summary if p else ""

    def model_header_label(self, idx: int) -> str:
        return "[bold]Model[/bold]"

    async def populate_overview(self, pane: TabPaneType) -> None:
        p = self._app.proposal
        if not p:
            return
        history = self._app._get_history()
        latest = history[-1] if history else {}
        ow: list = []
        if p.repo_url:
            ow.append(Link(p.repo_url, url=p.repo_url))
        if p.repo_description:
            ow.append(Static(f"[dim]{p.repo_description}[/dim]"))
        meta = format_proposal_meta(latest, elapsed_ms=p.trace_elapsed_ms, model_id=p.model_id)
        if meta:
            ow.append(Static(meta))
        if p.familiarity:
            ow.append(Static("[bold]Understanding:[/bold]"))
            ow.append(Static(RichMarkdown(p.familiarity)))
        if p.difficulty:
            ow.append(Static("[bold]Difficulty:[/bold]"))
            ow.append(Static(RichMarkdown(p.difficulty)))
        if p.rubrics:
            ow.append(Static(f"[bold]Rubrics ({len(p.rubrics)}):[/bold]"))
            rubric_md = "\n".join(f"{i}. {r}" for i, r in enumerate(p.rubrics, 1))
            ow.append(Static(RichMarkdown(rubric_md)))
        if p.issues:
            ow.append(Static("[bold]Issues:[/bold]"))
            ow.append(Static(RichMarkdown(p.issues)))
            for comment in p.issue_comments:
                email = comment.get("createdBy", {}).get("email", "unknown")
                author = email if self._app._show_emails else "reviewer"
                ts = format_timestamp(comment.get("createdAt", ""))
                w = Static(f"\n[dim]{author} ({ts}):[/dim]\n{comment.get('content', '')}")
                w._comment_email = email
                w._comment_ts = ts
                w._comment_content = comment.get("content", "")
                w.add_class("comment-meta")
                ow.append(w)
        await pane.mount_all(ow)

    async def populate_model(self, container: ScrollableContainer, idx: int) -> None:
        p = self._app.proposal
        if not p:
            return
        mid = container.id
        total_events = len(p.tool_events)
        tabs = TabbedContent(id=ids.model_tabs_id(mid))
        await container.mount(tabs)

        if p.trace_summary:
            response_pane = TabPane("Response", id=ids.tab_response_id(mid))
            await tabs.add_pane(response_pane)
            rw: list = []
            if p.model_id:
                rw.append(Static(f"[bold]Model:[/bold] {p.model_id}"))
            rw.append(Static(
                RichMarkdown(bump_headings(p.trace_summary, 4)),
                id="response-text-0",
            ))
            await response_pane.mount_all(rw)

        trace_pane = TabPane(f"Trace ({total_events})", id=ids.tab_trace_id(mid))
        await tabs.add_pane(trace_pane)
        await self._app._mount_trace_content(
            trace_pane, p.tool_events,
            model_id=p.model_id, bash_history=p.bash_history,
            setup_commands=p.setup_commands,
        )

        if p.file_diffs:
            diffs_pane = TabPane(f"Diffs ({len(p.file_diffs)})", id=ids.tab_diffs_id(mid))
            await tabs.add_pane(diffs_pane)
            await diffs_pane.mount_all([
                LazyCollapsible.for_diff(fd.filename, fd.diff, "S", classes="inner")
                for fd in p.file_diffs
            ])

    async def populate_unified_model(
        self, scroll: ScrollableContainer, idx: int,
    ) -> None:
        p = self._app.proposal
        if not p:
            return
        mid = ids.model_id(idx)
        tabs = TabbedContent(id=f"split-tabs-{mid}")
        await scroll.mount(tabs)

        if p.trace_summary:
            resp_pane = TabPane("Response", id=f"split-resp-{mid}")
            await tabs.add_pane(resp_pane)
            rw: list = []
            if p.model_id:
                rw.append(Static(f"[bold]Model:[/bold] {p.model_id}"))
            rw.append(Static(
                RichMarkdown(bump_headings(p.trace_summary, 4)),
                id="split-response-0",
            ))
            await resp_pane.mount_all(rw)

        trace_id = f"split-trace-{mid}"
        trace_pane = TabPane(f"Trace ({len(p.tool_events)})", id=trace_id)
        await tabs.add_pane(trace_pane)
        self._app._deferred_tabs[trace_id] = ("proposal-trace",)

        if p.file_diffs:
            diffs_id = f"split-diffs-{mid}"
            diffs_pane = TabPane(f"Diffs ({len(p.file_diffs)})", id=diffs_id)
            await tabs.add_pane(diffs_pane)
            self._app._deferred_tabs[diffs_id] = ("proposal-diffs",)

    async def populate_unified_overview(self, pane: TabPaneType) -> None:
        p = self._app.proposal
        if not p:
            return
        history = self._app._get_history()
        latest = history[-1] if history else {}
        ow: list = []
        meta = format_proposal_meta(latest, elapsed_ms=p.trace_elapsed_ms, model_id=p.model_id)
        if meta:
            ow.append(Static(meta))
        if p.familiarity:
            ow.append(Static("[bold]Understanding:[/bold]"))
            ow.append(Static(RichMarkdown(p.familiarity)))
        if p.difficulty:
            ow.append(Static("[bold]Difficulty:[/bold]"))
            ow.append(Static(RichMarkdown(p.difficulty)))
        if p.rubrics:
            ow.append(Static(f"[bold]Rubrics ({len(p.rubrics)}):[/bold]"))
            rubric_md = "\n".join(f"{i}. {r}" for i, r in enumerate(p.rubrics, 1))
            ow.append(Static(RichMarkdown(rubric_md)))
        if p.issues:
            ow.append(Static("[bold]Issues:[/bold]"))
            ow.append(Static(RichMarkdown(p.issues)))
        if ow:
            await pane.mount_all(ow)

    def has_changes(self, prev: dict, curr: dict) -> bool:
        return has_proposal_changes(prev, curr)

    def history_header(self, entry: dict, idx: int) -> Static:
        header = format_proposal_meta(entry)
        return Static(header) if header else Static("")

    def history_diff_widgets(self, prev: dict | None, curr: dict) -> list:
        if prev is None:
            initial = proposal_field_summary(curr)
            return [Static("\n".join(initial))] if initial else []
        statics: list = []
        for label, old_t, new_t in proposal_all_changes(prev, curr):
            if old_t is None and new_t is None:
                statics.append(Static(label))
            elif old_t and new_t:
                from redlines import Redlines

                statics.append(Static(f"[bold]{label}:[/bold]"))
                statics.append(Static(Redlines(old_t, new_t).output_rich))
            elif new_t:
                statics.append(Static(f"[bold]{label}:[/bold]"))
                statics.append(Static(f"[green]{sanitize(new_t)}[/green]"))
            else:
                statics.append(Static(f"[bold]{label}:[/bold]"))
                statics.append(Static(f"[red]{sanitize(old_t or '')}[/red]"))
        return statics

    def history_detail_widgets(self, entry: dict, changed: bool) -> list:
        return []

    def scoreboard_parts(self) -> list[str]:
        from sfctl.formatting import format_duration

        p = self._app.proposal
        if not p:
            return []
        parts: list[str] = []
        if p.solved:
            parts.append(solved_markup(p.solved))
        if p.duration:
            parts.append(f"[dim]{p.duration}[/dim]")
        if p.trace_elapsed_ms:
            parts.append(f"[bold]{format_duration(p.trace_elapsed_ms)} actual[/bold]")
        if p.rubrics:
            parts.append(f"[dim]{len(p.rubrics)} rubrics[/dim]")
        return parts

    def hidden_actions(self) -> frozenset[str]:
        return frozenset({
            "go_model", "vote_up", "vote_down",
            "edit_justification", "copy_summary",
            "yank_file", "reset_local",
        })

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "search_diffs":
            return self._app._is_on_model_view()
        return None
