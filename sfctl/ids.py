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

# Arena multi-field justification widget ids (suffix = field key).
# Namespace ``-u`` is used on the unified-view overview so those widgets can
# coexist with the dedicated overview (same ContentSwitcher DOM).
ARENA_CHECKLIST = "arena-checklist"
ARENA_VIOLATION_CHIPS = "arena-violation-chips"
ARENA_MARK_CQ = "arena-mark-cq"
VIOLATION_WHY_MODAL = "violation-why-modal"
VIOLATION_WHY_INPUT = "violation-why-input"
CHECKLIST_MARK_MODAL = "checklist-mark-modal"
CHECKLIST_MARK_FILTER = "checklist-mark-filter"
CHECKLIST_MARK_LIST = "checklist-mark-list"
CHECKLIST_MARK_MODEL = "checklist-mark-model"
UNIFIED_NS = "-u"


def with_ns(widget_id: str, ns: str = "") -> str:
    """Append a view namespace (e.g. unified ``-u``) to a base widget id."""
    return f"{widget_id}{ns}" if ns else widget_id


def just_preview_id(key: str, ns: str = "") -> str:
    return with_ns(f"just-preview-{key}", ns)


def just_editor_id(key: str, ns: str = "") -> str:
    return with_ns(f"just-editor-{key}", ns)


def just_section_id(key: str, ns: str = "") -> str:
    return with_ns(f"just-section-{key}", ns)


def justification_key_from_widget_id(wid: str) -> str | None:
    """Parse arena field key from a just-* widget id (with optional -u ns)."""
    for prefix in ("just-preview-", "just-section-", "just-editor-"):
        if not wid.startswith(prefix):
            continue
        rest = wid[len(prefix) :]
        if rest.endswith(UNIFIED_NS):
            rest = rest[: -len(UNIFIED_NS)]
        return rest or None
    if wid in (JUST_PREVIEW, with_ns(JUST_PREVIEW, UNIFIED_NS)):
        return ""
    return None


def violation_chip_id(model_idx: int, choice_id: str, ns: str = "") -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in choice_id)
    return with_ns(f"viol-chip-{model_idx}-{safe}", ns)


YANK_MODAL = "yank-comment-modal"
YANK_PREVIEW = "yank-preview"
YANK_COMMENT = "yank-comment"

REVIEW_COMMENT_MODAL = "review-comment-modal"
REVIEW_SNIPPET = "review-snippet"
REVIEW_COMMENT_INPUT = "review-comment-input"
COMMENTS_MODAL = "comments-modal"
COMMENTS_PREVIEW = "comments-preview"
COMMENTS_EDITOR = "comments-editor"

UNIFIED_VIEW = "unified-view"

SPLIT_HANDLE = "split-handle"
CONTENT_AREA = "content-area"
STATUS_BAR = "status-bar"


DIFF_SEARCH_MODAL = "diff-search-modal"
DIFF_SEARCH_INPUT = "diff-search-input"
DIFF_SEARCH_LIST = "diff-search-list"

EVENT_SEARCH_MODAL = "event-search-modal"
EVENT_SEARCH_INPUT = "event-search-input"
EVENT_SEARCH_LIST = "event-search-list"

SHARED_COMPARE_MODAL = "shared-compare-modal"
SHARED_FILE_LIST = "shared-file-list"
SHARED_COMPARE_SUMMARY = "shared-compare-summary"
SHARED_COMPARE_PATCHES = "shared-compare-patches"
TAB_SHARED = "tab-shared"




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
