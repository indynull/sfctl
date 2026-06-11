"""Custom Textual widgets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from threading import Thread

from rich.segment import Segment
from rich.style import Style
from textual.binding import Binding
from textual.message import Message
from textual.strip import Strip
from textual.widgets import Collapsible, Static, TextArea

from sfctl.constants import ARROW_DOWN, ARROW_RIGHT, ARROW_UP
from sfctl.ids import Context
from sfctl.parsing import _sanitize, build_diff_line_map

_STYLE_HUNK = Style(color="cyan", bold=True)
_STYLE_INSERTED = Style(color="green")
_STYLE_DELETED = Style(color="red")

def _build_line_styles(text: str) -> list[Style]:
    """Compute a style for each line of a unified diff based on its prefix."""
    styles: list[Style] = []
    for line in text.split("\n"):
        if line.startswith("@@"):
            styles.append(_STYLE_HUNK)
        elif line.startswith("+"):
            styles.append(_STYLE_INSERTED)
        elif line.startswith("-"):
            styles.append(_STYLE_DELETED)
        else:
            styles.append(Style.null())
    return styles

class DiffDisplay(TextArea):
    """Read-only TextArea for diffs with syntax highlighting and text selection."""

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

    def __init__(self, text: str, model_name: str, filename: str, **kwargs):
        super().__init__(text, read_only=True, show_line_numbers=True, soft_wrap=False, **kwargs)
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
