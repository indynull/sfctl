"""Custom Textual widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Thread

from pygments.lexers import DiffLexer
from pygments.token import Token
from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widgets import Collapsible, Static, TextArea

from sfctl.constants import ARROW_RIGHT
from sfctl.parsing import build_diff_line_map

_TOKEN_STYLES: dict[object, Style] = {
    Token.Generic.Heading: Style(color="bright_yellow", bold=True),
    Token.Generic.Subheading: Style(color="cyan", bold=True),
    Token.Generic.Inserted: Style(color="green"),
    Token.Generic.Deleted: Style(color="red"),
    Token.Generic.Strong: Style(bold=True),
    Token.Generic.Emph: Style(italic=True),
    Token.Generic.Output: Style(dim=True),
}

_DIFF_LEXER = DiffLexer()


def _sanitize(text: str, max_len: int = 200) -> str:
    """Strip newlines, brackets, and truncate for safe use in Rich markup."""
    return (
        text.replace("\n", " ")
        .replace("\r", "")
        .replace("[", "(")
        .replace("]", ")")[:max_len]
        .strip()
    )


def _build_line_styles(text: str) -> list[Style]:
    """Pre-compute a Pygments-based style for each line of a diff."""
    lines = text.split("\n")
    styles: list[Style] = [Style.null()] * len(lines)

    line_idx = 0
    col = 0
    for ttype, value in _DIFF_LEXER.get_tokens(text):
        # Walk up the token hierarchy to find a matching style
        style = Style.null()
        t = ttype
        while t is not Token:
            if t in _TOKEN_STYLES:
                style = _TOKEN_STYLES[t]
                break
            t = t.parent

        # Assign style to every line this token spans
        parts = value.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                line_idx += 1
                col = 0
            if line_idx < len(styles) and style != Style.null():
                styles[line_idx] = style
            col += len(part)

    return styles


class DiffDisplay(TextArea):
    """Read-only TextArea for diffs with Pygments syntax highlighting and text selection."""

    COMPONENT_CLASSES = TextArea.COMPONENT_CLASSES | {"diff-gutter"}

    def __init__(self, text: str, model_name: str, filename: str, **kwargs):
        super().__init__(text, read_only=True, show_line_numbers=True, **kwargs)
        self.diff_text = text
        self.model_name = model_name
        self.filename = filename
        self._line_styles: list[Style] | None = None
        self._tokenize_started = False
        self._diff_line_map = build_diff_line_map(text)
        # Width of the gutter based on the max line number
        max_num = max(self._diff_line_map.values()) if self._diff_line_map else 0
        self._gutter_width = len(str(max_num)) + 1

    def _start_tokenize(self) -> None:
        if self._tokenize_started:
            return
        self._tokenize_started = True

        def _build():
            self._line_styles = _build_line_styles(self.diff_text)
            self.app.call_from_thread(self.refresh)

        Thread(target=_build, daemon=True).start()

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        line_index = y + self.scroll_offset.y

        # Replace TextArea's default line numbers with real source numbers
        if self.show_line_numbers and self._diff_line_map is not None:
            gutter_style = self.get_component_rich_style("text-area--gutter")
            real_num = self._diff_line_map.get(line_index)
            gutter_text = (
                f"{real_num:>{self._gutter_width}} "
                if real_num is not None
                else " " * (self._gutter_width + 1)
            )
            gutter = Strip([Segment(gutter_text, gutter_style)])
            # Remove the original gutter (first segment group up to gutter_width)
            orig_gutter_len = self.gutter_width
            strip = strip.crop(orig_gutter_len, strip.cell_length)
            strip = Strip.join([gutter, strip])

        # Start tokenization on first render
        if not self._tokenize_started:
            self._start_tokenize()
            return strip

        # Apply syntax highlighting
        styles = self._line_styles
        if styles is not None and 0 <= line_index < len(styles):
            style = styles[line_index]
            if style != Style.null():
                new_segments = []
                for text, seg_style, control in strip._segments:
                    merged = seg_style + style if seg_style else style
                    new_segments.append(Segment(text, merged, control))
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


def _format_value(v: object, max_len: int = 120) -> str:
    """Format a single value for display, truncating long strings."""
    if v is None:
        return "[dim italic]null[/]"
    if isinstance(v, bool):
        return f"[dim]{v}[/]"
    if isinstance(v, (int, float)):
        return f"[dim]{v}[/]"
    if isinstance(v, str):
        s = _sanitize(v, max_len)
        return f"[dim]{s}[/]" if s else '[dim italic]""[/]'
    if isinstance(v, list):
        if not v:
            return "[dim italic](empty list)[/]"
        items = ", ".join(_sanitize(str(x), 40) for x in v[:5])
        suffix = f" ... +{len(v) - 5}" if len(v) > 5 else ""
        return f"[dim]({items}{suffix})[/]"
    if isinstance(v, dict):
        if not v:
            return "[dim italic]{...}[/]"
        items = ", ".join(
            f"{_sanitize(str(k), 20)}={_sanitize(str(val), 30)}" for k, val in list(v.items())[:4]
        )
        suffix = f" ... +{len(v) - 4}" if len(v) > 4 else ""
        return f"[dim]{items}{suffix}[/]"
    return f"[dim]{_sanitize(str(v), max_len)}[/]"


def _try_parse(v: object) -> object:
    """If v is a JSON string, parse it into a dict/list."""
    if isinstance(v, str):
        try:
            import json

            parsed = json.loads(v)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return v


def trace_event_detail_widgets(ev: dict) -> list[Static]:
    """Build detail widgets for a trace event's args and response/output."""
    widgets: list[Static] = []

    # Arguments
    args = None
    for key in ("args", "arguments", "input"):
        args = ev.get(key)
        if args is not None:
            break
    if args is not None:
        args = _try_parse(args)
        if isinstance(args, dict) and args:
            widgets.append(Static("  [bold]args:[/]"))
            for k, v in args.items():
                key_str = _sanitize(str(k), 30)
                widgets.append(Static(f"    [bold cyan]{key_str}[/] = {_format_value(v)}"))
        elif isinstance(args, list) and args:
            widgets.append(Static(f"  [bold]args:[/] {_format_value(args)}"))
        else:
            line = _sanitize(str(args))
            if line:
                widgets.append(Static(f"  [bold]args:[/] [dim]{line}[/]"))

    # Response / output
    response = None
    for key in ("output", "result", "response"):
        response = ev.get(key)
        if response is not None:
            break
    if response is not None:
        response = _try_parse(response)
        if isinstance(response, dict) and response:
            widgets.append(Static(f"  [bold]{ARROW_RIGHT} output:[/]"))
            for k, v in response.items():
                key_str = _sanitize(str(k), 30)
                widgets.append(Static(f"    [bold green]{key_str}[/] = {_format_value(v)}"))
        elif isinstance(response, list) and response:
            widgets.append(Static(f"  [bold]{ARROW_RIGHT}[/] {_format_value(response)}"))
        else:
            line = _sanitize(str(response), 300)
            if line:
                widgets.append(Static(f"  [bold]{ARROW_RIGHT}[/] [dim]{line}[/]"))

    return widgets
