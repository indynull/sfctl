"""Modal screens for the Starfleet TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static, TextArea

from sfctl import ids
from sfctl.parsing import format_event_line, language_from_filename

_MATCH_STYLE = Style(bold=True, color="cyan")

_SCORE_MATCH = 16
_BONUS_BOUNDARY = 8
_BONUS_BOUNDARY_WHITE = 10
_BONUS_CAMEL = 10
_BONUS_CONSECUTIVE = 4
_BONUS_FIRST_MULT = 2
_PENALTY_GAP_START = -3
_PENALTY_GAP_EXTEND = -1
_BOUNDARY_CHARS = frozenset("/-_. ,;:\\")


def _char_bonus(prev: str | None, curr: str) -> int:
    """Compute fzf-style position bonus for a character."""
    if prev is None:
        return _BONUS_BOUNDARY_WHITE
    if prev in _BOUNDARY_CHARS:
        return _BONUS_BOUNDARY_WHITE if prev in (" ", "\t") else _BONUS_BOUNDARY
    if prev.islower() and curr.isupper():
        return _BONUS_CAMEL
    return 0


def _fzf_score(query: str, candidate: str) -> tuple[float, list[int]]:
    """fzf-style fuzzy match: subsequence with boundary/consecutive bonuses.

    Returns (score, matched_positions).  Score <= 0 means no match.
    """
    ql = query.lower()
    cl = candidate.lower()
    n, m = len(cl), len(ql)
    if m == 0:
        return 0, []
    if m > n:
        return 0, []

    # Forward pass: find a valid subsequence (greedy left-to-right)
    positions: list[int] = []
    j = 0
    for i in range(n):
        if j < m and cl[i] == ql[j]:
            positions.append(i)
            j += 1
    if j < m:
        return 0, []

    # Score the alignment with bonuses
    score = 0.0
    consecutive = 0
    for k, pos in enumerate(positions):
        prev = candidate[pos - 1] if pos > 0 else None
        bonus = _char_bonus(prev, candidate[pos])
        char_score = _SCORE_MATCH + bonus
        if k == 0 and bonus > 0:
            char_score += bonus * (_BONUS_FIRST_MULT - 1)
        if consecutive > 0:
            char_score += _BONUS_CONSECUTIVE
        else:
            gap = pos - (positions[k - 1] + 1) if k > 0 else 0
            if gap > 0:
                char_score += _PENALTY_GAP_START + _PENALTY_GAP_EXTEND * (gap - 1)
        # Exact case match bonus
        if query[k] == candidate[pos]:
            char_score += 1
        score += max(0, char_score)
        if k > 0 and pos == positions[k - 1] + 1:
            consecutive += 1
        else:
            consecutive = 0

    return score, positions


def _highlight(text: str, positions: list[int]) -> Text:
    """Build a Rich Text with matched positions highlighted."""
    result = Text(text)
    for pos in positions:
        result.stylize(_MATCH_STYLE, pos, pos + 1)
    return result


def fzf_match(query: str, candidate: str) -> tuple[float, Text]:
    """Full fzf-style fuzzy match with highlighting.

    Returns (score, highlighted_text).  Score <= 0 means no match.
    Falls back to token-substring matching when subsequence fails.
    """
    score, positions = _fzf_score(query, candidate)
    if score > 0:
        return score, _highlight(candidate, positions)
    # Fallback: check if any query token is a substring of candidate
    ql = query.lower()
    cl = candidate.lower()
    best_score = 0.0
    best_positions: list[int] = []
    for token in _split_tokens(ql):
        idx = cl.find(token)
        if idx >= 0:
            t_positions = list(range(idx, idx + len(token)))
            bonus = _char_bonus(candidate[idx - 1] if idx > 0 else None, candidate[idx])
            t_score = len(token) * _SCORE_MATCH + bonus * 2
            if t_score > best_score:
                best_score = t_score
                best_positions = t_positions
    if best_score > 0:
        return best_score, _highlight(candidate, best_positions)
    return 0, Text(candidate)


def _split_tokens(query: str) -> list[str]:
    """Split a query into tokens on boundary characters."""
    import re

    return [t for t in re.split(r"[_\-./\s]+", query) if len(t) >= 2]

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


class DiffSearchResult:
    """Result from the diff search modal."""

    __slots__ = ("filename", "grep_line", "model_index")

    def __init__(self, model_index: int, filename: str, grep_line: str | None = None):
        self.model_index = model_index
        self.filename = filename
        self.grep_line = grep_line


class DiffSearchModal(ModalScreen[DiffSearchResult | None]):
    """File search with fuzzy filename matching and exact content grep.

    Toggle between modes with ctrl+f.
    Dismisses with DiffSearchResult (includes grep line for jumping).
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("ctrl+f", "toggle_mode", "Toggle fuzzy/grep", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
    ]

    def __init__(self, model_index: int, file_diffs: list[FileDiff]):
        super().__init__()
        self.model_index = model_index
        self.file_diffs = file_diffs
        self._grep = False
        self._results: list[tuple[str, str | None]] = []

    def compose(self) -> ComposeResult:
        with Container(id=ids.DIFF_SEARCH_MODAL):
            yield Label("fuzzy  [dim]ctrl+f to grep[/dim]", id="diff-search-mode")
            yield Input(placeholder="search files...", id=ids.DIFF_SEARCH_INPUT)
            yield OptionList(*[fd.filename for fd in self.file_diffs], id=ids.DIFF_SEARCH_LIST)

    def on_mount(self) -> None:
        self.query_one(f"#{ids.DIFF_SEARCH_INPUT}", Input).focus()

    def _move_highlight(self, delta: int) -> None:
        ol = self.query_one(f"#{ids.DIFF_SEARCH_LIST}", OptionList)
        if ol.option_count == 0:
            return
        current = ol.highlighted if ol.highlighted is not None else -1
        ol.highlighted = max(0, min(ol.option_count - 1, current + delta))
        ol.scroll_to_highlight()

    def action_cursor_up(self) -> None:
        self._move_highlight(-1)

    def action_cursor_down(self) -> None:
        self._move_highlight(1)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
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
        self._results = []
        new_options: list = []

        if self._grep:
            if query:
                q = query.lower()
                for fd in self.file_diffs:
                    for line in fd.diff.splitlines():
                        if q in line.lower() and len(self._results) < 200:
                            self._results.append((fd.filename, line.strip()))
                            display = Text(f"{fd.filename}: {line.strip()[:120]}")
                            # Highlight the matched portion in the line part
                            offset = len(fd.filename) + 2
                            line_lower = line.strip()[:120].lower()
                            mi = line_lower.find(q)
                            if mi >= 0:
                                display.stylize(
                                    _MATCH_STYLE, offset + mi, offset + mi + len(q),
                                )
                            new_options.append(display)
        elif not query:
            self._results = [(fd.filename, None) for fd in self.file_diffs]
            new_options = [fd.filename for fd in self.file_diffs]
        else:
            scored: list[tuple[float, Text, str]] = []
            for fd in self.file_diffs:
                score, display = fzf_match(query, fd.filename)
                if score > 0:
                    scored.append((score, display, fd.filename))
            scored.sort(key=lambda x: -x[0])
            for _, display, name in scored:
                self._results.append((name, None))
                new_options.append(display)

        option_list.set_options(new_options)
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_results(event.value)

    def _dismiss_at(self, idx: int) -> None:
        if 0 <= idx < len(self._results):
            filename, grep_line = self._results[idx]
            self.dismiss(DiffSearchResult(self.model_index, filename, grep_line))

    def _dismiss_highlighted(self) -> None:
        option_list = self.query_one(f"#{ids.DIFF_SEARCH_LIST}", OptionList)
        if option_list.option_count > 0 and option_list.highlighted is not None:
            self._dismiss_at(option_list.highlighted)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self._dismiss_highlighted()

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._dismiss_at(event.option_index)


