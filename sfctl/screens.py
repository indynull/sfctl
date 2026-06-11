"""Modal screens for the Starfleet TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static, TextArea

from sfctl import ids

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
