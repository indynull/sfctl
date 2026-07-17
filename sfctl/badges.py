"""Shared status-chip styling for list rows, headers, and button badges.

Surfaces share one tone map:

* **List rows** — fixed-width kind label, foreground color only (scannable)
* **Headers / help** — filled chip markup (``badge_markup``)
* **Buttons** — ``ui-badge ui-badge-{tone}`` CSS (CQ marks)

CQ violation chips and shared-compare path triage both use this vocabulary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.text import Text

# Semantic tones → CSS modifier (``ui-badge-{tone}``) and Rich fill styles.
# Colors track Textual's default dark theme (success/warning/error/primary).
_TONE_FILL: dict[str, str] = {
    "success": "bold #0c0c0c on #4EBF71",
    "warning": "bold #0c0c0c on #ffa62b",
    "info": "bold #0c0c0c on #3d9ee5",
    "error": "bold #f0f0f0 on #ba3c5b",
    "muted": "bold #c8c8c8 on #3a3a3a",
    "primary": "bold #f0f0f0 on #0178D4",
}

# Foreground-only styles for dense file lists (no fill block per row).
_TONE_FG: dict[str, str] = {
    "success": "bold #4EBF71",
    "warning": "bold #ffa62b",
    "info": "bold #3d9ee5",
    "error": "bold #e06080",
    "muted": "dim",
    "primary": "bold #0178D4",
}

# Shared-compare path triage labels → tone.
PATH_BADGE_TONE: dict[str, str] = {
    "same": "success",
    "share": "success",
    "new": "info",
    "del": "error",
    "diff": "warning",
    "solo": "muted",
}

# Fixed column width for kind labels in the file list (share is longest).
KIND_COL_WIDTH = 5


def badge_tone(kind: str) -> str:
    """Resolve a badge kind or tone name to a canonical tone key."""
    if kind in _TONE_FILL:
        return kind
    return PATH_BADGE_TONE.get(kind, "muted")


def badge_css_classes(tone: str, *extra: str) -> str:
    """Space-joined CSS classes for a Button badge (``ui-badge ui-badge-error``)."""
    t = badge_tone(tone)
    parts = ["ui-badge", f"ui-badge-{t}", *extra]
    return " ".join(parts)


def badge_markup(label: str, kind_or_tone: str = "muted") -> str:
    """Rich markup chip: padded label with tone fill (headers / help)."""
    from rich.markup import escape

    tone = badge_tone(kind_or_tone)
    style = _TONE_FILL.get(tone, _TONE_FILL["muted"])
    text = f" {label} "
    return f"[{style}]{escape(text)}[/]"


def append_badge(text: Text, label: str, kind_or_tone: str = "muted") -> None:
    """Append a filled badge chip onto a Rich ``Text`` (headers / rare UI)."""
    tone = badge_tone(kind_or_tone)
    style = _TONE_FILL.get(tone, _TONE_FILL["muted"])
    text.append(f" {label} ", style=style)


def append_kind_column(text: Text, kind: str) -> None:
    """Fixed-width kind label for file-list rows (fg color only, no fill)."""
    tone = badge_tone(kind)
    style = _TONE_FG.get(tone, _TONE_FG["muted"])
    text.append(f"{kind:<{KIND_COL_WIDTH}}", style=style)


def path_badge_markup(badge: str) -> str:
    """Markup for a shared-compare path triage badge (``same``, ``diff``, …)."""
    return badge_markup(badge, PATH_BADGE_TONE.get(badge, "muted"))
