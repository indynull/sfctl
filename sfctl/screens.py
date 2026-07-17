"""Modal screens for the Starfleet TUI."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, OptionList, Static, TextArea

from sfctl import ids
from sfctl.badges import path_badge_markup
from sfctl.constants import DIFF_ADD
from sfctl.diff import language_from_filename
from sfctl.formatting import format_event_line
from sfctl.fuzzy import MATCH_STYLE, fzf_match

if TYPE_CHECKING:
    from sfctl.models import FileDiff


class YankCommentModal(ModalScreen[tuple[int, str] | None]):
    """Modal to copy a diff snippet into the justification.

    Dismisses with (model_index, formatted_markdown) on submit, None on cancel.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
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
                f"{self.filename}:{self.line_ref}  "
                "(Enter to copy, Esc to cancel)",
                classes="section-title",
            )
            yield TextArea(
                self.snippet,
                read_only=True,
                show_line_numbers=False,
                id=ids.YANK_PREVIEW,
            )
            yield Input(placeholder="Optional comment", id=ids.YANK_COMMENT)

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
        Binding("escape", "dismiss", "Cancel", priority=True),
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
                f"{label}  (Enter to add, Esc to cancel)",
                classes="section-title",
            )
            if self.snippet:
                yield TextArea(
                    self.snippet,
                    read_only=True,
                    show_line_numbers=False,
                    id=ids.REVIEW_SNIPPET,
                )
            yield Input(placeholder="Comment", id=ids.REVIEW_COMMENT_INPUT)

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


