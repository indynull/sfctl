"""Shared UI constants (symbols, diff colors)."""

from __future__ import annotations

ARROW_UP = "\u25b2"
ARROW_DOWN = "\u25bc"
ARROW_RIGHT = "\u2192"
EM_DASH = "\u2014"

# Unified add / delete colors — DiffDisplay markers, shared-compare snippets,
# history/proposal change markup, and vote +/- feedback.
DIFF_ADD = "#4ec94e"
DIFF_DEL = "#e05050"
DIFF_HUNK = "#5f87ff"

# Rich markup style tokens (e.g. f"[{DIFF_ADD_STYLE}]+line[/]").
DIFF_ADD_STYLE = f"bold {DIFF_ADD}"
DIFF_DEL_STYLE = f"bold {DIFF_DEL}"
DIFF_HUNK_STYLE = f"bold {DIFF_HUNK}"

# DiffDisplay row background tints (RGB deltas blended into the theme bg).
DIFF_ADD_TINT = (0, 20, 0)
DIFF_DEL_TINT = (20, 0, 0)
DIFF_HUNK_TINT = (0, 8, 18)

