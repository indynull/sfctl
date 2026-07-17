"""Abstract base for task-type handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from rich.markdown import Markdown as RichMarkdown
from textual.widgets import Static

from sfctl.history import (
    format_history_entry,
    history_justification,
    history_justification_texts,
    history_ranking_changes,
)

if TYPE_CHECKING:
    from textual.containers import ScrollableContainer
    from textual.widgets import TabPane

    from sfctl.app import StarfleetApp
    from sfctl.models import ModelData, ParsedContent


class TaskHandler(ABC):
    """Base class for task-type-specific TUI behavior.

    The app owns widget lifecycle, navigation, and bindings.
    The handler owns parsing, content rendering, and type-specific logic.
    """

    def __init__(self, app: StarfleetApp, data: dict) -> None:
        self._app = app
        self.data = data

    @abstractmethod
    def parse(self) -> tuple[ParsedContent, list[ModelData]]:
        """Parse raw data into ParsedContent and a list of ModelData."""

    @property
    @abstractmethod
    def model_count(self) -> int:
        """Number of model columns in unified view."""

    @property
    def has_model_tabs(self) -> bool:
        """Whether model views use Response / Trace / Diffs tabs."""
        return False

    @property
    def has_vote_bars(self) -> bool:
        """Whether vote +/- bars appear in model views."""
        return False

    @property
    def supports_split(self) -> bool:
        """Whether unified/split view is available."""
        return self.model_count >= 2

    def prompt_source(self) -> str:
        """Return the prompt text for this task."""
        return self._app.parsed.current_prompt or ""

    def response_source(self, idx: int) -> str:
        """Return the response text for model at idx."""
        models = self._app.models
        return models[idx].trace_summary or "" if idx < len(models) else ""

    def prepare_response_text(self, idx: int, text: str) -> str:
        """Transform response body before display."""
        return text

    def response_chrome_widgets(self, idx: int, id_prefix: str = "") -> list:
        """Extra widgets mounted above the response body (badges, metrics)."""
        return []

    def response_body_classes(self) -> str:
        """CSS classes for the response summary Static widget."""
        return ""

    def response_wrap_classes(self) -> str:
        """CSS classes for an optional host around the response summary.

        Empty means mount the response Static directly (classic ranking).
        """
        return ""

    def model_header_label(self, idx: int) -> str:
        """Build the header label for a model column."""
        from sfctl.ids import model_letter

        letter = model_letter(idx)
        models = self._app.models
        tag = models[idx].name if idx < len(models) else ""
        label = f"[bold]{letter}[/bold]"
        if tag:
            label += f"  [dim]{tag}[/dim]"
        return label

    @abstractmethod
    async def populate_overview(self, pane: TabPane) -> None:
        """Populate the Current overview tab pane."""

    def extra_overview_tabs(self) -> list[tuple[str, str, tuple]]:
        """Additional tabs to add after Current (before history).

        Returns list of (label, tab_id, deferred_key) tuples.
        """
        return []

    def shared_file_compares(self) -> list:
        """Shared-file comparisons for multi-model ranking (empty if N/A)."""
        return []

    @abstractmethod
    async def populate_model(self, container: ScrollableContainer, idx: int) -> None:
        """Populate a model view container."""

    async def populate_unified_model(
        self, scroll: ScrollableContainer, idx: int,
    ) -> None:
        """Populate a model column in the unified view.

        Default: same as populate_model. Override for different behavior.
        """
        await self.populate_model(scroll, idx)

    async def populate_unified_overview(self, pane: TabPane) -> None:
        """Populate the Current tab in the unified view overview.

        Default: same as populate_overview. Override for different content.
        """
        await self.populate_overview(pane)

    @abstractmethod
    def has_changes(self, prev: dict, curr: dict) -> bool:
        """Whether a history entry differs from its predecessor."""

    def history_header(self, entry: dict, idx: int) -> Static:
        """Build the header widget for a history entry."""
        w = Static(
            format_history_entry(entry, idx, show_email=self._app._show_emails),
            classes="history-meta",
        )
        w._entry_idx = idx
        return w

    def history_diff_widgets(self, prev: dict | None, curr: dict) -> list:
        """Build widgets showing changes between two history entries."""
        if prev is None:
            return []
        rc = history_ranking_changes(prev, curr)
        jt = history_justification_texts(prev, curr)
        statics: list = []
        if rc:
            statics.append(Static("\n".join(rc)))
        if jt:
            from redlines import Redlines

            statics.append(Static(Redlines(jt[0], jt[1]).output_rich))
        return statics

    def history_detail_widgets(self, entry: dict, changed: bool) -> list:
        """Widgets below the changes collapsible (justification, rationale)."""
        if not changed:
            return []
        just = history_justification(entry)
        if just:
            return [
                Static("[bold]Justification:[/bold]", classes="section-title"),
                Static(RichMarkdown(just)),
            ]
        return [Static("No justification.", classes="status")]

    @abstractmethod
    def scoreboard_parts(self) -> list[str]:
        """Return Rich-markup parts for the scoreboard (joined with |)."""

    def translatable_extras(self) -> list[tuple[str, str]]:
        """Extra (cache_key, source_text) pairs to translate beyond prompt+responses."""
        return []

    def apply_translated_extras(self, translated: dict[int | str, str]) -> None:  # noqa: B027
        """Apply translated extras to the UI."""

    def restore_extras(self) -> None:  # noqa: B027
        """Restore original text for extras after un-translate."""

    def hidden_actions(self) -> frozenset[str]:
        """Actions to always return False for in check_action."""
        return frozenset()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Type-specific action gate. Return None to defer to default logic."""
        return None