class ViolationWhyModal(ModalScreen[str | None]):
    """Optional free-text why for a checklist violation note.

    Dismisses with the why string (possibly empty) on submit, None on cancel.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
    ]

    def __init__(self, model_letter: str, rule_label: str) -> None:
        super().__init__()
        self.model_letter = (model_letter or "?").strip().upper()[:1] or "?"
        self.rule_label = (rule_label or "Violation").strip()

    def compose(self) -> ComposeResult:
        with Container(id=ids.VIOLATION_WHY_MODAL):
            yield Label(
                f"Model {self.model_letter}  ·  {self.rule_label}\n"
                "[dim]Optional note · Enter to add · Esc to cancel[/dim]",
                classes="section-title",
            )
            yield Input(
                placeholder="Why this is a problem (optional)",
                id=ids.VIOLATION_WHY_INPUT,
            )

    def on_mount(self) -> None:
        self.query_one(f"#{ids.VIOLATION_WHY_INPUT}", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


_CQ_CATEGORY_COLORS: dict[str, str] = {
    "organisation": "#5f87ff",
    "organization": "#5f87ff",
    "prose": "#d7af5f",
    "formatting": "#5fd7d7",
    "other": "#af87ff",
}
_CQ_CATEGORY_FALLBACK = (
    "#5f87ff",
    "#d7af5f",
    "#5fd7d7",
    "#d75fd7",
    "#5fd75f",
    "#af87ff",
)
_CQ_MODEL_COLORS = ("#5f87ff", "#d7af5f", "#e05050")


def _cq_category_color(category: str, index: int = 0) -> str:
    key = (category or "").strip().lower()
    if key in _CQ_CATEGORY_COLORS:
        return _CQ_CATEGORY_COLORS[key]
    return _CQ_CATEGORY_FALLBACK[index % len(_CQ_CATEGORY_FALLBACK)]


class _ChecklistFilterInput(Input):
    """Filter field that does not swallow model hotkeys ``1`` / ``2`` / ``3``.

    While focused, a normal ``Input`` inserts digit keys into the value, so the
    modal's model-select bindings never run. Intercept those keys and forward
    them to the parent ``ChecklistMarkModal`` when model switching is allowed.
    """

    def on_key(self, event) -> None:
        if event.key not in ("1", "2", "3"):
            return
        screen = self.screen
        if getattr(screen, "_lock_model", False):
            return
        set_model = getattr(screen, "action_set_model", None)
        n_models = getattr(screen, "_n_models", 0)
        if not callable(set_model) or not n_models:
            return
        idx = int(event.key) - 1
        if 0 <= idx < n_models:
            set_model(idx)
            event.prevent_default()
            event.stop()


class ChecklistMarkModal(ModalScreen[None]):
    """Mark or clear code-quality checklist rules.

    Rules are grouped by category. Toggles apply immediately via *on_toggle*
    and the modal stays open for multi-mark; Escape dismisses.

    When *lock_model* is True (opened from a model response view), the target
    model is fixed to *initial_model* — no ``1``/``2``/``3`` switcher.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Done", priority=True),
        Binding("1", "set_model(0)", "A", show=True, priority=True),
        Binding("2", "set_model(1)", "B", show=True, priority=True),
        Binding("3", "set_model(2)", "C", show=True, priority=True),
    ]

    def __init__(
        self,
        catalog: list,
        selections: list[tuple[int, str]],
        *,
        n_models: int = 3,
        initial_model: int = 0,
        lock_model: bool = False,
        on_toggle: Callable[[int, str, bool], None] | None = None,
    ) -> None:
        super().__init__()
        self._catalog = list(catalog)
        self._selected = set(selections)
        self._n_models = max(1, min(3, n_models))
        self._model_idx = max(0, min(initial_model, self._n_models - 1))
        self._lock_model = lock_model
        self._on_toggle = on_toggle
        self._filter = ""
        self._categories: list[str] = []
        for rule in self._catalog:
            if rule.category not in self._categories:
                self._categories.append(rule.category)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return not (action == "set_model" and self._lock_model)

    def compose(self) -> ComposeResult:
        letter = chr(65 + self._model_idx)
        if self._lock_model:
            title = (
                f"Mark Code Quality · Model {letter}\n"
                "[dim]Type to filter · Enter to toggle · Esc when done[/dim]"
            )
        else:
            title = (
                "Mark Code Quality\n"
                "[dim]1 / 2 / 3 model · type to filter · Enter to toggle · "
                "Esc when done[/dim]"
            )
        with Container(id=ids.CHECKLIST_MARK_MODAL):
            yield Label(title, classes="section-title", id="checklist-mark-title")
            yield Static(self._model_label(), id=ids.CHECKLIST_MARK_MODEL)
            yield _ChecklistFilterInput(
                placeholder="Filter rules or category…",
                id=ids.CHECKLIST_MARK_FILTER,
            )
            yield OptionList(id=ids.CHECKLIST_MARK_LIST)

    def on_mount(self) -> None:
        self._rebuild_list()
        # Prefer the rule list so Enter toggles immediately; Tab reaches filter.
        try:
            self.query_one(f"#{ids.CHECKLIST_MARK_LIST}", OptionList).focus()
        except Exception:
            self.query_one(f"#{ids.CHECKLIST_MARK_FILTER}", Input).focus()

    def _model_label(self) -> Text:
        t = Text()
        if self._lock_model:
            color = _CQ_MODEL_COLORS[self._model_idx % len(_CQ_MODEL_COLORS)]
            ch = chr(65 + self._model_idx)
            t.append("Marking  ", style="dim")
            t.append(f" {ch} ", style=f"bold reverse {color}")
            n_marked = sum(1 for m, _ in self._selected if m == self._model_idx)
            t.append(f"  {n_marked} marked on this model", style="dim")
            return t
        t.append("Model  ")
        for i in range(self._n_models):
            ch = chr(65 + i)
            color = _CQ_MODEL_COLORS[i % len(_CQ_MODEL_COLORS)]
            if i == self._model_idx:
                t.append(f" {ch} ", style=f"bold reverse {color}")
            else:
                t.append(f" {ch} ", style=f"dim {color}")
            t.append(" ")
        n_marked = sum(1 for m, _ in self._selected if m == self._model_idx)
        t.append(f"  {n_marked} marked", style="dim")
        return t

    def _rebuild_list(self, *, keep_highlight: bool = False) -> None:
        from textual.widgets.option_list import Option

        ol = self.query_one(f"#{ids.CHECKLIST_MARK_LIST}", OptionList)
        prev_id: str | None = None
        if keep_highlight and ol.highlighted is not None:
            try:
                prev_id = str(ol.get_option_at_index(ol.highlighted).id or "")
            except Exception:
                prev_id = None
        ol.clear_options()
        q = self._filter.strip().lower()

        by_cat: dict[str, list] = {c: [] for c in self._categories}
        for rule in self._catalog:
            hay = f"{rule.category} {rule.title} {rule.choice_id}".lower()
            if q and q not in hay:
                continue
            by_cat.setdefault(rule.category, []).append(rule)

        options: list = []
        for cat_idx, category in enumerate(self._categories):
            rules = by_cat.get(category) or []
            if not rules:
                continue
            color = _cq_category_color(category, cat_idx)
            n_on = sum(
                1
                for r in rules
                if (self._model_idx, r.choice_id) in self._selected
            )
            header = Text()
            header.append("━ ", style=color)
            header.append(category, style=f"bold {color}")
            header.append(f"  ({n_on}/{len(rules)})", style=f"dim {color}")
            options.append(
                Option(header, id=f"__cat__{cat_idx}", disabled=True)
            )
            for rule in rules:
                marked = (self._model_idx, rule.choice_id) in self._selected
                row = Text()
                if marked:
                    row.append("  ●  ", style=f"bold {DIFF_ADD}")
                    row.append(rule.title, style=f"bold {color}")
                else:
                    row.append("  ·  ", style="dim")
                    row.append(rule.title, style=color)
                options.append(Option(row, id=rule.choice_id))

        if options:
            ol.add_options(options)
            restored = False
            if prev_id:
                for i, opt in enumerate(options):
                    if str(opt.id or "") == prev_id:
                        ol.highlighted = i
                        restored = True
                        break
            if not restored:
                for i, opt in enumerate(options):
                    if not str(opt.id or "").startswith("__"):
                        ol.highlighted = i
                        break
        else:
            ol.add_option(
                Option(Text("(no matching rules)", style="dim"), id="__none__")
            )

    def action_set_model(self, idx: int) -> None:
        if self._lock_model:
            return
        if idx < 0 or idx >= self._n_models:
            return
        if idx == self._model_idx:
            return
        self._model_idx = idx
        self.query_one(f"#{ids.CHECKLIST_MARK_MODEL}", Static).update(
            self._model_label()
        )
        self._rebuild_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != ids.CHECKLIST_MARK_FILTER:
            return
        self._filter = event.value
        self._rebuild_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != ids.CHECKLIST_MARK_FILTER:
            return
        ol = self.query_one(f"#{ids.CHECKLIST_MARK_LIST}", OptionList)
        if ol.option_count == 0:
            return
        if ol.highlighted is None:
            ol.highlighted = 0
        self._toggle_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != ids.CHECKLIST_MARK_LIST:
            return
        self._toggle_highlighted()

    def _toggle_highlighted(self) -> None:
        ol = self.query_one(f"#{ids.CHECKLIST_MARK_LIST}", OptionList)
        if ol.highlighted is None:
            return
        try:
            opt = ol.get_option_at_index(ol.highlighted)
        except Exception:
            return
        cid = str(opt.id or "")
        if not cid or cid.startswith("__"):
            return
        key = (self._model_idx, cid)
        now_selected = key not in self._selected
        if now_selected:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        if self._on_toggle is not None:
            self._on_toggle(self._model_idx, cid, now_selected)
            self.query_one(f"#{ids.CHECKLIST_MARK_MODEL}", Static).update(
                self._model_label()
            )
            self._rebuild_list(keep_highlight=True)
            return
        # Fallback: single-shot dismiss (tests / callers without callback).
        self.dismiss()


