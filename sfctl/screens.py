"""Modal screens for the Starfleet TUI."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, TextArea

if TYPE_CHECKING:
    from sfctl.app import StarfleetApp
    from sfctl.models import FileDiff


class YankCommentModal(ModalScreen):
    """Modal to yank a snippet as a structured annotation with sentiment."""

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
        starfleet_app: StarfleetApp,
    ):
        super().__init__()
        self.model_index = model_index
        self.model_name = model_name
        self.filename = filename
        self.snippet = snippet
        self.line_ref = line_ref
        self.starfleet_app = starfleet_app
        self._sentiment = 0

    def compose(self) -> ComposeResult:
        with Container(id="yank-comment-modal"):
            yield Label(
                f"{self.filename}:{self.line_ref}  (enter to yank, esc to cancel)",
                classes="section-title",
            )
            yield TextArea(
                self.snippet,
                read_only=True,
                show_line_numbers=False,
                id="yank-preview",
            )
            with Horizontal(id="yank-sentiment"):
                yield Button("+1", id="yank-pos", variant="success")
                yield Button("0", id="yank-neu", variant="default", classes="selected")
                yield Button("-1", id="yank-neg", variant="error")
            yield Input(placeholder="optional comment", id="yank-comment")

    def on_mount(self) -> None:
        self.query_one("#yank-comment", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "yank-pos":
            self._sentiment = 1
        elif btn_id == "yank-neg":
            self._sentiment = -1
        else:
            self._sentiment = 0
        # Visual feedback: mark selected
        for b in self.query("#yank-sentiment Button"):
            b.remove_class("selected")
        event.button.add_class("selected")
        self.query_one("#yank-comment", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        from sfctl.models import Annotation

        comment = event.value.strip()
        annotation = Annotation(
            filename=self.filename,
            line_ref=self.line_ref,
            snippet=self.snippet,
            comment=comment,
            context="code",
            sentiment=self._sentiment,
        )
        self.starfleet_app.add_annotation(self.model_index, annotation)
        self.starfleet_app.notify(f"Yanked snippet from {self.filename}")
        self.dismiss()


class DiffSearchModal(ModalScreen):
    """Scoped file search for the current model's diffs."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, model_index: int, file_diffs: list[FileDiff], starfleet_app: StarfleetApp):
        super().__init__()
        self.model_index = model_index
        self.file_diffs = file_diffs
        self.starfleet_app = starfleet_app

    def compose(self) -> ComposeResult:
        with Container(id="diff-search-modal"):
            yield Input(placeholder="search files...", id="diff-search-input")
            yield OptionList(*[fd.filename for fd in self.file_diffs], id="diff-search-list")

    def on_mount(self) -> None:
        self.query_one("#diff-search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        option_list = self.query_one("#diff-search-list", OptionList)
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
        option_list = self.query_one("#diff-search-list", OptionList)
        if option_list.option_count > 0 and option_list.highlighted is not None:
            filename = str(option_list.get_option_at_index(option_list.highlighted).prompt)
            await self.starfleet_app.go_to_diff(self.model_index, filename)
            self.dismiss()

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        filename = str(event.option.prompt)
        await self.starfleet_app.go_to_diff(self.model_index, filename)
        self.dismiss()


def _strip_markup(text: str) -> str:
    """Remove Rich markup tags from a string."""
    return re.sub(r"\[/?[a-z_ #0-9-]*\]", "", text)


def build_clipboard_text(app: StarfleetApp) -> str:
    """Build plain-text summary of rankings and annotations for clipboard."""
    from sfctl.scoring import render_annotations_md

    parts = [f"Task: {app.task_id}"]
    rankings = _strip_markup(app.rankings_summary())
    if rankings:
        parts.append(f"\nRankings: {rankings}")
    rendered = render_annotations_md(app.annotations, app.summary_text)
    if rendered.strip():
        parts.append(f"\n{rendered}")
    return "\n".join(parts)
