"""Custom Textual widgets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual.binding import Binding
from textual.events import MouseDown, MouseMove, MouseUp
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Collapsible, Static, TextArea

from sfctl.constants import ARROW_DOWN, ARROW_RIGHT, ARROW_UP
from sfctl.diff import (
    DiffLine,
    build_diff_line_map,
    build_highlighted_sides,
    language_from_filename,
    parse_diff_lines,
)
from sfctl.formatting import sanitize


class SplitHandle(Widget):
    """Horizontal draggable handle for resizing top/bottom panels."""

    DEFAULT_CSS = "SplitHandle { height: 1; }"

    def __init__(self, top_id: str, bottom_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._top_id = top_id
        self._bottom_id = bottom_id
        self._dragging = False
        self._start_y = 0
        self._start_fraction = 0.5

    def render_line(self, y: int) -> Strip:
        return Strip([Segment("─" * self.size.width, self.rich_style)])

    def on_mouse_down(self, event: MouseDown) -> None:
        self._dragging = True
        self.capture_mouse()
        self._start_y = event.screen_y
        parent = self.parent
        if parent:
            top = parent.query_one(f"#{self._top_id}")
            bottom = parent.query_one(f"#{self._bottom_id}")
            total = top.size.height + bottom.size.height
            if total > 0:
                self._start_fraction = top.size.height / total

    def on_mouse_up(self, event: MouseUp) -> None:
        self._dragging = False
        self.release_mouse()

    def on_mouse_move(self, event: MouseMove) -> None:
        if not self._dragging:
            return
        parent = self.parent
        if not parent:
            return
        top = parent.query_one(f"#{self._top_id}")
        bottom = parent.query_one(f"#{self._bottom_id}")
        total = top.size.height + bottom.size.height
        if total <= 0:
            return
        delta = event.screen_y - self._start_y
        frac = self._start_fraction + (delta / total)
        frac = max(0.1, min(0.9, frac))
        top.styles.height = f"{round(frac * 100)}fr"
        bottom.styles.height = f"{round((1 - frac) * 100)}fr"

_KOTLIN_HIGHLIGHTS = """\
[
  "fun" "val" "var" "class" "object" "interface" "enum" "when" "if" "else"
  "for" "while" "do" "return" "throw" "try" "catch" "finally"
  "import" "package" "is" "as" "in" "by" "constructor" "companion" "init"
  "this" "super" "abstract" "final" "open" "override" "private" "protected"
  "public" "internal" "sealed" "data" "suspend" "tailrec" "operator" "infix"
  "inline" "external" "annotation" "crossinline" "noinline" "typealias"
  "lateinit" "const"
] @keyword
(string_literal) @string
(line_comment) @comment
(block_comment) @comment
(function_declaration (identifier) @function)
(call_expression (identifier) @function)
(class_declaration (identifier) @type)
(user_type) @type
"""


def _load_extra_language(lang: str) -> tuple[object | None, str | None]:
    """Load a tree-sitter language capsule and highlight query for non-builtin languages."""
    if lang == "c":
        import tree_sitter_c
        return tree_sitter_c.language(), tree_sitter_c.HIGHLIGHTS_QUERY
    if lang == "cpp":
        import tree_sitter_cpp
        return tree_sitter_cpp.language(), tree_sitter_cpp.HIGHLIGHTS_QUERY
    if lang == "ruby":
        import tree_sitter_ruby
        return tree_sitter_ruby.language(), tree_sitter_ruby.HIGHLIGHTS_QUERY
    if lang == "php":
        import tree_sitter_php
        return tree_sitter_php.language_php(), tree_sitter_php.HIGHLIGHTS_QUERY
    if lang in ("typescript", "tsx"):
        import importlib.resources

        import tree_sitter_typescript
        capsule = (
            tree_sitter_typescript.language_tsx()
            if lang == "tsx"
            else tree_sitter_typescript.language_typescript()
        )
        query = (
            importlib.resources.files("tree_sitter_typescript")
            / "queries"
            / "highlights.scm"
        ).read_text()
        return capsule, query
    if lang == "kotlin":
        import tree_sitter_kotlin
        return tree_sitter_kotlin.language(), _KOTLIN_HIGHLIGHTS
    return None, None


_MARKER_ADD = Style(color="#4ec94e", bold=True)
_MARKER_DEL = Style(color="#e05050", bold=True)
_MARKER_HUNK = Style(color="#5f87ff", bold=True)
_KIND_MARKER = {"add": ("+", _MARKER_ADD), "del": ("-", _MARKER_DEL), "hunk": ("~", _MARKER_HUNK)}

_TINT_ADD = (0, 20, 0)
_TINT_DEL = (20, 0, 0)
_TINT_HUNK = (0, 8, 18)
_KIND_TINT = {"add": _TINT_ADD, "del": _TINT_DEL, "hunk": _TINT_HUNK}


def _blend_bg(base: Color, tint: tuple[int, int, int]) -> Style:
    """Blend a tint into a base color to produce a subtle background style."""
    r, g, b = base.triplet
    tr, tg, tb = tint
    return Style(bgcolor=f"#{min(r+tr,255):02x}{min(g+tg,255):02x}{min(b+tb,255):02x}")


def _apply_side_highlights(
    parser: object,
    query: object,
    side_lines: list[str],
    side_map: list[int],
    diff_lines: list[DiffLine],
    accept_kinds: set[str],
    highlights: dict[int, list[tuple[int, int | None, str]]],
) -> None:
    """Parse *side_lines* with tree-sitter and map highlights back to unified indices.

    Only lines whose diff kind is in *accept_kinds* are written into
    *highlights*.  This lets us use new-side highlights for add/ctx lines
    and old-side highlights for del lines without double-applying context.
    """
    from tree_sitter import QueryCursor  # type: ignore[import-untyped]

    side_text = "\n".join(side_lines)
    tree = parser.parse(side_text.encode())  # type: ignore[union-attr]
    cursor = QueryCursor(query)
    captures = cursor.captures(tree.root_node)

    side_to_unified = {
        side_idx: unified_idx
        for side_idx, unified_idx in enumerate(side_map)
        if unified_idx >= 0
    }

    for highlight_name, nodes in captures.items():
        for node in nodes:
            s_row, s_col = node.start_point
            e_row, e_col = node.end_point

            if s_row == e_row:
                rows = [(s_row, s_col, e_col)]
            else:
                rows = [(s_row, s_col, None)]
                for mid in range(s_row + 1, e_row):
                    rows.append((mid, 0, None))
                rows.append((e_row, 0, e_col))

            for side_row, col_start, col_end in rows:
                unified_idx = side_to_unified.get(side_row)
                if unified_idx is None:
                    continue
                if diff_lines[unified_idx].kind not in accept_kinds:
                    continue
                highlights[unified_idx].append((col_start, col_end, highlight_name))


class DiffDisplay(TextArea):
    """Read-only TextArea showing diffs with tree-sitter syntax highlighting
    and subtle background shading for added/deleted lines."""

    class VoteRequested(Message):
        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class YankRequested(Message):
        pass

    BINDINGS = [
        Binding("+", "vote_up", f"{ARROW_UP} Code", show=True),
        Binding("-", "vote_down", f"{ARROW_DOWN} Code", show=True),
        Binding("y", "yank", "Yank", show=True),
    ]

    def action_vote_up(self) -> None:
        self.post_message(self.VoteRequested(1))

    def action_vote_down(self) -> None:
        self.post_message(self.VoteRequested(-1))

    def action_yank(self) -> None:
        self.post_message(self.YankRequested())

    def original_lines(self, start: int, end: int) -> str:
        """Return original diff lines (with +/- prefixes) for a line range."""
        lines = self._diff_lines[start : end + 1]
        return "\n".join(dl.source for dl in lines)

    def __init__(self, text: str, model_name: str, filename: str, **kwargs):
        diff_lines = parse_diff_lines(text)
        self._diff_lines = diff_lines
        clean_text = "\n".join(dl.text for dl in diff_lines)
        lang = language_from_filename(filename)
        super().__init__(
            clean_text, read_only=True,
            show_line_numbers=True, soft_wrap=False, **kwargs,
        )
        self._lang_name = lang
        self._register_extra_language(lang)
        if lang:
            self.language = lang
        self.diff_text = text
        self.model_name = model_name
        self.filename = filename
        self._diff_line_map = build_diff_line_map(text)
        max_num = max(self._diff_line_map.values()) if self._diff_line_map else 0
        self._gutter_width = len(str(max_num)) + 1

    def _register_extra_language(self, lang: str | None) -> None:
        if not lang or lang in self.available_languages:
            return
        try:
            from tree_sitter import Language
            ts_lang, query = _load_extra_language(lang)
            if ts_lang and query:
                self.register_language(lang, Language(ts_lang), query)
        except (ImportError, OSError):
            pass

    def _build_highlight_map(self) -> None:
        """Build highlights by parsing old/new sides of the diff separately.

        The default TextArea implementation parses the displayed text as a
        single document, but that text is a unified diff with interleaved
        additions and deletions — not valid source code.  Instead we split
        the diff into old-side (ctx+del) and new-side (ctx+add), parse each
        with tree-sitter, and map the resulting highlights back onto the
        unified line indices.
        """
        self._line_cache.clear()
        highlights = self._highlights
        highlights.clear()

        if not self._highlight_query or not self._lang_name:
            return

        try:
            from tree_sitter import Parser
        except ImportError:
            return

        # The tree-sitter Language lives on the SyntaxAwareDocument that
        # TextArea already created.  The prepared highlight query is in
        # self._highlight_query.
        doc = self.document
        ts_lang = getattr(doc, "language", None)
        if ts_lang is None:
            return

        parser = Parser(ts_lang)

        new_lines, new_map, old_lines, old_map = build_highlighted_sides(
            self._diff_lines,
        )

        _apply_side_highlights(
            parser, self._highlight_query, new_lines, new_map,
            self._diff_lines, {"add", "ctx", "hunk"}, highlights,
        )
        _apply_side_highlights(
            parser, self._highlight_query, old_lines, old_map,
            self._diff_lines, {"del"}, highlights,
        )

    _bg_cache: dict[str, Style] | None = None
    _bg_cache_key: tuple[int, int, int] | None = None

    def _get_kind_bg(self, kind: str) -> Style | None:
        tint = _KIND_TINT.get(kind)
        if not tint:
            return None
        theme = self._theme
        base = (
            theme.base_style.bgcolor if theme and theme.base_style else None
        ) or self.background_colors[1]
        if hasattr(base, "triplet") and base.triplet:
            rgb = (base.triplet.red, base.triplet.green, base.triplet.blue)
        else:
            rgb = (30, 30, 30)
        if self._bg_cache is not None and self._bg_cache_key == rgb:
            return self._bg_cache.get(kind)
        self._bg_cache_key = rgb
        base_color = Color.from_rgb(rgb[0], rgb[1], rgb[2])
        self._bg_cache = {k: _blend_bg(base_color, t) for k, t in _KIND_TINT.items()}
        return self._bg_cache.get(kind)

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        line_index = y + self.scroll_offset.y

        if self.show_line_numbers and self._diff_line_map is not None:
            gutter_style = self.get_component_rich_style("text-area--gutter")
            real_num = self._diff_line_map.get(line_index)
            gutter_text = (
                f"{real_num:>{self._gutter_width}} "
                if real_num is not None
                else " " * (self._gutter_width + 1)
            )
            gutter = Strip([Segment(gutter_text, gutter_style)])
            orig_gutter_len = self.gutter_width
            strip = strip.crop(orig_gutter_len, strip.cell_length)
            strip = Strip.join([gutter, strip])

        if 0 <= line_index < len(self._diff_lines):
            kind = self._diff_lines[line_index].kind
            marker_info = _KIND_MARKER.get(kind)
            if marker_info:
                char, marker_style = marker_info
                marker = Strip([Segment(char, marker_style)])
            else:
                marker = Strip([Segment(" ", Style.null())])
            strip = Strip.join([marker, strip])

            bg = self._get_kind_bg(kind)
            if bg:
                new_segments = []
                for seg_text, seg_style, control in strip._segments:
                    if seg_style and seg_style.bgcolor:
                        new_segments.append(Segment(seg_text, seg_style, control))
                    else:
                        merged = (seg_style + bg) if seg_style else bg
                        new_segments.append(Segment(seg_text, merged, control))
                strip = Strip(new_segments, strip.cell_length)

        return strip


@dataclass
class _LazyPayload:
    """Metadata for lazy-loaded content in a Collapsible."""

    populated: bool = False
    diff: str | None = None
    letter: str = ""
    filename: str = ""
    events: list = field(default_factory=list)


class LazyCollapsible(Collapsible):
    """A Collapsible that carries lazy-load metadata without ad-hoc attributes."""

    def __init__(self, *args, lazy: _LazyPayload | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.lazy = lazy or _LazyPayload()

    @classmethod
    def for_diff(cls, filename: str, diff: str, letter: str, **kwargs) -> LazyCollapsible:
        return cls(
            title=filename,
            collapsed=True,
            lazy=_LazyPayload(diff=diff, letter=letter, filename=filename),
            **kwargs,
        )

    @classmethod
    def for_trace(cls, title: str, events: list, **kwargs) -> LazyCollapsible:
        return cls(
            title=title,
            collapsed=True,
            lazy=_LazyPayload(events=events),
            **kwargs,
        )


def format_value(v: object, max_len: int = 120) -> str:
    """Format a single value for inline display, truncating long strings."""
    if v is None:
        return "[dim italic]null[/]"
    if isinstance(v, bool):
        return f"[dim]{v}[/]"
    if isinstance(v, (int, float)):
        return f"[dim]{v}[/]"
    if isinstance(v, str):
        s = sanitize(v, max_len)
        return f"[dim]{s}[/]" if s else '[dim italic]""[/]'
    if isinstance(v, list):
        if not v:
            return "[dim italic](empty list)[/]"
        items = ", ".join(sanitize(str(x), 40) for x in v[:5])
        suffix = f" ... +{len(v) - 5}" if len(v) > 5 else ""
        return f"[dim]({items}{suffix})[/]"
    if isinstance(v, dict):
        if not v:
            return "[dim italic]{...}[/]"
        items = ", ".join(
            f"{sanitize(str(k), 20)}={sanitize(str(val), 30)}" for k, val in list(v.items())[:4]
        )
        suffix = f" ... +{len(v) - 4}" if len(v) > 4 else ""
        return f"[dim]{items}{suffix}[/]"
    return f"[dim]{sanitize(str(v), max_len)}[/]"


def try_parse(v: object) -> object:
    """If v is a JSON string, parse it into a dict/list."""
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return v


_MAX_BLOCK_LINES = 20

# Keys that are just wrapper metadata and duplicate the event name.
_SKIP_INPUT_KEYS = frozenset({"variant"})
_SKIP_OUTPUT_KEYS = frozenset({"type"})


def _is_multiline(v: object) -> bool:
    return isinstance(v, str) and "\n" in v


def _decode_bytes(v: list[int]) -> str:
    """Decode a list of byte values to a UTF-8 string."""
    try:
        return bytes(v).decode("utf-8", errors="replace")
    except (TypeError, ValueError, OverflowError):
        return str(v)


def _format_block(text: str, indent: str = "      ") -> str:
    """Truncate a multi-line string and indent it for display."""
    lines = text.split("\n")
    if len(lines) > _MAX_BLOCK_LINES:
        lines = lines[:_MAX_BLOCK_LINES] + [f"... +{len(lines) - _MAX_BLOCK_LINES} lines"]
    return "\n".join(f"{indent}{line}" for line in lines)


def _unwrap_output(d: dict) -> object:
    """Unwrap a wrapped output dict into its human-readable payload.

    Proposal outputs use two patterns:

    1. Single-wrapper: ``{"type": "ReadFile", "FileContent": {"content": "..."}}``
       — strip *type*, descend into the single remaining dict and pull
       ``content`` / ``*_for_prompt``.
    2. Flat wrapper: ``{"type": "Bash", "output_for_prompt": "exit: 0\\n...", ...}``
       — prefer the ``*_for_prompt`` key directly.

    Model-ranking outputs are already plain strings and never reach here.
    """
    remaining = {k: v for k, v in d.items() if k not in _SKIP_OUTPUT_KEYS}

    # Prefer a *_for_prompt key at top level (e.g. Bash output_for_prompt).
    for k, v in remaining.items():
        if k.endswith("_for_prompt") and isinstance(v, str) and v.strip():
            return v

    if len(remaining) == 1:
        inner = next(iter(remaining.values()))
        if isinstance(inner, dict):
            for ik in ("content", "tool_output_for_prompt", "summary_for_prompt"):
                iv = inner.get(ik)
                if isinstance(iv, str) and iv.strip():
                    return iv
            if len(inner) == 1:
                sole = next(iter(inner.values()))
                if isinstance(sole, str):
                    return sole
        if isinstance(inner, str):
            return inner

    return remaining


def _make_block_widget(text: str) -> Static:
    """Create a Static widget that preserves newlines and brackets."""
    block = _format_block(text)
    return Static(block, markup=False, classes="trace-block")


def _add_value_widgets(
    widgets: list[Static],
    key: str,
    value: object,
    key_style: str,
    indent: str = "    ",
) -> None:
    """Append widget(s) for a single key/value pair.

    Multi-line strings get a block display; everything else stays inline.
    """
    key_str = sanitize(str(key), 30)
    if isinstance(value, list) and value and all(isinstance(x, int) for x in value):
        value = _decode_bytes(value)
    if _is_multiline(value):
        widgets.append(Static(f"{indent}[{key_style}]{key_str}[/]:"))
        widgets.append(_make_block_widget(value))
    else:
        widgets.append(Static(f"{indent}[{key_style}]{key_str}[/] = {format_value(value)}"))


def trace_event_detail_widgets(ev: object) -> list[Static]:
    """Build detail widgets for a trace event's input and output."""
    widgets: list[Static] = []

    # ---- Input ----
    if hasattr(ev, "input"):
        raw_input = ev.input
    elif isinstance(ev, dict):
        raw_input = ev.get("input", ev.get("args", ev.get("arguments", "")))
    else:
        raw_input = ""
    if raw_input:
        args = try_parse(raw_input)
        if isinstance(args, dict) and args:
            filtered = {
                k: v for k, v in args.items()
                if k not in _SKIP_INPUT_KEYS and v is not None and v is not False
            }
            if filtered:
                widgets.append(Static("  [bold]args:[/]"))
                for k, v in filtered.items():
                    _add_value_widgets(widgets, k, v, "bold cyan")
        elif isinstance(args, list) and args:
            widgets.append(Static(f"  [bold]args:[/] {format_value(args)}"))
        else:
            text = str(args)
            if text.strip():
                if _is_multiline(text):
                    widgets.append(Static("  [bold]args:[/]"))
                    widgets.append(_make_block_widget(text))
                else:
                    widgets.append(Static(f"  [bold]args:[/] [dim]{sanitize(text)}[/]"))

    # ---- Output ----
    if hasattr(ev, "output"):
        raw_output = ev.output
    elif isinstance(ev, dict):
        raw_output = ev.get("output", ev.get("result", ev.get("response", "")))
    else:
        raw_output = ""
    if raw_output:
        response = try_parse(raw_output)
        if isinstance(response, dict) and response:
            response = _unwrap_output(response)
        if isinstance(response, str):
            text = response.strip()
            if text:
                if _is_multiline(text):
                    widgets.append(Static(f"  [bold]{ARROW_RIGHT} output:[/]"))
                    widgets.append(_make_block_widget(text))
                else:
                    widgets.append(Static(f"  [bold]{ARROW_RIGHT}[/] [dim]{sanitize(text, 300)}[/]"))
        elif isinstance(response, dict) and response:
            widgets.append(Static(f"  [bold]{ARROW_RIGHT} output:[/]"))
            for k, v in response.items():
                if k in _SKIP_OUTPUT_KEYS:
                    continue
                if isinstance(v, list) and v and all(isinstance(x, int) for x in v):
                    v = _decode_bytes(v)
                _add_value_widgets(widgets, k, v, "bold green")
        elif isinstance(response, list) and response:
            widgets.append(Static(f"  [bold]{ARROW_RIGHT}[/] {format_value(response)}"))

    return widgets