class CommentsModal(ModalScreen[str]):
    """Modal for viewing/editing reviewer comments.

    Toggles between rendered markdown and raw editor.
    Always dismisses with the current text.
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("ctrl+n", "toggle_edit", "Edit", show=True),
    ]

    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        from textual.widgets import Markdown

        with Container(id=ids.COMMENTS_MODAL):
            yield Label(
                "Comments  [dim]Ctrl+N to edit · Esc to close[/dim]",
                classes="section-title",
            )
            yield Markdown(
                self._text or "*No comments yet — press n to add one.*",
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
            preview.update(self._text or "*No comments yet — press n to add one.*")
            preview.display = True
        else:
            editor.text = self._text
            preview.display = False
            editor.display = True
            editor.focus()

    def action_close(self) -> None:
        """Leave edit mode first; dismiss on the next Esc press."""
        editor = self.query_one(f"#{ids.COMMENTS_EDITOR}", TextArea)
        if editor.display:
            from textual.widgets import Markdown

            self._text = editor.text
            editor.display = False
            preview = self.query_one(f"#{ids.COMMENTS_PREVIEW}", Markdown)
            preview.update(self._text or "*No comments yet — press n to add one.*")
            preview.display = True
            return
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
        Binding("escape", "dismiss", "Cancel", priority=True),
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._grep = False

    def compose(self) -> ComposeResult:
        with Container(id=self._container_id):
            yield Label(
                f"Fuzzy  [dim]{self._toggle_key_label} for content search[/dim]",
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
            label.update(
                f"Content  [dim]{self._toggle_key_label} for fuzzy match[/dim]"
            )
            inp.placeholder = self._grep_placeholder
        else:
            label.update(
                f"Fuzzy  [dim]{self._toggle_key_label} for content search[/dim]"
            )
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
    _toggle_key_label = "Ctrl+F"
    _fuzzy_placeholder = "Search files by name…"
    _grep_placeholder = "Search diff content…"

    BINDINGS = [
        *FuzzyGrepModal.BINDINGS,
        Binding("ctrl+f", "toggle_mode", "Switch Search Mode", show=False),
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
    _toggle_key_label = "Ctrl+G"
    _fuzzy_placeholder = "Search events by name…"
    _grep_placeholder = "Search event content…"

    BINDINGS = [
        *FuzzyGrepModal.BINDINGS,
        Binding("ctrl+g", "toggle_mode", "Switch Search Mode", show=False),
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
        Binding("escape", "dismiss", "Close", show=True, priority=True),
        Binding("q", "dismiss", "Close", show=False),
    ]

    def __init__(self, content: str, title: str = "Help"):
        super().__init__()
        self._content = content
        self._title = title

    def compose(self) -> ComposeResult:
        with Container(id="help-modal"):
            yield Label(self._title, classes="help-modal-title")
            with ScrollableContainer(id="help-modal-body"):
                yield Static(self._content, id="help-modal-content")
            yield Static("[dim]Esc[/] close", classes="help-modal-footer")

    def on_mount(self) -> None:
        try:
            self.query_one("#help-modal-body", ScrollableContainer).focus()
        except Exception:
            pass


class SharedCompareHelpScreen(ModalScreen[None]):
    """Structured, scannable help for the shared-file compare modal."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=True, priority=True),
        Binding("?", "dismiss", "Close", show=False),
        Binding("q", "dismiss", "Close", show=False),
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("down", "scroll_down", "Down", show=False),
        Binding("up", "scroll_up", "Up", show=False),
    ]

    def compose(self) -> ComposeResult:
        chip = path_badge_markup
        with Container(id="shared-help-modal"):
            yield Label("Compare Files", classes="shared-help-title")
            yield Static(
                "Compare how models A, B, and C change the [bold]same paths[/].  "
                "This is not a merge tool.",
                classes="shared-help-lede",
            )
            with ScrollableContainer(id="shared-help-body"):
                yield Static("File List", classes="shared-help-h")
                yield Static(
                    "The left column lists every path any model touched.\n"
                    "Files are [bold]grouped by which models changed them[/] "
                    "(A B C headers). Under each header:\n"
                    "  1. [bold]kind[/] + [bold]basename[/] + size (new/del)\n"
                    "  2. dim directory only\n"
                    "Order: kind (diff → share → new → del → same → solo), "
                    "then model set (ABC → pairs → solo), then path.\n"
                    "Full stats are in the detail header.",
                    classes="shared-help-p",
                )
                yield Static(
                    f"{chip('new')}   multi-model new path — cards top to bottom "
                    "(A, then B, then C)\n"
                    "\n"
                    f"{chip('del')}   full-file delete — removed body on the card\n"
                    "\n"
                    f"{chip('diff')}  same base site, different designs\n"
                    "         → banner groups alternate cards\n"
                    "\n"
                    f"{chip('share')} shared edit (plus unique-to-one-model cards)\n"
                    "\n"
                    f"{chip('same')}  full agreement on an edit — one card is enough\n"
                    "\n"
                    f"{chip('solo')}  one model touched this path",
                    classes="shared-help-block shared-help-legend",
                )

                yield Static("Reading the Detail", classes="shared-help-h")
                yield Static(
                    "1.  Filename, badge chips, and sizes appear at the top of "
                    "the right pane.\n"
                    "2.  Badge chips match the file list "
                    f"({chip('new')} {chip('del')} {chip('diff')} "
                    f"{chip('share')} {chip('same')} "
                    f"{chip('solo')}) — same chip family as CQ marks.\n"
                    "3.  Stats: [bold]unique[/] = change at a site in only one "
                    "model; [bold]split[/] = different designs; "
                    "[bold]pair[/] = two models match.\n"
                    "4.  Multi-design sites use [bold]tabs[/] under one site "
                    "title (not stacked cards). Solo / same sites stay "
                    "collapsibles.\n"
                    "5.  [bold]1 / 2 / 3[/] select the design that includes "
                    "model A / B / C on every multi-design site (synced). "
                    "Pair tabs like [bold]AC[/] count for both A and C. "
                    "Unique sites for that model expand; other models' unique "
                    "sites collapse.\n"
                    "6.  [bold]Shift+1 / 2 / 3[/] leave compare and open that "
                    "model's full Diffs tab for this path "
                    "(terminals may send [bold]![/] [bold]@[/] [bold]#[/]).\n"
                    "7.  Card titles use Title Case kind words "
                    "([bold]Shared[/], [bold]Pair[/], or [bold]A / B / C[/]) "
                    "plus a code preview.\n"
                    f"8.  Do [bold]not[/] look for a Shared card on {chip('diff')} "
                    "files.",
                    classes="shared-help-block",
                )

                yield Static("Filters", classes="shared-help-h")
                yield Static(
                    "Filters apply to the [bold]file list[/] and the detail cards:\n"
                    "[bold]c[/] Shared — share/same paths, or a multi-model same site\n"
                    "[bold]u[/] Unique — solo/new/del paths, or only-one-model sites\n"
                    "[bold]p[/] Pairs — paths with a 2-agree + 1-differ site\n"
                    "[bold]a[/] All paths\n",
                    classes="shared-help-block",
                )

                yield Static("Keys", classes="shared-help-h")
                yield Static(
                    "[bold cyan]j  k[/]          next / previous file\n"
                    "[bold cyan]1  2  3[/]        select design A / B / C (synced)\n"
                    "[bold cyan]Shift+1 2 3[/]   open full Diffs for A / B / C\n"
                    "[bold cyan]Tab[/]           next pane / control\n"
                    "[bold cyan]c u p a[/]       path filters (above)\n"
                    "[bold cyan]y[/]             copy focused diff into "
                    "justification\n"
                    "[bold cyan]?[/]             this help\n"
                    "[bold cyan]Esc[/]           close compare",
                    classes="shared-help-block shared-help-keys",
                )

                yield Static("Ranking Tip", classes="shared-help-h")
                yield Static(
                    "Start with multi-model [cyan]new[/] and core [yellow]diff[/] files.\n"
                    "Leave solo demos and tests for last.\n"
                    "When designs trade off, prefer a clean surface and a smaller "
                    "blast radius.",
                    classes="shared-help-block shared-help-tip",
                )
            yield Static(
                "[dim]j / k scroll · Esc close[/]",
                classes="shared-help-footer",
            )

    def on_mount(self) -> None:
        try:
            self.query_one("#shared-help-body", ScrollableContainer).focus()
        except Exception:
            pass

    def action_scroll_down(self) -> None:
        try:
            self.query_one("#shared-help-body", ScrollableContainer).scroll_down()
        except Exception:
            pass

    def action_scroll_up(self) -> None:
        try:
            self.query_one("#shared-help-body", ScrollableContainer).scroll_up()
        except Exception:
            pass


