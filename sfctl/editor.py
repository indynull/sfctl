"""Editor controller — justification, comments, yank, and copy actions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Markdown, Static, TabbedContent, TextArea

from sfctl import ids
from sfctl.diff import diff_line_ref, language_from_filename
from sfctl.screens import CommentsModal, ReviewCommentModal, YankCommentModal, build_clipboard_text
from sfctl.widgets import DiffDisplay

if TYPE_CHECKING:
    from sfctl.app import StarfleetApp


class EditorController:
    """Composition helper that owns justification/comments editing and clipboard actions."""

    def __init__(self, app: StarfleetApp) -> None:
        self._app = app

    def save_summary(self, text: str) -> None:
        self._app.review.set_summary(text)

    def save_comments(self, text: str) -> None:
        self._app.review.set_comments(text)

    def save_summary_from_editor(self) -> None:
        """Save summary from the inline editor if it exists."""
        try:
            editor = self._app.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            self.save_summary(editor.text)
        except Exception:
            pass

    def refresh_overview_annotations(self) -> None:
        """Refresh the overview summary and rankings."""
        if not self._app._overview_populated:
            return
        try:
            rankings = self._app.query_one(f"#{ids.JUST_RANKINGS}", Static)
            rankings.update(self._app.rankings_summary())
            preview = self._app.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
            preview.update(self._app.review.summary or self._app._EMPTY_SUMMARY)
        except Exception:
            pass

    async def edit_justification(self) -> None:
        """Navigate to overview/current tab and activate the editor."""
        await self._app.go_to("overview")
        try:
            tabs = self._app.query_one(f"#{ids.TABS_OVERVIEW}", TabbedContent)
            tabs.active = ids.TAB_CURRENT
        except Exception:
            pass
        self.show_justification_editor()

    def show_justification_editor(self) -> None:
        try:
            editor = self._app.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            preview = self._app.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
        except Exception:
            return
        editor.text = self._app.review.summary
        preview.display = False
        editor.display = True
        editor.focus()

    def show_justification_preview(self) -> None:
        try:
            editor = self._app.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            preview = self._app.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
        except Exception:
            return
        if editor.display:
            self.save_summary(editor.text)
            editor.display = False
            preview.update(self._app.review.summary or self._app._EMPTY_SUMMARY)
            preview.display = True

    def add_comment(self) -> None:
        snippet = ""
        context = ""
        lang = ""
        focused = self._app.focused
        if isinstance(focused, DiffDisplay):
            sel_text = focused.selected_text.strip()
            if sel_text:
                snippet = sel_text
            context = f"{focused.model_name} {focused.filename}"
            lang = language_from_filename(focused.filename) or "diff"
        elif isinstance(focused, TextArea) and focused.selected_text.strip():
            snippet = focused.selected_text.strip()

        def _on_result(result: str | None) -> None:
            if result:
                if self._app.review.comments:
                    if not self._app.review.comments.endswith("\n"):
                        self._app.review.comments += "\n"
                    if not self._app.review.comments.endswith("\n\n"):
                        self._app.review.comments += "\n"
                self._app.review.comments += result
                self.save_comments(self._app.review.comments)
                self._app.notify("Comment added.")

        self._app.push_screen(ReviewCommentModal(snippet, context, lang), _on_result)

    def edit_comments(self) -> None:
        def _on_result(text: str) -> None:
            self.save_comments(text)

        self._app.push_screen(CommentsModal(self._app.review.comments), _on_result)

    def copy_comments(self) -> None:
        if not self._app.review.comments.strip():
            self._app.notify("No comments to copy.", severity="warning")
            return
        self._app.copy_to_clipboard(self._app.review.comments)
        self._app.notify("Comments copied to clipboard.")

    def copy_summary(self) -> None:
        text = build_clipboard_text(
            self._app.task_id,
            self._app.rankings_summary(),
            self._app.review.summary,
        )
        if not text.strip():
            self._app.notify("Nothing to copy.", severity="warning")
            return
        self._app.copy_to_clipboard(text)
        self._app.notify("Rankings & justification copied to clipboard.")

    def yank_file(self) -> None:
        focused = self._app.focused
        if not isinstance(focused, DiffDisplay):
            self._app.notify("Focus a diff first (click or tab into it).", severity="warning")
            return
        has_selection = focused.selected_text.strip() != ""
        if has_selection:
            sel = focused.selection
            start_idx = min(sel.start[0], sel.end[0])
            end_idx = max(sel.start[0], sel.end[0])
            snippet = focused.original_lines(start_idx, end_idx)
            line_ref = diff_line_ref(focused.diff_text, start_idx, end_idx)
        else:
            snippet = focused.diff_text
            line_ref = diff_line_ref(focused.diff_text, 0, len(focused.diff_text.splitlines()) - 1)
        if not snippet.strip():
            self._app.notify("No diff content to yank.", severity="warning")
            return
        filename = focused.filename

        def _on_result(result: tuple[int, str] | None) -> None:
            if result:
                _, block = result
                if self._app.review.summary:
                    if not self._app.review.summary.endswith("\n"):
                        self._app.review.summary += "\n"
                    if not self._app.review.summary.endswith("\n\n"):
                        self._app.review.summary += "\n"
                self._app.review.summary += block
                self.save_summary(self._app.review.summary)
                self.refresh_overview_annotations()
                self._app.notify(f"Yanked snippet from {filename}")

        self._app.push_screen(
            YankCommentModal(
                self._app.current_model_index,
                focused.model_name,
                filename,
                snippet,
                line_ref,
            ),
            _on_result,
        )

    def handle_escape_from_editor(self, event) -> bool:
        """Handle escape from justification editor. Returns True if handled."""
        if (
            isinstance(self._app.focused, TextArea)
            and getattr(self._app.focused, "id", None) == ids.JUST_EDITOR
        ):
            event.prevent_default()
            event.stop()
            self.show_justification_preview()
            return True
        return False
