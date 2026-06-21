"""Widget IDs, CSS classes, and enum constants used across the TUI."""

from __future__ import annotations

from enum import StrEnum


class Context(StrEnum):
    OVERALL = "overall"
    RESPONSE = "response"
    CODE = "code"


MAIN_SWITCHER = "main-switcher"
INFO_BAR = "info-bar"
TASK_BAR = "task-bar"
SCOREBOARD = "scoreboard"
REPO_BAR = "repo-bar"
PROMPT_BAR = "prompt-bar"
OVERVIEW = "overview"
TABS_OVERVIEW = "tabs-overview"
TAB_CURRENT = "tab-current"
JUST_EDITOR = "justification-editor"
JUST_PREVIEW = "justification-preview"
JUST_RANKINGS = "just-rankings"

YANK_MODAL = "yank-comment-modal"
YANK_PREVIEW = "yank-preview"
YANK_COMMENT = "yank-comment"

REVIEW_COMMENT_MODAL = "review-comment-modal"
REVIEW_SNIPPET = "review-snippet"
REVIEW_COMMENT_INPUT = "review-comment-input"
COMMENTS_MODAL = "comments-modal"
COMMENTS_PREVIEW = "comments-preview"
COMMENTS_EDITOR = "comments-editor"

ANALYSIS = "analysis"
TABS_ANALYSIS = "tabs-analysis"
TAB_ANALYSIS_SIGNALS = "tab-analysis-signals"
TAB_ANALYSIS_HISTORY = "tab-analysis-history"

SPLIT_HANDLE = "split-handle"
CONTENT_AREA = "content-area"


DIFF_SEARCH_MODAL = "diff-search-modal"
DIFF_SEARCH_INPUT = "diff-search-input"
DIFF_SEARCH_LIST = "diff-search-list"

EVENT_SEARCH_MODAL = "event-search-modal"
EVENT_SEARCH_INPUT = "event-search-input"
EVENT_SEARCH_LIST = "event-search-list"




def model_letter(index: int) -> str:
    return chr(65 + index)


def model_id(index: int) -> str:
    return f"model-{chr(97 + index)}"


def model_header_id(mid: str) -> str:
    return f"header-{mid}"


def model_tabs_id(mid: str) -> str:
    return f"tabs-{mid}"


def tab_response_id(mid: str) -> str:
    return f"tab-response-{mid}"


def tab_trace_id(mid: str) -> str:
    return f"tab-trace-{mid}"


def tab_diffs_id(mid: str) -> str:
    return f"tab-diffs-{mid}"


def tab_entry_id(idx: int) -> str:
    return f"tab-entry-{idx}"


def vote_up_id(idx: int, ctx: str) -> str:
    return f"vote-up-{idx}-{ctx}"


def vote_down_id(idx: int, ctx: str) -> str:
    return f"vote-down-{idx}-{ctx}"


def vote_label_id(idx: int, ctx: str) -> str:
    return f"vote-label-{idx}-{ctx}"
