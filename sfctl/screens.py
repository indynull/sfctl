"""Modal screens for the Starfleet TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static, TextArea

from sfctl import ids
from sfctl.parsing import format_event_line

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
    """Scoped file search for the current model's diffs.

    Dismisses with (model_index, filename) on selection, None on cancel.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, model_index: int, file_diffs: list[FileDiff]):
        super().__init__()
        self.model_index = model_index
        self.file_diffs = file_diffs

    def compose(self) -> ComposeResult:
        with Container(id=ids.DIFF_SEARCH_MODAL):
            yield Input(placeholder="search files...", id=ids.DIFF_SEARCH_INPUT)
            yield OptionList(*[fd.filename for fd in self.file_diffs], id=ids.DIFF_SEARCH_LIST)

    def on_mount(self) -> None:
        self.query_one(f"#{ids.DIFF_SEARCH_INPUT}", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        option_list = self.query_one(f"#{ids.DIFF_SEARCH_LIST}", OptionList)
        option_list.clear_options()
        if not query:
            for fd in self.file_diffs:
                option_list.add_option(fd.filename)
        else:
            matcher = Matcher(query)
            scored = []
            for fd in self.file_diffs:
                score = matcher.match(fd.filename)
                if score > 0:
                    scored.append((score, fd.filename))
            for _, filename in sorted(scored, key=lambda x: -x[0]):
                option_list.add_option(filename)
        if option_list.option_count > 0:
            option_list.highlighted = 0

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        option_list = self.query_one(f"#{ids.DIFF_SEARCH_LIST}", OptionList)
        if option_list.option_count > 0 and option_list.highlighted is not None:
            filename = str(option_list.get_option_at_index(option_list.highlighted).prompt)
            self.dismiss((self.model_index, filename))

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        filename = str(event.option.prompt)
        self.dismiss((self.model_index, filename))


class EventSearchModal(ModalScreen[int | None]):
    """Fuzzy search over trace events by name/title."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, events: list[dict]):
        super().__init__()
        self.events = events
        self._indices: list[int] = list(range(len(events)))

    @staticmethod
    def _event_label(ev: dict) -> str:
        from rich.text import Text

        return Text.from_markup(format_event_line(ev)).plain

    def compose(self) -> ComposeResult:
        with Container(id=ids.EVENT_SEARCH_MODAL):
            yield Input(placeholder="search events...", id=ids.EVENT_SEARCH_INPUT)
            yield OptionList(
                *[self._event_label(ev) for ev in self.events],
                id=ids.EVENT_SEARCH_LIST,
            )

    def on_mount(self) -> None:
        self.query_one(f"#{ids.EVENT_SEARCH_INPUT}", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        option_list = self.query_one(f"#{ids.EVENT_SEARCH_LIST}", OptionList)
        option_list.clear_options()
        if not query:
            self._indices = list(range(len(self.events)))
            for i in self._indices:
                option_list.add_option(self._event_label(self.events[i]))
        else:
            matcher = Matcher(query)
            scored = []
            for i, ev in enumerate(self.events):
                label = self._event_label(ev)
                score = matcher.match(label)
                if score > 0:
                    scored.append((score, i))
            scored.sort(key=lambda x: -x[0])
            self._indices = [i for _, i in scored]
            for i in self._indices:
                option_list.add_option(self._event_label(self.events[i]))
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def _dismiss_selected(self, highlighted: int | None) -> None:
        if highlighted is not None and 0 <= highlighted < len(self._indices):
            self.dismiss(self._indices[highlighted])

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        option_list = self.query_one(f"#{ids.EVENT_SEARCH_LIST}", OptionList)
        if option_list.option_count > 0:
            self._dismiss_selected(option_list.highlighted)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        self._dismiss_selected(idx)


class GrepDiffsModal(ModalScreen[tuple[int, str] | None]):
    """Substring search across diff content, like ripgrep for diffs.

    Dismisses with (model_index, filename) on selection, None on cancel.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, model_index: int, file_diffs: list[FileDiff]):
        super().__init__()
        self.model_index = model_index
        self.file_diffs = file_diffs
        self._results: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with Container(id=ids.GREP_DIFFS_MODAL):
            yield Input(placeholder="grep diffs...", id=ids.GREP_DIFFS_INPUT)
            yield OptionList(id=ids.GREP_DIFFS_LIST)

    def on_mount(self) -> None:
        self.query_one(f"#{ids.GREP_DIFFS_INPUT}", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        option_list = self.query_one(f"#{ids.GREP_DIFFS_LIST}", OptionList)
        option_list.clear_options()
        self._results = []
        if not query:
            return
        for fd in self.file_diffs:
            for line in fd.diff.splitlines():
                if query in line.lower():
                    label = f"{fd.filename}: {line.strip()[:120]}"
                    if len(self._results) < 200:
                        self._results.append((fd.filename, label))
                        option_list.add_option(label)
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def _dismiss_selected(self, highlighted: int | None) -> None:
        if highlighted is not None and 0 <= highlighted < len(self._results):
            filename = self._results[highlighted][0]
            self.dismiss((self.model_index, filename))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        option_list = self.query_one(f"#{ids.GREP_DIFFS_LIST}", OptionList)
        if option_list.option_count > 0:
            self._dismiss_selected(option_list.highlighted)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._dismiss_selected(event.option_index)


class GrepEventsModal(ModalScreen[int | None]):
    """Substring search across event input/output content.

    Dismisses with the event index on selection, None on cancel.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, events: list[dict]):
        super().__init__()
        self.events = events
        self._indices: list[int] = []

    @staticmethod
    def _event_label(ev: dict) -> str:
        from rich.text import Text

        return Text.from_markup(format_event_line(ev)).plain

    @staticmethod
    def _searchable_text(ev: dict) -> str:
        parts = []
        for key in ("input", "output", "name", "title"):
            val = ev.get(key, "")
            if isinstance(val, dict):
                import json
                parts.append(json.dumps(val))
            elif val:
                parts.append(str(val))
        return "\n".join(parts).lower()

    def compose(self) -> ComposeResult:
        with Container(id=ids.GREP_EVENTS_MODAL):
            yield Input(placeholder="grep events...", id=ids.GREP_EVENTS_INPUT)
            yield OptionList(id=ids.GREP_EVENTS_LIST)

    def on_mount(self) -> None:
        self.query_one(f"#{ids.GREP_EVENTS_INPUT}", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        option_list = self.query_one(f"#{ids.GREP_EVENTS_LIST}", OptionList)
        option_list.clear_options()
        self._indices = []
        if not query:
            return
        for i, ev in enumerate(self.events):
            text = self._searchable_text(ev)
            if query in text:
                match_line = ""
                for line in text.splitlines():
                    if query in line:
                        match_line = line.strip()[:100]
                        break
                label = f"{self._event_label(ev)}  |  {match_line}"
                self._indices.append(i)
                option_list.add_option(label)
                if len(self._indices) >= 200:
                    break
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def _dismiss_selected(self, highlighted: int | None) -> None:
        if highlighted is not None and 0 <= highlighted < len(self._indices):
            self.dismiss(self._indices[highlighted])

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        option_list = self.query_one(f"#{ids.GREP_EVENTS_LIST}", OptionList)
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