def _design_letters_from_label(label: str) -> str:
    """Leading model letter-run from a design tab label (``A``, ``AC``, …).

    Strips Rich markup so painted titles still resolve to ABC letters.
    """
    import re

    plain = re.sub(r"\[/?[^\]]*\]", "", label or "")
    head = plain.split()[0] if plain.split() else ""
    letters = "".join(ch for ch in head if ch in "ABC")
    return letters


def _section_model_run(sec: object) -> str:
    """ABC letter-run for models this detail section belongs to.

    Used to expand/collapse unique (and pair) collapsibles when the user
    picks a design with ``1``/``2``/``3``. Empty means neutral (Shared / full
    path cards) — leave expand state alone.
    """
    import re

    kind = str(getattr(sec, "kind", "") or "")
    # Agreement / full-path cards are not model-scoped unique sites.
    if kind in {"same", "identical", "region", "deleted"}:
        return ""
    parts = list(getattr(sec, "part_labels", None) or [])
    chars: list[str] = []
    for lab in parts:
        plain = re.sub(r"\[/?[^\]]*\]", "", str(lab))
        chars.extend(ch for ch in plain if ch in "ABC")
    if not chars:
        raw = str(getattr(sec, "model_letter", "") or "")
        plain = re.sub(r"\[/?[^\]]*\]", "", raw)
        chars = [ch for ch in plain if ch in "ABC"]
    if not chars:
        # Titles like ``A · RemoveArgs`` or ``Pair · site``.
        title = re.sub(r"\[/?[^\]]*\]", "", str(getattr(sec, "title", "") or ""))
        head = title.split("·", 1)[0].strip().split()
        if head:
            chars = [ch for ch in head[0] if ch in "ABC"]
    if not chars:
        return ""
    return "".join(sorted(set(chars), key="ABC".index))