class EventSearchModal(ModalScreen[int | None]):
    """Event search with fuzzy name matching and exact content grep.

    Toggle between modes with ctrl+g. Dismisses with event index.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("ctrl+g", "toggle_mode", "Toggle fuzzy/grep", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
    ]

    def __init__(self, events: list[dict]):
        super().__init__()
        self.events = events
        self._indices: list[int] = list(range(len(events)))
        self._grep = False

    @staticmethod
    def _event_label(ev: dict) -> str:
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

    def _move_highlight(self, delta: int) -> None:
        ol = self.query_one(f"#{ids.EVENT_SEARCH_LIST}", OptionList)
        if ol.option_count == 0:
            return
        current = ol.highlighted if ol.highlighted is not None else -1
        ol.highlighted = max(0, min(ol.option_count - 1, current + delta))
        ol.scroll_to_highlight()

    def action_cursor_up(self) -> None:
        self._move_highlight(-1)

    def action_cursor_down(self) -> None:
        self._move_highlight(1)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
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
                        label = self._event_label(ev)
                        display = Text(f"{label}  |  {match_line}")
                        mi = match_line.lower().find(q)
                        if mi >= 0:
                            offset = len(label) + 5
                            display.stylize(_MATCH_STYLE, offset + mi, offset + mi + len(q))
                        new_options.append(display)
                        self._indices.append(i)
                        if len(self._indices) >= 200:
                            break
        elif not query:
            self._indices = list(range(len(self.events)))
            new_options = [self._event_label(self.events[i]) for i in self._indices]
        else:
            scored: list[tuple[float, Text, int]] = []
            for i, ev in enumerate(self.events):
                label = self._event_label(ev)
                score, display = fzf_match(query, label)
                if score > 0:
                    scored.append((score, display, i))
            scored.sort(key=lambda x: -x[0])
            self._indices = [i for _, _, i in scored]
            new_options = [display for _, display, _ in scored]

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
