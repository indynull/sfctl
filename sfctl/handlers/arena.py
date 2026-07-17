"""Handler for arena ranking tasks (clarity checklist + multi-justification)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown as RichMarkdown
from textual.containers import Vertical
from textual.widgets import Button, Markdown, Static, TextArea

from sfctl import ids, ranking
from sfctl.arena import (
    EDITABLE_JUSTIFICATION_KEYS,
    arena_checklist_change_lines,
    arena_justification_diff_texts,
    arena_ranking_changes,
    checklist_from_entry,
    checklist_from_selections,
    checklist_violation_summary,
    empty_justification_hint,
    format_arena_history_meta,
    format_checklist_table,
    has_arena_changes,
    justification_sections,
    parse_arena_meta,
    section_header_hint,
    selections_with_titles,
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
    """Arena ranking: 3-model review with checklist and multi-field justifications.

    Model panes (response / trace / diffs) match classic ranking. Overview has
    three editable sections (response / code / overall) plus interactive
    checklist-violation chips that append notes under model headings.
    """

    def __init__(self, app: StarfleetApp, data: dict) -> None:
        super().__init__(app, data)
        self.meta = parse_arena_meta(data)
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
        from textual.containers import Vertical as Vert

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
                    wrap = app.query_one(f"#{wrap_id}", Vert)
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

    def _local_checklist(self):
        """Checklist table built from local code-quality selections."""
        app = self._app
        n = min(3, max(1, len(app.models)))
        return checklist_from_selections(
            app.review.checklist_selections,
            self.meta.checklist_catalog,
            n_models=n,
            rule_labels=self.meta.rule_labels,
        )

    def _checklist_widgets(
        self, entry: dict, *, heading: str, table_id: str | None = None
    ) -> list:
        """Clarity checklist table (history entry or empty placeholder)."""
        cl = checklist_from_entry(entry, self.meta.rule_labels)
        if not cl:
            return [Static("[dim]No clarity checklist in this history entry.[/dim]")]
        table = (
            Static(format_checklist_table(cl), id=table_id)
            if table_id
            else Static(format_checklist_table(cl))
        )
        return [
            Static(heading, classes="section-title"),
            table,
        ]

    def _preview_body(self, key: str) -> str:
        text = self._app.review.justification_text(key).strip()
        return text if text else empty_justification_hint(key)

    async def populate_overview(self, pane: TabPane) -> None:
        await self._mount_current_overview(pane, ns="")

    async def populate_unified_overview(self, pane: TabPane) -> None:
        # Same CQ + multi-field editors as the dedicated overview (unique -u ids).
        await self._mount_current_overview(pane, ns=ids.UNIFIED_NS)

    async def _mount_current_overview(self, pane: TabPane, *, ns: str) -> None:
        """Mount rankings, checklist, chips, and editable justifications.

        *ns* is ``\"\"`` for the main overview or ``ids.UNIFIED_NS`` for the
        unified-view strip so both can exist in the ContentSwitcher DOM.
        """
        app = self._app
        latest = self._latest_entry()
        await pane.mount(
            Static(app.rankings_summary(), id=ids.with_ns(ids.JUST_RANKINGS, ns))
        )

        cl = self._local_checklist()
        await pane.mount(
            Static(
                "[bold]Clarity Checklist[/bold]  "
                "[dim]v mark rule · chips note why[/dim]",
                classes="section-title",
            )
        )
        checklist_id = ids.with_ns(ids.ARENA_CHECKLIST, ns)
        if cl:
            await pane.mount(Static(format_checklist_table(cl), id=checklist_id))
        else:
            await pane.mount(
                Static(
                    "[dim]No violations marked — press v on a model response "
                    "(or overview) to mark code quality rules.[/dim]",
                    id=checklist_id,
                )
            )

        await pane.mount(
            Static(
                "[bold]Marked Violations[/bold]  "
                "[dim]v to add or clear · Enter on chip notes Response[/dim]",
                classes="section-title",
            )
        )
        chip_row = Vertical(
            id=ids.with_ns(ids.ARENA_VIOLATION_CHIPS, ns),
            classes="violation-chip-row",
        )
        await pane.mount(chip_row)
        from sfctl.badges import badge_css_classes

        await chip_row.mount(
            Button(
                "+ Mark Code Quality",
                classes=badge_css_classes("primary", "violation-chip", "violation-mark"),
                compact=True,
                flat=True,
            )
        )
        for model_idx, _choice_id, title in selections_with_titles(
            app.review.checklist_selections, self.meta.rule_labels
        ):
            letter = ids.model_letter(model_idx)
            short = title if len(title) <= 42 else title[:39] + "…"
            btn = Button(
                f"{letter}  {short}",
                classes=badge_css_classes("error", "violation-chip"),
                compact=True,
                flat=True,
            )
            btn._viol_model = model_idx  # type: ignore[attr-defined]
            btn._viol_label = title  # type: ignore[attr-defined]
            btn.tooltip = f"Note why · {letter} · {title}"
            await chip_row.mount(btn)

        for sec_label, sec_text in justification_sections(latest):
            if sec_label != "Prompt Understanding":
                continue
            await pane.mount(
                Static(
                    "[bold]Prompt Understanding[/bold]  [dim]server[/dim]",
                    classes="section-title",
                )
            )
            await pane.mount(Static(RichMarkdown(sec_text)))
            break

        for key, label in EDITABLE_JUSTIFICATION_KEYS:
            hint = section_header_hint(key)
            body = self._preview_body(key)
            section = Vertical(
                id=ids.just_section_id(key, ns), classes="just-section"
            )
            await pane.mount(section)
            await section.mount(
                Static(
                    f"[bold]{label}[/bold]  [dim]{hint}[/dim]",
                    classes="section-title",
                ),
                Markdown(
                    body,
                    id=ids.just_preview_id(key, ns),
                    classes="just-preview",
                ),
                TextArea(
                    app.review.justification_text(key),
                    language="markdown",
                    show_line_numbers=True,
                    id=ids.just_editor_id(key, ns),
                    classes="just-editor",
                ),
            )
            app.query_one(f"#{ids.just_editor_id(key, ns)}", TextArea).display = False

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
        rank_str = ranking.rankings_summary(app.scores, app._get_history())
        parts: list[str] = []
        if rank_str:
            parts.append(rank_str)
        history = app._get_history()
        if history:
            cl = checklist_from_entry(history[-1], self.meta.rule_labels)
            if cl:
                summary = checklist_violation_summary(cl)
                if summary:
                    parts.append(f"[dim]Checklist[/dim] {summary}")
        return parts

    def hidden_actions(self) -> frozenset[str]:
        return frozenset({"go_model_proposal"})