class SharedCompareScreen(ModalScreen[tuple[int, str, str | None] | None]):
    """Cross-model file compare with selectable section groups.

    Each group is a collapsible of DiffDisplay widgets so snippets can be
    focused, selected, and copied with ``y``. Multi-design sites use tabs;
    ``1``/``2``/``3`` select the design that includes model A/B/C (synced
    across sites). ``Shift+1``/``2``/``3`` (or ``!``/``@``/``#``) leave the
    modal and open that model's full Diffs tab. Dismisses with
    ``(model_index, filename, jump_line)`` on open, or ``None`` on close.
    """

    BINDINGS = [
        Binding("?", "show_help", "Help", show=True),
        Binding("c", "filter_consensus", "Shared", show=True),
        Binding("u", "filter_unique", "Unique", show=True),
        Binding("p", "filter_pairs", "Pairs", show=True),
        Binding("a", "filter_all", "All", show=True),
        # Primary: stay in compare and pick a design (synced A/B/C tabs).
        Binding("1", "select_design(0)", "A", show=True),
        Binding("2", "select_design(1)", "B", show=True),
        Binding("3", "select_design(2)", "C", show=True),
        # Secondary: jump out to the full model Diffs tab for this path.
        Binding("shift+1", "open_model(0)", "Open A", show=True),
        Binding("shift+2", "open_model(1)", "Open B", show=True),
        Binding("shift+3", "open_model(2)", "Open C", show=True),
        # US layout: Shift+digit often arrives as punctuation, not shift+N.
        Binding("exclamation_mark", "open_model(0)", "Open A", show=False),
        Binding("at", "open_model(1)", "Open B", show=False),
        Binding("number_sign", "open_model(2)", "Open C", show=False),
        Binding("j", "cursor_down", "Next File", show=True),
        Binding("k", "cursor_up", "Previous File", show=True),
        Binding("tab", "focus_next", "Next Pane", show=False),
        Binding("y", "yank_focused", "Copy Snippet", show=True),
        Binding("escape", "dismiss", "Close", show=True, priority=True),
        Binding("d", "filter_unique", "Unique", show=False),
    ]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide design / open actions when that model has no patch for the file."""
        if action in ("open_model", "select_design") and parameters:
            idx = int(parameters[0])  # type: ignore[arg-type]
            cmp = self._current()
            if cmp is None:
                return False
            return not (idx < 0 or idx > 2 or cmp.empty_models[idx])
        return True

    # Title Case — matches footer bindings (Shared / Unique / Pairs / All).
    _FILTER_LABELS = {
        "all": "All",
        "consensus": "Shared",
        "unique": "Unique",
        "pairs": "Pairs",
        "diverge": "Diff",
    }

    def __init__(
        self,
        compares: list,
        initial_index: int = 0,
        *,
        model_colors: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        from sfctl.diff_compare import SharedFileCompare

        self._compares: list[SharedFileCompare] = list(compares)
        self._index = max(0, min(initial_index, len(self._compares) - 1)) if self._compares else 0
        self._filter = "all"
        self._detail_gen = 0
        self._model_colors = dict(model_colors or {})
        # option_index -> compare index (None for group headers)
        self._option_to_compare: list[int | None] = []
        # Preferred design letter-run (A / B / C / AC / …); synced across sites.
        self._design_letter_pref: str = ""
        # Single model letter last chosen with 1/2/3 — drives unique-site collapse.
        self._focus_model_letter: str = ""
        self._syncing_design_tabs: bool = False

    def _triage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self._compares:
            k = c.kind_badge()
            counts[k] = counts.get(k, 0) + 1
        return counts

    def _header_title(self) -> str:
        n = len(self._compares)
        multi = sum(1 for c in self._compares if c.n_models >= 2)
        counts = self._triage_counts()
        bits = [f"{n} files", f"{multi} multi-model"]
        # Chip tokens — same words as list badges and detail header.
        for key in ("diff", "new", "share", "same", "solo"):
            if counts.get(key):
                bits.append(f"{counts[key]} {key}")
        filt = self._FILTER_LABELS.get(self._filter, self._filter)
        if self._filter != "all":
            bits.append(f"Filter: {filt}")
        return "  ·  ".join(bits)

    def compose(self) -> ComposeResult:
        with Container(id=ids.SHARED_COMPARE_MODAL):
            yield Label(self._header_title(), id="shared-compare-title", classes="section-title")
            with Container(classes="shared-compare-layout"):
                yield OptionList(id=ids.SHARED_FILE_LIST)
                yield ScrollableContainer(id="shared-compare-detail")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_file_list()
        if self._compares:
            self._refresh_detail()
        try:
            self.query_one(f"#{ids.SHARED_FILE_LIST}", OptionList).focus()
        except Exception:
            pass

    def _compare_index_from_option(self, option_index: int | None) -> int | None:
        if option_index is None:
            return None
        if not (0 <= option_index < len(self._option_to_compare)):
            return None
        return self._option_to_compare[option_index]

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        if event.option_list.id != ids.SHARED_FILE_LIST:
            return
        ci = self._compare_index_from_option(event.option_index)
        if ci is None or ci == self._index:
            return
        self._index = ci
        self._refresh_detail()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != ids.SHARED_FILE_LIST:
            return
        ci = self._compare_index_from_option(event.option_index)
        if ci is None:
            return
        self._index = ci
        self._refresh_detail()

    def _current(self):
        if not self._compares:
            return None
        if not (0 <= self._index < len(self._compares)):
            return None
        return self._compares[self._index]

    def _refresh_detail(self) -> None:
        self._detail_gen += 1
        gen = self._detail_gen
        self.run_worker(self._rebuild_detail(gen), exclusive=True, name="shared-detail")

    async def _rebuild_detail(self, gen: int) -> None:
        """Mount header + one collapsible DiffDisplay section per group."""
        from textual.widgets import Collapsible

        from sfctl.diff_compare import build_compare_sections, compare_header_markup
        from sfctl.widgets import DiffDisplay

        if gen != self._detail_gen:
            return
        detail = self.query_one("#shared-compare-detail", ScrollableContainer)
        await detail.remove_children()
        if gen != self._detail_gen:
            return

        cmp = self._current()
        if cmp is None:
            await detail.mount(Static("[dim]No files to compare.[/]"))
            return

        try:
            title_w = self.query_one("#shared-compare-title", Label)
            title_w.update(self._header_title())
        except Exception:
            pass

        header = compare_header_markup(
            cmp,
            model_colors=self._model_colors,
            filter_mode=self._filter,
        )
        sections = build_compare_sections(
            cmp, filter_mode=self._filter, model_colors=self._model_colors,
        )
        widgets: list = [Static(header, id=ids.SHARED_COMPARE_SUMMARY)]
        widgets.append(
            Static(
                "[bold]?[/] help   "
                "[bold]j / k[/] files   "
                "[bold]1 / 2 / 3[/] design A/B/C   "
                "[bold]Shift+1/2/3[/] open Diffs   "
                "[bold]Tab[/] next pane   "
                "[bold]c u p a[/] filter   "
                "[bold]y[/] copy snippet   "
                "[bold]Esc[/] close",
                classes="shared-keys-hint",
            )
        )
        if not sections:
            widgets.append(
                Static(
                    "[dim]No sections for this filter. "
                    "Press [/][bold]a[/][dim] for all.[/]",
                    classes="shared-empty-hint",
                )
            )
        for sec in sections:
            title = sec.title
            kind_cls = f"shared-kind-{sec.kind}" if sec.kind else ""
            model_run = _section_model_run(sec)
            model_cls = (
                f"shared-models-{model_run}" if model_run else "shared-models-neutral"
            )
            classes = f"shared-section {kind_cls} {model_cls}".strip()
            if sec.is_banner:
                widgets.append(
                    Static(
                        f"[bold]{title}[/]",
                        classes=f"shared-region-banner {kind_cls}".strip(),
                    )
                )
                continue
            # Multi-design chunk: one site in the linear scroll; designs as tabs.
            if sec.design_tabs:
                from textual.widgets import TabbedContent

                # Empty TabbedContent; panes added after mount via add_pane.
                widgets.append(
                    Static(
                        f"[bold]{title}[/]",
                        classes=f"shared-region-banner {kind_cls}".strip(),
                    )
                )
                widgets.append(
                    TabbedContent(
                        id=f"dtabs-{sec.key}",
                        classes=f"shared-design-tabs {kind_cls}".strip(),
                    )
                )
                if not hasattr(self, "_pending_design_tabs"):
                    self._pending_design_tabs = []
                self._pending_design_tabs.append((f"dtabs-{sec.key}", sec, cmp.filename))
                continue
            if not sec.snip_diffs:
                widgets.append(
                    Collapsible(
                        Static("[dim](none)[/]"),
                        title=title,
                        collapsed=sec.collapsed,
                        classes=classes,
                    )
                )
                continue
            # One DiffDisplay per single-design section.
            displays = [
                DiffDisplay(
                    text,
                    sec.model_letter or "A",
                    cmp.filename,
                    classes="shared-section-diff",
                )
                for text in sec.snip_diffs
            ]
            widgets.append(
                Collapsible(
                    *displays,
                    title=title,
                    collapsed=sec.collapsed,
                    classes=classes,
                )
            )
        pending = getattr(self, "_pending_design_tabs", [])
        self._pending_design_tabs = []
        await detail.mount_all(widgets)
        if gen != self._detail_gen:
            return
        # Fill multi-design TabbedContent panes after mount.
        if pending:
            from textual.widgets import TabbedContent, TabPane

            for tid, sec, filename in pending:
                if gen != self._detail_gen:
                    return
                try:
                    tabbed = detail.query_one(f"#{tid}", TabbedContent)
                except Exception:
                    continue
                used_letters: set[str] = set()
                for ti, (tab_lab, snips) in enumerate(sec.design_tabs):
                    letters = _design_letters_from_label(tab_lab)
                    if not letters:
                        letters = (sec.model_letter or "A")[:1]
                    # Unique pane ids if two tabs somehow share a letter-run.
                    pane_letters = letters
                    if pane_letters in used_letters:
                        pane_letters = f"{letters}{ti}"
                    used_letters.add(pane_letters)
                    body = [
                        DiffDisplay(
                            text,
                            letters[:1] if letters else "A",
                            filename,
                            classes="shared-section-diff",
                        )
                        for text in snips
                    ]
                    if not body:
                        body = [Static("[dim](none)[/]")]
                    pane = TabPane(
                        tab_lab,
                        *body,
                        id=f"dtab-{sec.key}-{pane_letters}",
                    )
                    await tabbed.add_pane(pane)
                # Remember letter-run ids on the widget for sync (exact match).
                tabbed.set_class(True, "shared-design-tabs")
        if gen != self._detail_gen:
            return
        # Restore last design letter across sites (and after file switch).
        if self._design_letter_pref:
            self._sync_design_tabs(self._design_letter_pref)
        if self._focus_model_letter in "ABC":
            self._apply_model_site_collapse(self._focus_model_letter)
        detail.scroll_home(animate=False)
        self.call_after_refresh(lambda: self._scroll_detail_home())
        self.refresh_bindings()

    def on_tabbed_content_tab_activated(
        self, event: object,
    ) -> None:
        """When a design tab is chosen, select the same design on every site."""
        if self._syncing_design_tabs:
            return
        from textual.widgets import TabbedContent

        if not isinstance(event, TabbedContent.TabActivated):
            return
        tc = event.tabbed_content
        if "shared-design-tabs" not in tc.classes:
            return
        letters = ""
        pane = event.pane
        if pane is not None and pane.id:
            # Pane ids: dtab-{section-key}-{letters}
            suffix = pane.id.rsplit("-", 1)[-1]
            letters = "".join(ch for ch in suffix if ch in "ABC")
        if not letters and pane is not None:
            letters = _design_letters_from_label(str(getattr(pane, "name", "") or ""))
        if not letters:
            # Fall back to tab label text.
            try:
                letters = _design_letters_from_label(event.tab.label.plain)
            except Exception:
                letters = _design_letters_from_label(str(event.tab.label))
        if not letters:
            return
        self._design_letter_pref = letters
        self._sync_design_tabs(letters, source=tc)
        if len(letters) == 1 and letters in "ABC":
            self._focus_model_letter = letters
            self._apply_model_site_collapse(letters)

    def _pane_id_for_letters(self, tabbed: object, letters: str) -> str | None:
        """Return the TabPane id in *tabbed* whose design letter-run is *letters*."""
        from textual.widgets import TabPane

        if not letters:
            return None
        suffix = f"-{letters}"
        try:
            panes = tabbed.query(TabPane)  # type: ignore[union-attr]
        except Exception:
            return None
        for pane in panes:
            pid = pane.id or ""
            if pid.endswith(suffix):
                return pid
            # Also accept exact trailing letter-run after last hyphen.
            tail = pid.rsplit("-", 1)[-1] if pid else ""
            if tail == letters:
                return pid
        return None

    def _sync_design_tabs(
        self,
        letters: str,
        *,
        source: object | None = None,
    ) -> None:
        """Activate design tab *letters* on every multi-design site that has it."""
        if not letters:
            return
        from textual.widgets import TabbedContent

        self._syncing_design_tabs = True
        try:
            for tc in self.query(".shared-design-tabs"):
                if not isinstance(tc, TabbedContent):
                    continue
                if source is not None and tc is source:
                    continue
                target = self._pane_id_for_letters(tc, letters)
                if target is None:
                    continue
                try:
                    if tc.active != target:
                        tc.active = target
                except Exception:
                    # Pane may not be ready yet during rebuild races.
                    pass
        finally:
            self._syncing_design_tabs = False

    def _scroll_detail_home(self) -> None:
        try:
            self.query_one("#shared-compare-detail", ScrollableContainer).scroll_home(
                animate=False,
            )
        except Exception:
            pass

    def _status(self, msg: str) -> None:
        status = getattr(self.app, "_status", None)
        if callable(status):
            status(msg)

    def _set_filter(self, mode: str, label: str) -> None:
        self._filter = mode
        self._status(f"Filter: {label}")
        self._rebuild_file_list()
        self._refresh_detail()

    def _rebuild_file_list(self) -> None:
        """Rebuild left list for current path-level filter (c/u/p/a)."""
        from textual.widgets.option_list import Option

        from sfctl.diff_compare import (
            build_compare_list_entries,
            list_coverage_header_prompt,
            path_matches_filter,
        )

        opts = self.query_one(f"#{ids.SHARED_FILE_LIST}", OptionList)
        # Map option index -> index into full self._compares
        visible = [
            i
            for i, c in enumerate(self._compares)
            if path_matches_filter(c, self._filter)
        ]
        visible_cmps = [self._compares[i] for i in visible]
        opts.clear_options()
        self._option_to_compare = []
        highlight_opt: int | None = None
        # Entries are relative to visible_cmps; remap to full compares indices.
        for entry in build_compare_list_entries(visible_cmps):
            if entry.is_header:
                opts.add_option(
                    Option(
                        list_coverage_header_prompt(
                            entry.coverage,
                            entry.kind,
                            entry.count,
                            self._model_colors,
                        ),
                        disabled=True,
                    )
                )
                self._option_to_compare.append(None)
                continue
            full_i = visible[entry.compare_index]
            opts.add_option(
                Option(
                    self._compares[full_i].list_prompt(self._model_colors),
                    id=f"f{full_i}",
                )
            )
            self._option_to_compare.append(full_i)
            if full_i == self._index:
                highlight_opt = len(self._option_to_compare) - 1
        if highlight_opt is None and self._option_to_compare:
            # Current path hidden by filter — jump to first visible file.
            for opt_i, ci in enumerate(self._option_to_compare):
                if ci is not None:
                    highlight_opt = opt_i
                    self._index = ci
                    break
        if highlight_opt is not None:
            opts.highlighted = highlight_opt

    def action_show_help(self) -> None:
        self.app.push_screen(SharedCompareHelpScreen())

    def action_filter_consensus(self) -> None:
        self._set_filter("consensus", "Shared")

    def action_filter_unique(self) -> None:
        self._set_filter("unique", "Unique")

    def action_focus_next(self) -> None:
        """Default Tab: move focus through detail collapsibles / diffs."""
        self.screen.focus_next()

    def action_filter_pairs(self) -> None:
        self._set_filter("pairs", "Pairs")

    def action_filter_all(self) -> None:
        self._set_filter("all", "All")

    def action_cursor_down(self) -> None:
        opts = self.query_one(f"#{ids.SHARED_FILE_LIST}", OptionList)
        opts.action_cursor_down()

    def action_cursor_up(self) -> None:
        opts = self.query_one(f"#{ids.SHARED_FILE_LIST}", OptionList)
        opts.action_cursor_up()

    def action_yank_focused(self) -> None:
        """Copy the focused DiffDisplay snippet into the justification."""
        from sfctl.widgets import DiffDisplay

        focused = self.app.focused
        if not isinstance(focused, DiffDisplay):
            self._status("Focus a snippet first (Tab into a section).")
            return
        yank = getattr(self.app, "action_yank_file", None)
        if callable(yank):
            yank()
        else:
            self._status("Copy snippet is not available.")

    def on_diff_display_yank_requested(self, event) -> None:
        event.stop()
        self.action_yank_focused()

    def _design_runs_on_screen(self) -> set[str]:
        """Letter-runs currently available on multi-design TabbedContent panes."""
        from textual.widgets import TabPane

        runs: set[str] = set()
        for tc in self.query(".shared-design-tabs"):
            try:
                panes = tc.query(TabPane)
            except Exception:
                continue
            for pane in panes:
                pid = pane.id or ""
                tail = pid.rsplit("-", 1)[-1] if pid else ""
                letters = "".join(ch for ch in tail if ch in "ABC")
                if letters:
                    runs.add(letters)
        return runs

    def _design_run_for_model(self, model_idx: int) -> str | None:
        """Best design letter-run that includes *model_idx* (exact letter preferred)."""
        from sfctl.ids import model_letter

        letter = model_letter(model_idx)
        runs = self._design_runs_on_screen()
        if not runs:
            return None
        if letter in runs:
            return letter
        containing = [r for r in runs if letter in r]
        if not containing:
            return None
        # Prefer the tightest group (A over ABC when both somehow exist).
        return min(containing, key=len)

    def _apply_model_site_collapse(self, letter: str) -> None:
        """Expand collapsibles for *letter*; collapse unique sites for others.

        Multi-design sites already use tabs (synced separately). This targets
        single-design collapsibles: unique ``only A`` sites open when A is
        selected; B/C-only sites collapse so the linear view follows the
        chosen model.
        """
        if letter not in "ABC":
            return
        from textual.widgets import Collapsible

        for col in self.query(Collapsible):
            if "shared-section" not in col.classes:
                continue
            model_run = ""
            for cls in col.classes:
                if cls.startswith("shared-models-") and cls != "shared-models-neutral":
                    model_run = cls.removeprefix("shared-models-")
                    break
            if not model_run:
                # Shared / identical / neutral — leave expand state alone.
                continue
            if letter in model_run:
                col.collapsed = False
            else:
                col.collapsed = True

    def action_select_design(self, index: int) -> None:
        """Select design tabs for model A/B/C and focus that model's unique sites."""
        from sfctl.ids import model_letter

        cmp = self._current()
        if cmp is None:
            return
        letter = model_letter(index)
        if index < 0 or index > 2 or cmp.empty_models[index]:
            self._status(f"Model {letter} has no patch for this file")
            return
        chosen = self._design_run_for_model(index)
        self._design_letter_pref = chosen or letter
        self._focus_model_letter = letter
        if chosen is not None:
            self._sync_design_tabs(chosen)
        # Unique / pair collapsibles follow the selected *model* letter even
        # when the active design tab is a multi-letter run (e.g. AC).
        self._apply_model_site_collapse(letter)
        if chosen is None:
            self._status(
                f"Design {letter} — unique sites focused "
                f"(Shift+{index + 1} opens full Diffs)"
            )
        elif chosen == letter:
            self._status(f"Design {letter}")
        else:
            self._status(f"Design {chosen} (includes {letter})")

    def action_open_model(self, index: int) -> None:
        """Leave compare and open this model's full Diffs tab for the path."""
        from sfctl.diff_compare import jump_line_for_model
        from sfctl.ids import model_letter

        cmp = self._current()
        if cmp is None:
            return
        if cmp.empty_models[index] or not cmp.patches[index].strip():
            self._status(f"Model {model_letter(index)} has no patch for this file")
            return
        jump = jump_line_for_model(cmp, index)
        self.dismiss((index, cmp.filename, jump))

    def action_dismiss(self) -> None:
        self.dismiss(None)


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
