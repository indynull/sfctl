"""Modal screens for the Starfleet TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.style import Style
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static, TextArea

from sfctl import ids
from sfctl.parsing import format_event_line

_MATCH_STYLE = Style(bold=True, color="cyan")

if TYPE_CHECKING:
    from sfctl.models import FileDiff


class YankCommentModal(ModalScreen[tuple[int, str] | None]):
    """Modal to yank a diff snippet into the justification.

    Dismisses with (model_index, formatted_markdown) on submit, None on cancel.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(
        self,
        model_index: int,
        model_name: str,
        filename: str,
        snippet: str,
        line_ref: str,
    ):
        super().__init__()
        self.model_index = model_index
        self.model_name = model_name
        self.filename = filename
        self.snippet = snippet
        self.line_ref = line_ref

    def compose(self) -> ComposeResult:
        with Container(id=ids.YANK_MODAL):
            yield Label(
                f"{self.filename}:{self.line_ref}  (enter to yank, esc to cancel)",
                classes="section-title",
            )
            yield TextArea(
                self.snippet,
                read_only=True,
                show_line_numbers=False,
                id=ids.YANK_PREVIEW,
            )
            yield Input(placeholder="optional comment", id=ids.YANK_COMMENT)

    def on_mount(self) -> None:
        self.query_one(f"#{ids.YANK_COMMENT}", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        comment = event.value.strip()
        header = f"**{self.model_name}** `{self.filename}:{self.line_ref}`"
        if comment:
            header += f" {comment}"
        block = f"{header}\n```diff\n{self.snippet}\n```\n"
        self.dismiss((self.model_index, block))


class DiffSearchModal(ModalScreen[tuple[int, str] | None]):
    """File search with fuzzy filename matching and exact content grep.

    Toggle between modes with ctrl+f. Dismisses with (model_index, filename).
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("ctrl+f", "toggle_mode", "Toggle fuzzy/grep", show=False),
    ]

    def __init__(self, model_index: int, file_diffs: list[FileDiff]):
        super().__init__()
        self.model_index = model_index
        self.file_diffs = file_diffs
        self._grep = False
        self._result_filenames: list[str] = []

    def compose(self) -> ComposeResult:
        with Container(id=ids.DIFF_SEARCH_MODAL):
            yield Label("fuzzy  [dim]ctrl+f to grep[/dim]", id="diff-search-mode")
            yield Input(placeholder="search files...", id=ids.DIFF_SEARCH_INPUT)
            yield OptionList(*[fd.filename for fd in self.file_diffs], id=ids.DIFF_SEARCH_LIST)

    def on_mount(self) -> None:
        self.query_one(f"#{ids.DIFF_SEARCH_INPUT}", Input).focus()

    def action_toggle_mode(self) -> None:
        self._grep = not self._grep
        label = self.query_one("#diff-search-mode", Label)
        inp = self.query_one(f"#{ids.DIFF_SEARCH_INPUT}", Input)
        if self._grep:
            label.update("grep  [dim]ctrl+f to fuzzy[/dim]")
            inp.placeholder = "grep diff content..."
        else:
            label.update("fuzzy  [dim]ctrl+f to grep[/dim]")
            inp.placeholder = "search files..."
        self._refresh_results(inp.value)
        inp.focus()

    def _refresh_results(self, query_raw: str) -> None:
        query = query_raw.strip()
        option_list = self.query_one(f"#{ids.DIFF_SEARCH_LIST}", OptionList)
        self._result_filenames = []
        new_options: list = []

        if self._grep:
            if query:
                q = query.lower()
                for fd in self.file_diffs:
                    for line in fd.diff.splitlines():
                        if q in line.lower() and len(self._result_filenames) < 200:
                            self._result_filenames.append(fd.filename)
                            new_options.append(f"{fd.filename}: {line.strip()[:120]}")
        elif not query:
            self._result_filenames = [fd.filename for fd in self.file_diffs]
            new_options = list(self._result_filenames)
        else:
            matcher = Matcher(query, match_style=_MATCH_STYLE)
            scored = [
                (matcher.match(fd.filename), fd.filename)
                for fd in self.file_diffs
            ]
            scored.sort(key=lambda x: -x[0])
            for s, n in scored:
                if s > 0:
                    self._result_filenames.append(n)
                    new_options.append(matcher.highlight(n))

        option_list.set_options(new_options)
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_results(event.value)

    def _dismiss_highlighted(self) -> None:
        option_list = self.query_one(f"#{ids.DIFF_SEARCH_LIST}", OptionList)
        if option_list.option_count > 0 and option_list.highlighted is not None:
            idx = option_list.highlighted
            if 0 <= idx < len(self._result_filenames):
                self.dismiss((self.model_index, self._result_filenames[idx]))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self._dismiss_highlighted()

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if 0 <= idx < len(self._result_filenames):
            self.dismiss((self.model_index, self._result_filenames[idx]))


class EventSearchModal(ModalScreen[int | None]):
    """Event search with fuzzy name matching and exact content grep.

    Toggle between modes with ctrl+g. Dismisses with event index.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("ctrl+g", "toggle_mode", "Toggle fuzzy/grep", show=False),
    ]

    def __init__(self, events: list[dict]):
        super().__init__()
        self.events = events
        self._indices: list[int] = list(range(len(events)))
        self._grep = False

    @staticmethod
    def _event_label(ev: dict) -> str:
        from rich.text import Text

        return Text.from_markup(format_event_line(ev)).plain

    @staticmethod
    def _searchable_text(ev: dict) -> str:
        import json

        parts = []
        for val in ev.values():
            if isinstance(val, dict):
                parts.append(json.dumps(val))
            elif isinstance(val, str) and val:
                parts.append(val)
        return "\n".join(parts)

    def compose(self) -> ComposeResult:
        with Container(id=ids.EVENT_SEARCH_MODAL):
            yield Label("fuzzy  [dim]ctrl+g to grep[/dim]", id="event-search-mode")
            yield Input(placeholder="search events...", id=ids.EVENT_SEARCH_INPUT)
            yield OptionList(
                *[self._event_label(ev) for ev in self.events],
                id=ids.EVENT_SEARCH_LIST,
            )

    def on_mount(self) -> None:
        self.query_one(f"#{ids.EVENT_SEARCH_INPUT}", Input).focus()

    def action_toggle_mode(self) -> None:
        self._grep = not self._grep
        label = self.query_one("#event-search-mode", Label)
        inp = self.query_one(f"#{ids.EVENT_SEARCH_INPUT}", Input)
        if self._grep:
            label.update("grep  [dim]ctrl+g to fuzzy[/dim]")
            inp.placeholder = "grep event content..."
        else:
            label.update("fuzzy  [dim]ctrl+g to grep[/dim]")
            inp.placeholder = "search events..."
        self._refresh_results(inp.value)
        inp.focus()

    def _refresh_results(self, query_raw: str) -> None:
        query = query_raw.strip()
        option_list = self.query_one(f"#{ids.EVENT_SEARCH_LIST}", OptionList)
        self._indices = []
        new_options: list = []

        if self._grep:
            if query:
                q = query.lower()
                for i, ev in enumerate(self.events):
                    text = self._searchable_text(ev).lower()
                    if q in text:
                        match_line = ""
                        for line in text.splitlines():
                            if q in line:
                                match_line = line.strip()[:100]
                                break
                        new_options.append(f"{self._event_label(ev)}  |  {match_line}")
                        self._indices.append(i)
                        if len(self._indices) >= 200:
                            break
        elif not query:
            self._indices = list(range(len(self.events)))
            new_options = [self._event_label(self.events[i]) for i in self._indices]
        else:
            matcher = Matcher(query, match_style=_MATCH_STYLE)
            scored: list[tuple[float, int]] = []
            for i, ev in enumerate(self.events):
                label = self._event_label(ev)
                score = matcher.match(label)
                if score > 0:
                    scored.append((score, i, label))
            scored.sort(key=lambda x: -x[0])
            self._indices = [i for _, i, _ in scored]
            new_options = [matcher.highlight(label) for _, _, label in scored]

        option_list.set_options(new_options)
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_results(event.value)

    def _dismiss_selected(self, highlighted: int | None) -> None:
        if highlighted is not None and 0 <= highlighted < len(self._indices):
            self.dismiss(self._indices[highlighted])

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        option_list = self.query_one(f"#{ids.EVENT_SEARCH_LIST}", OptionList)
        if option_list.option_count > 0:
            self._dismiss_selected(option_list.highlighted)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._dismiss_selected(event.option_index)


class HelpModal(ModalScreen):
    """Scrollable modal for help text or tutorial content."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    def __init__(self, content: str, title: str = "Help"):
        super().__init__()
        self._content = content
        self._title = title

    def compose(self) -> ComposeResult:
        with Container(id="help-modal"):
            yield Label(self._title, classes="section-title")
            with ScrollableContainer():
                yield Static(self._content)


def _strip_markup(text: str) -> str:
    """Remove Rich markup tags from a string."""
    from rich.text import Text

    return Text.from_markup(text).plain


def build_clipboard_text(
    task_id: str,
    rankings_summary: str,
    summary_text: str,
) -> str:
    """Build plain-text summary of rankings and justification for clipboard."""
    parts = [f"Task: {task_id}"]
    rankings = _strip_markup(rankings_summary)
    if rankings:
        parts.append(f"\nRankings: {rankings}")
    if summary_text.strip():
        parts.append(f"\n{summary_text.strip()}")
    return "\n".join(parts)
