"""Handler for arena ranking tasks (clarity checklist + multi-justification)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown as RichMarkdown
from textual.widgets import Static

from sfctl import ids, ranking
from sfctl.arena import (
    arena_checklist_change_lines,
    arena_justification_diff_texts,
    arena_ranking_changes,
    checklist_from_entry,
    checklist_violation_summary,
    combined_justification,
    format_arena_history_meta,
    format_checklist_table,
    has_arena_changes,
    justification_sections,
    parse_arena_meta,
)
from sfctl.cq_viewport import (
    RESPONSE_WIDTH_CLASS,
    RESPONSE_WRAP_CLASS,
    RESPONSE_WRAP_NARROW_CLASS,
)
from sfctl.handlers.ranking import RankingHandler

if TYPE_CHECKING:
    from textual.widgets import TabPane

    from sfctl.app import StarfleetApp


class ArenaHandler(RankingHandler):
    """Arena ranking: 3-model review with checklist and split justifications.

    Model panes (response / trace / diffs) match classic ranking. Overview and
    history are read-only surfaces for multi-field annotations.
    """

    def __init__(self, app: StarfleetApp, data: dict) -> None:
        super().__init__(app, data)
        self.meta = parse_arena_meta(data)
        # When True, response is framed at RESPONSE_TERMINAL_WIDTH columns.
        self.narrow_response: bool = False

    def model_header_label(self, idx: int) -> str:
        from sfctl.ids import model_letter

        return f"[bold]{model_letter(idx)}[/bold]"

    def response_body_classes(self) -> str:
        """CSS classes for the response summary Static."""
        return RESPONSE_WIDTH_CLASS if self.narrow_response else ""

    def response_wrap_classes(self) -> str:
        """CSS classes for the centering host around the response summary."""
        classes = RESPONSE_WRAP_CLASS
        if self.narrow_response:
            classes = f"{classes} {RESPONSE_WRAP_NARROW_CLASS}"
        return classes

    def toggle_response_width(self) -> bool:
        """Toggle standard-terminal width on response summaries; return new state."""
        self.narrow_response = not self.narrow_response
        self._apply_response_width()
        return self.narrow_response

    def clear_response_width(self) -> bool:
        """Turn off terminal-width mode if active. Returns True if it was on."""
        if not self.narrow_response:
            return False
        self.narrow_response = False
        self._apply_response_width()
        return True

    def _apply_response_width(self) -> None:
        """Add or remove terminal-width + center classes on mounted response widgets."""
        from textual.containers import Vertical

        app = self._app
        for idx in range(len(app.models)):
            for prefix in ("", "split-"):
                body_id = (
                    f"split-response-{idx}" if prefix else f"response-text-{idx}"
                )
                wrap_id = f"{prefix}response-wrap-{idx}"
                try:
                    body = app.query_one(f"#{body_id}", Static)
                except Exception:
                    body = None
                try:
                    wrap = app.query_one(f"#{wrap_id}", Vertical)
                except Exception:
                    wrap = None
                if body is not None:
                    if self.narrow_response:
                        body.add_class(RESPONSE_WIDTH_CLASS)
                    else:
                        body.remove_class(RESPONSE_WIDTH_CLASS)
                if wrap is not None:
                    if self.narrow_response:
                        wrap.add_class(RESPONSE_WRAP_NARROW_CLASS)
                    else:
                        wrap.remove_class(RESPONSE_WRAP_NARROW_CLASS)

    def _latest_entry(self) -> dict:
        history = self._app._get_history()
        return history[-1] if history else {}

    def _checklist_widgets(self, entry: dict, *, heading: str) -> list:
        """Read-only clarity checklist table for a history entry."""
        cl = checklist_from_entry(entry, self.meta.rule_labels)
        if not cl:
            return [Static("[dim]No clarity checklist in latest history.[/dim]")]
        return [
            Static(heading),
            Static(format_checklist_table(cl)),
        ]

    async def populate_overview(self, pane: TabPane) -> None:
        app = self._app
        latest = self._latest_entry()
        widgets: list = [
            Static(app.rankings_summary(), id=ids.JUST_RANKINGS),
        ]
        widgets.extend(
            self._checklist_widgets(
                latest, heading="[bold]Clarity checklist[/bold]"
            )
        )

        sections = justification_sections(latest)
        if sections:
            for label, text in sections:
                widgets.append(Static(f"[bold]{label}[/bold]", classes="section-title"))
                widgets.append(Static(RichMarkdown(text)))
        else:
            widgets.append(Static("[dim]No justifications in latest history.[/dim]"))

        await pane.mount_all(widgets)

    async def populate_unified_overview(self, pane: TabPane) -> None:
        latest = self._latest_entry()
        widgets: list = []
        cl = checklist_from_entry(latest, self.meta.rule_labels)
        if cl:
            widgets.extend(
                self._checklist_widgets(
                    latest, heading="[bold]Clarity checklist[/bold]"
                )
            )

        just = combined_justification(latest)
        if just:
            widgets.append(Static("[bold]Justifications[/bold]"))
            widgets.append(Static(RichMarkdown(just)))

        if widgets:
            await pane.mount_all(widgets)

    def history_header(self, entry: dict, idx: int) -> Static:
        w = Static(
            format_arena_history_meta(
                entry,
                idx,
                show_email=self._app._show_emails,
                rule_labels=self.meta.rule_labels,
            ),
            classes="history-meta",
        )
        w._entry_idx = idx
        return w

    def history_diff_widgets(self, prev: dict | None, curr: dict) -> list:
        if prev is None:
            return []
        statics: list = []
        rc = arena_ranking_changes(prev, curr)
        if rc:
            statics.append(Static("\n".join(rc)))
        cl_lines = arena_checklist_change_lines(prev, curr, self.meta.rule_labels)
        if cl_lines:
            statics.append(Static("\n".join(cl_lines)))
        jt = arena_justification_diff_texts(prev, curr)
        if jt:
            from redlines import Redlines

            statics.append(Static(Redlines(jt[0], jt[1]).output_rich))
        return statics

    def history_detail_widgets(self, entry: dict, changed: bool) -> list:
        if not changed:
            return []
        widgets: list = []
        cl = checklist_from_entry(entry, self.meta.rule_labels)
        if cl:
            widgets.extend(
                self._checklist_widgets(
                    entry, heading="[bold]Clarity checklist:[/bold]"
                )
            )
        sections = justification_sections(entry)
        if sections:
            for label, text in sections:
                widgets.append(Static(f"[bold]{label}:[/bold]", classes="section-title"))
                widgets.append(Static(RichMarkdown(text)))
        if not widgets:
            return [Static("No checklist or justifications.", classes="status")]
        return widgets

    def has_changes(self, prev: dict, curr: dict) -> bool:
        return has_arena_changes(prev, curr)

    def scoreboard_parts(self) -> list[str]:
        app = self._app
        rank_str = ranking.rankings_summary(app.scores, app.data.get("history", []))
        parts: list[str] = []
        if rank_str:
            parts.append(rank_str)
        history = app.data.get("history") or []
        if history:
            cl = checklist_from_entry(history[-1], self.meta.rule_labels)
            if cl:
                summary = checklist_violation_summary(cl)
                if summary:
                    parts.append(f"[dim]Checklist[/dim] {summary}")
        return parts

    def hidden_actions(self) -> frozenset[str]:
        return frozenset({"go_model_proposal", "edit_justification"})
