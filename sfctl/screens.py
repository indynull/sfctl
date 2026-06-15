"""Modal screens for the Starfleet TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static, TextArea

from sfctl import ids
from sfctl.diff import language_from_filename
from sfctl.formatting import format_event_line
from sfctl.fuzzy import MATCH_STYLE, fzf_match

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
        lang = language_from_filename(self.filename) or "diff"
        caption = f"**{self.model_name}** `{self.filename}:{self.line_ref}`"
        if comment:
            caption += f" — {comment}"
        block = f"{caption}\n```{lang}\n{self.snippet}\n```\n"
        self.dismiss((self.model_index, block))


class ReviewCommentModal(ModalScreen[str | None]):
    """Modal to add a reviewer comment with an optional snippet.

    Dismisses with a formatted markdown block on submit, None on cancel.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, snippet: str = "", context: str = "", lang: str = ""):
        super().__init__()
        self.snippet = snippet
        self.context = context
        self.lang = lang

    def compose(self) -> ComposeResult:
        label = self.context or "Add comment"
        with Container(id=ids.REVIEW_COMMENT_MODAL):
            yield Label(
                f"{label}  (enter to add, esc to cancel)",
                classes="section-title",
            )
            if self.snippet:
                yield TextArea(
                    self.snippet,
                    read_only=True,
                    show_line_numbers=False,
                    id=ids.REVIEW_SNIPPET,
                )
            yield Input(placeholder="comment", id=ids.REVIEW_COMMENT_INPUT)

    def on_mount(self) -> None:
        self.query_one(f"#{ids.REVIEW_COMMENT_INPUT}", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        comment = event.value.strip()
        if not comment and not self.snippet:
            return
        parts: list[str] = []
        if self.context:
            parts.append(f"**{self.context}**")
        if self.snippet:
            if self.lang:
                parts.append(f"```{self.lang}\n{self.snippet}\n```")
            else:
                quoted = "\n".join(f"> {line}" for line in self.snippet.splitlines())
                parts.append(quoted)
        if comment:
            parts.append(comment)
        block = "\n\n".join(parts) + "\n\n---\n"
        self.dismiss(block)


class CommentsModal(ModalScreen[str]):
    """Modal for viewing/editing reviewer comments.

    Toggles between rendered markdown and raw editor.
    Always dismisses with the current text.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+n", "toggle_edit", "Toggle Edit", show=True),
    ]

    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        from textual.widgets import Markdown

        with Container(id=ids.COMMENTS_MODAL):
            yield Label(
                "Comments  [dim]ctrl+n to edit, esc to close[/dim]",
                classes="section-title",
            )
            yield Markdown(
                self._text or "*No comments yet -- press n to add one.*",
                id=ids.COMMENTS_PREVIEW,
            )
            yield TextArea(
                self._text, language="markdown",
                show_line_numbers=True, id=ids.COMMENTS_EDITOR,
            )

    def on_mount(self) -> None:
        self.query_one(f"#{ids.COMMENTS_EDITOR}").display = False

    def action_toggle_edit(self) -> None:
        from textual.widgets import Markdown

        editor = self.query_one(f"#{ids.COMMENTS_EDITOR}", TextArea)
        preview = self.query_one(f"#{ids.COMMENTS_PREVIEW}", Markdown)
        if editor.display:
            self._text = editor.text
            editor.display = False
            preview.update(self._text or "*No comments yet -- press n to add one.*")
            preview.display = True
        else:
            editor.text = self._text
            preview.display = False
            editor.display = True
            editor.focus()

    def action_close(self) -> None:
        editor = self.query_one(f"#{ids.COMMENTS_EDITOR}", TextArea)
        if editor.display:
            self._text = editor.text
        self.dismiss(self._text)


class FuzzyGrepModal(ModalScreen):
    """Base class for fuzzy/grep search modals.

    Subclasses must set _container_id, _mode_label_id, _input_id, _list_id,
    _toggle_key_label, _fuzzy_placeholder, _grep_placeholder, and implement
    _build_initial_options(), _fuzzy_options(query), _grep_options(query),
    and _dismiss_at(idx).
    """

    _container_id: str
    _mode_label_id: str
    _input_id: str
    _list_id: str
    _toggle_key_label: str
    _fuzzy_placeholder: str
    _grep_placeholder: str

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._grep = False

    def compose(self) -> ComposeResult:
        with Container(id=self._container_id):
            yield Label(
                f"fuzzy  [dim]{self._toggle_key_label} to grep[/dim]",
                id=self._mode_label_id,
            )
            yield Input(placeholder=self._fuzzy_placeholder, id=self._input_id)
            yield OptionList(*self._build_initial_options(), id=self._list_id)

    def _build_initial_options(self) -> list:
        return []

    def on_mount(self) -> None:
        self.query_one(f"#{self._input_id}", Input).focus()

    def _move_highlight(self, delta: int) -> None:
        ol = self.query_one(f"#{self._list_id}", OptionList)
        if ol.option_count == 0:
            return
        current = ol.highlighted if ol.highlighted is not None else -1
        ol.highlighted = max(0, min(ol.option_count - 1, current + delta))
        ol.scroll_to_highlight()

    def action_cursor_up(self) -> None:
        self._move_highlight(-1)

    def action_cursor_down(self) -> None:
        self._move_highlight(1)

    def action_toggle_mode(self) -> None:
        self._grep = not self._grep
        label = self.query_one(f"#{self._mode_label_id}", Label)
        inp = self.query_one(f"#{self._input_id}", Input)
        if self._grep:
            label.update(f"grep  [dim]{self._toggle_key_label} to fuzzy[/dim]")
            inp.placeholder = self._grep_placeholder
        else:
            label.update(f"fuzzy  [dim]{self._toggle_key_label} to grep[/dim]")
            inp.placeholder = self._fuzzy_placeholder
        self._refresh_results(inp.value)
        inp.focus()

    def _refresh_results(self, query_raw: str) -> None:
        query = query_raw.strip()
        option_list = self.query_one(f"#{self._list_id}", OptionList)
        if self._grep:
            new_options = self._grep_options(query) if query else []
        elif not query:
            new_options = self._build_initial_options()
        else:
            new_options = self._fuzzy_options(query)
        option_list.set_options(new_options)
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def _fuzzy_options(self, query: str) -> list:
        return []

    def _grep_options(self, query: str) -> list:
        return []

    def _dismiss_at(self, idx: int) -> None:
        raise NotImplementedError

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_results(event.value)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one(f"#{self._list_id}", OptionList)
        if ol.option_count > 0 and ol.highlighted is not None:
            self._dismiss_at(ol.highlighted)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._dismiss_at(event.option_index)


class DiffSearchResult:
    """Result from the diff search modal."""

    __slots__ = ("filename", "grep_line", "model_index")

    def __init__(self, model_index: int, filename: str, grep_line: str | None = None):
        self.model_index = model_index
        self.filename = filename
        self.grep_line = grep_line


class DiffSearchModal(FuzzyGrepModal):
    """File search with fuzzy filename matching and exact content grep."""

    _container_id = ids.DIFF_SEARCH_MODAL
    _mode_label_id = "diff-search-mode"
    _input_id = ids.DIFF_SEARCH_INPUT
    _list_id = ids.DIFF_SEARCH_LIST
    _toggle_key_label = "ctrl+f"
    _fuzzy_placeholder = "search files..."
    _grep_placeholder = "grep diff content..."

    BINDINGS = [
        *FuzzyGrepModal.BINDINGS,
        Binding("ctrl+f", "toggle_mode", "Toggle fuzzy/grep", show=False),
    ]

    def __init__(self, model_index: int, file_diffs: list[FileDiff]):
        super().__init__()
        self.model_index = model_index
        self.file_diffs = file_diffs
        self._results: list[tuple[str, str | None]] = []

    def _build_initial_options(self) -> list:
        self._results = [(fd.filename, None) for fd in self.file_diffs]
        return [fd.filename for fd in self.file_diffs]

    def _fuzzy_options(self, query: str) -> list:
        self._results = []
        scored: list[tuple[float, Text, str]] = []
        for fd in self.file_diffs:
            score, display = fzf_match(query, fd.filename)
            if score > 0:
                scored.append((score, display, fd.filename))
        scored.sort(key=lambda x: -x[0])
        options: list = []
        for _, display, name in scored:
            self._results.append((name, None))
            options.append(display)
        return options

    def _grep_options(self, query: str) -> list:
        self._results = []
        options: list = []
        q = query.lower()
        for fd in self.file_diffs:
            for line in fd.diff.splitlines():
                if q in line.lower() and len(self._results) < 200:
                    self._results.append((fd.filename, line.strip()))
                    display = Text(f"{fd.filename}: {line.strip()[:120]}")
                    offset = len(fd.filename) + 2
                    line_lower = line.strip()[:120].lower()
                    mi = line_lower.find(q)
                    if mi >= 0:
                        display.stylize(MATCH_STYLE, offset + mi, offset + mi + len(q))
                    options.append(display)
        return options

    def _dismiss_at(self, idx: int) -> None:
        if 0 <= idx < len(self._results):
            filename, grep_line = self._results[idx]
            self.dismiss(DiffSearchResult(self.model_index, filename, grep_line))


class EventSearchModal(FuzzyGrepModal):
    """Event search with fuzzy name matching and exact content grep."""

    _container_id = ids.EVENT_SEARCH_MODAL
    _mode_label_id = "event-search-mode"
    _input_id = ids.EVENT_SEARCH_INPUT
    _list_id = ids.EVENT_SEARCH_LIST
    _toggle_key_label = "ctrl+g"
    _fuzzy_placeholder = "search events..."
    _grep_placeholder = "grep event content..."

    BINDINGS = [
        *FuzzyGrepModal.BINDINGS,
        Binding("ctrl+g", "toggle_mode", "Toggle fuzzy/grep", show=False),
    ]

    def __init__(self, events: list) -> None:
        super().__init__()
        self.events = events
        self._indices: list[int] = []

    @staticmethod
    def _event_label(ev: object) -> str:
        return Text.from_markup(format_event_line(ev)).plain

    @staticmethod
    def _searchable_text(ev) -> str:
        import dataclasses
        import json

        parts: list[str] = []
        for f in dataclasses.fields(ev):
            val = getattr(ev, f.name)
            if isinstance(val, dict):
                parts.append(json.dumps(val))
            elif isinstance(val, str) and val:
                parts.append(val)
        return "\n".join(parts)

    def _build_initial_options(self) -> list:
        self._indices = list(range(len(self.events)))
        return [self._event_label(ev) for ev in self.events]

    def _fuzzy_options(self, query: str) -> list:
        self._indices = []
        scored: list[tuple[float, Text, int]] = []
        for i, ev in enumerate(self.events):
            label = self._event_label(ev)
            score, display = fzf_match(query, label)
            if score > 0:
                scored.append((score, display, i))
        scored.sort(key=lambda x: -x[0])
        self._indices = [i for _, _, i in scored]
        return [display for _, display, _ in scored]

    def _grep_options(self, query: str) -> list:
        self._indices = []
        options: list = []
        q = query.lower()
        for i, ev in enumerate(self.events):
            text = self._searchable_text(ev).lower()
            if q in text:
                match_line = ""
                for line in text.splitlines():
                    if q in line:
                        match_line = line.strip()[:100]
                        break
                label = self._event_label(ev)
                display = Text(f"{label}  |  {match_line}")
                mi = match_line.lower().find(q)
                if mi >= 0:
                    offset = len(label) + 5
                    display.stylize(MATCH_STYLE, offset + mi, offset + mi + len(q))
                options.append(display)
                self._indices.append(i)
                if len(self._indices) >= 200:
                    break
        return options

    def _dismiss_at(self, idx: int) -> None:
        if 0 <= idx < len(self._indices):
            self.dismiss(self._indices[idx])


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


def strip_markup(text: str) -> str:
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
    rankings = strip_markup(rankings_summary)
    if rankings:
        parts.append(f"\nRankings: {rankings}")
    if summary_text.strip():
        parts.append(f"\n{summary_text.strip()}")
    return "\n".join(parts)
