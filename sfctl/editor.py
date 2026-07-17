"""Editor controller — justification, comments, snippets, and copy actions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Button, Markdown, Static, TabbedContent, TextArea

from sfctl import ids
from sfctl.arena import (
    EDITABLE_JUSTIFICATION_KEYS,
    append_violation_note,
    empty_justification_hint,
)
from sfctl.diff import diff_line_ref, language_from_filename
from sfctl.screens import (
    ChecklistMarkModal,
    CommentsModal,
    ReviewCommentModal,
    ViolationWhyModal,
    YankCommentModal,
    build_clipboard_text,
)
from sfctl.widgets import DiffDisplay

if TYPE_CHECKING:
    from sfctl.app import StarfleetApp

_CODE_JUST_KEY = "code_quality_justification"
_RESPONSE_JUST_KEY = "response_justification"


class EditorController:
    """Composition helper that owns justification/comments editing and clipboard actions."""

    def __init__(self, app: StarfleetApp) -> None:
        self._app = app
        self._editing_key: str | None = None

    def is_arena(self) -> bool:
        from sfctl.handlers.arena import ArenaHandler

        return isinstance(self._app.handler, ArenaHandler)

    def _active_ns(self) -> str:
        """Widget-id namespace for the overview currently on screen."""
        if self._app._current_section == ids.UNIFIED_VIEW:
            return ids.UNIFIED_NS
        return ""

    def _overview_namespaces(self) -> tuple[str, ...]:
        """Namespaces that may have mounted overview widgets."""
        return ("", ids.UNIFIED_NS)

    def save_summary(self, text: str) -> None:
        self._app.review.set_summary(text)

    def save_justification(self, key: str, text: str) -> None:
        self._app.review.set_justification(key, text)

    def save_comments(self, text: str) -> None:
        self._app.review.set_comments(text)

    def save_summary_from_editor(self) -> None:
        """Persist any open justification editor(s) before quit."""
        if self.is_arena():
            saved: set[str] = set()
            for key, _ in EDITABLE_JUSTIFICATION_KEYS:
                for ns in self._overview_namespaces():
                    try:
                        editor = self._app.query_one(
                            f"#{ids.just_editor_id(key, ns)}", TextArea
                        )
                    except Exception:
                        continue
                    if editor.display and key not in saved:
                        self.save_justification(key, editor.text)
                        saved.add(key)
            return
        try:
            editor = self._app.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            self.save_summary(editor.text)
        except Exception:
            pass

    def discard_open_editors(self) -> None:
        """Hide justification editors without saving (before reset)."""
        self._editing_key = None
        if self.is_arena():
            for key, _ in EDITABLE_JUSTIFICATION_KEYS:
                for ns in self._overview_namespaces():
                    try:
                        editor = self._app.query_one(
                            f"#{ids.just_editor_id(key, ns)}", TextArea
                        )
                        preview = self._app.query_one(
                            f"#{ids.just_preview_id(key, ns)}", Markdown
                        )
                    except Exception:
                        continue
                    editor.display = False
                    preview.display = True
            return
        try:
            editor = self._app.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            preview = self._app.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
            editor.display = False
            preview.display = True
        except Exception:
            pass

    def sync_widgets_from_state(self) -> None:
        """Push ReviewState text into mounted preview/editor widgets."""
        if self.is_arena():
            for key, _ in EDITABLE_JUSTIFICATION_KEYS:
                text = self._app.review.justification_text(key)
                for ns in self._overview_namespaces():
                    try:
                        editor = self._app.query_one(
                            f"#{ids.just_editor_id(key, ns)}", TextArea
                        )
                        editor.text = text
                    except Exception:
                        pass
                    try:
                        preview = self._app.query_one(
                            f"#{ids.just_preview_id(key, ns)}", Markdown
                        )
                        preview.update(self._preview_text(key))
                    except Exception:
                        pass
            return
        try:
            editor = self._app.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            editor.text = self._app.review.summary
        except Exception:
            pass
        try:
            preview = self._app.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
            preview.update(self._app.review.summary or self._app._EMPTY_SUMMARY)
        except Exception:
            pass

    def _preview_text(self, key: str) -> str:
        text = self._app.review.justification_text(key).strip()
        return text if text else empty_justification_hint(key)

    def _clipboard_body(self) -> str:
        if self.is_arena():
            return self._app.review.combined_justifications()
        return self._app.review.summary

    def refresh_overview_annotations(self) -> None:
        """Refresh overview rankings and justification previews."""
        if not self._app._overview_populated:
            return
        for ns in self._overview_namespaces():
            try:
                rankings = self._app.query_one(
                    f"#{ids.with_ns(ids.JUST_RANKINGS, ns)}", Static
                )
                rankings.update(self._app.rankings_summary())
            except Exception:
                pass
        if self.is_arena():
            for key, _ in EDITABLE_JUSTIFICATION_KEYS:
                if self._editing_key == key:
                    continue
                for ns in self._overview_namespaces():
                    try:
                        preview = self._app.query_one(
                            f"#{ids.just_preview_id(key, ns)}", Markdown
                        )
                        preview.update(self._preview_text(key))
                    except Exception:
                        pass
            return
        try:
            preview = self._app.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
            preview.update(self._app.review.summary or self._app._EMPTY_SUMMARY)
        except Exception:
            pass

    async def edit_justification(self) -> None:
        """Navigate to overview and edit justification via Ctrl+E.

        Classic ranking: open the single summary editor.
        Arena: move through Response Quality, Code Quality, and Overall
        (Ctrl+E advances; Esc saves and returns to preview). Works in unified
        overview (full multi-field UI) or the dedicated overview section.
        """
        if self._app._current_section != ids.UNIFIED_VIEW:
            await self._app.go_to("overview")
            try:
                tabs = self._app.query_one(f"#{ids.TABS_OVERVIEW}", TabbedContent)
                tabs.active = ids.TAB_CURRENT
            except Exception:
                pass
            await self._app._populate_overview()
        else:
            # Focus the unified overview strip (full CQ + multi-field UI).
            from textual.containers import ScrollableContainer

            self._app._split_focus = -1
            self._app._update_split_focus()
            try:
                self._app.query_one(
                    "#unified-overview", ScrollableContainer
                ).focus()
            except Exception:
                pass
        if self.is_arena():
            keys = [k for k, _ in EDITABLE_JUSTIFICATION_KEYS]
            if self._editing_key and self._editing_key in keys:
                idx = keys.index(self._editing_key)
                next_key = keys[(idx + 1) % len(keys)]
                self.show_justification_editor(next_key)
            else:
                self.show_justification_editor(keys[0])
            return
        self.show_justification_editor()

    def show_justification_editor(self, key: str | None = None) -> None:
        """Open a justification editor. *key* selects the arena field."""
        if self.is_arena():
            if not key:
                key = EDITABLE_JUSTIFICATION_KEYS[0][0]
            self._show_arena_editor(key)
            return
        try:
            editor = self._app.query_one(f"#{ids.JUST_EDITOR}", TextArea)
            preview = self._app.query_one(f"#{ids.JUST_PREVIEW}", Markdown)
        except Exception:
            return
        editor.text = self._app.review.summary
        preview.display = False
        editor.display = True
        editor.focus()

    def _show_arena_editor(self, key: str) -> None:
        if self._editing_key and self._editing_key != key:
            self.show_justification_preview(self._editing_key)
        editor = None
        preview = None
        for candidate in (self._active_ns(), "", ids.UNIFIED_NS):
            try:
                editor = self._app.query_one(
                    f"#{ids.just_editor_id(key, candidate)}", TextArea
                )
                preview = self._app.query_one(
                    f"#{ids.just_preview_id(key, candidate)}", Markdown
                )
                break
            except Exception:
                continue
        if editor is None or preview is None:
            return
        editor.text = self._app.review.justification_text(key)
        preview.display = False
        editor.display = True
        self._editing_key = key
        editor.focus()
        labels = dict(EDITABLE_JUSTIFICATION_KEYS)
        label = labels.get(key, key)
        keys = [k for k, _ in EDITABLE_JUSTIFICATION_KEYS]
        n = keys.index(key) + 1 if key in keys else 1
        total = len(keys)
        self._app._status(
            f"Editing {label} ({n}/{total}) — Ctrl+E next · Esc saves"
        )

    def show_justification_preview(self, key: str | None = None) -> None:
        """Save open editor(s) and restore markdown previews."""
        if self.is_arena():
            targets = [key] if key else [
                k for k, _ in EDITABLE_JUSTIFICATION_KEYS
            ]
            for k in targets:
                for ns in self._overview_namespaces():
                    try:
                        editor = self._app.query_one(
                            f"#{ids.just_editor_id(k, ns)}", TextArea
                        )
                        preview = self._app.query_one(
                            f"#{ids.just_preview_id(k, ns)}", Markdown
                        )
                    except Exception:
                        continue
                    if editor.display:
                        self.save_justification(k, editor.text)
                        editor.display = False
                        preview.update(self._preview_text(k))
                        preview.display = True
            if key is None or self._editing_key == key:
                self._editing_key = None
            return
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

    def open_checklist_mark_modal(self) -> None:
        """Open the code-quality catalog for marking rules.

        From a model response (or unified model column), the catalog is locked
        to that model so ``v`` marks violations for what you are reading.
        From overview, ``1``/``2``/``3`` switch the target model.
        """
        from sfctl.handlers.arena import ArenaHandler

        if not isinstance(self._app.handler, ArenaHandler):
            return
        catalog = self._app.handler.meta.checklist_catalog
        if not catalog:
            self._app._status("No code quality rule catalog on this task.")
            return
        n = min(3, max(1, len(self._app.models)))
        on_model = self._app._is_on_model_view()
        if on_model:
            initial = self._app.current_model_index
            if initial < 0 or initial >= n:
                initial = 0
            lock_model = True
        else:
            # Overview / other: start on last focused model, allow switching.
            initial = self._app.current_model_index
            if initial < 0 or initial >= n:
                initial = 0
            lock_model = False

        def _on_toggle(model_idx: int, choice_id: str, now_selected: bool) -> None:
            self._app.review.set_checklist_selection(
                model_idx, choice_id, now_selected
            )
            title = self._app.handler.meta.rule_labels.get(choice_id, choice_id)
            letter = ids.model_letter(model_idx)
            if now_selected:
                self._app._status(f"Marked {letter} · {title}")
                self._prompt_why_after_mark(model_idx, title)
            else:
                self._app._status(f"Cleared {letter} · {title}")
            self.refresh_checklist_ui()

        letter = ids.model_letter(initial)
        if lock_model:
            self._app._status(f"Code quality · Model {letter}")
        self._app.push_screen(
            ChecklistMarkModal(
                catalog,
                self._app.review.checklist_selections,
                n_models=n,
                initial_model=initial,
                lock_model=lock_model,
                on_toggle=_on_toggle,
            ),
        )

    def _prompt_why_after_mark(self, model_idx: int, rule_label: str) -> None:
        letter = ids.model_letter(model_idx)

        def _on_result(why: str | None) -> None:
            if why is None:
                return
            self._apply_violation_note(model_idx, rule_label, why)

        self._app.push_screen(
            ViolationWhyModal(letter, rule_label),
            _on_result,
        )

    def prompt_violation_note(self, button: Button) -> None:
        """Open optional-why modal for a checklist violation chip."""
        model_idx = getattr(button, "_viol_model", None)
        rule_label = getattr(button, "_viol_label", None)
        if model_idx is None or not rule_label:
            return
        letter = ids.model_letter(int(model_idx))

        def _on_result(why: str | None) -> None:
            if why is None:
                return
            self._apply_violation_note(int(model_idx), str(rule_label), why)

        self._app.push_screen(
            ViolationWhyModal(letter, str(rule_label)),
            _on_result,
        )

    def refresh_checklist_ui(self) -> None:
        """Rebuild checklist table and violation chips from local selections."""
        from sfctl.arena import (
            checklist_from_selections,
            format_checklist_table,
            selections_with_titles,
        )
        from sfctl.handlers.arena import ArenaHandler

        if not isinstance(self._app.handler, ArenaHandler):
            return
        handler = self._app.handler
        catalog = handler.meta.checklist_catalog
        selections = self._app.review.checklist_selections
        n = min(3, max(1, len(self._app.models)))
        cl = checklist_from_selections(
            selections,
            catalog,
            n_models=n,
            rule_labels=handler.meta.rule_labels,
        )
        empty_msg = (
            "[dim]No violations marked — press v on a model response "
            "to mark code quality rules.[/dim]"
        )
        table_markup = format_checklist_table(cl) if cl else empty_msg
        chips = list(selections_with_titles(selections, handler.meta.rule_labels))
        for ns in self._overview_namespaces():
            try:
                table = self._app.query_one(
                    f"#{ids.with_ns(ids.ARENA_CHECKLIST, ns)}", Static
                )
                table.update(table_markup)
            except Exception:
                pass
            try:
                row = self._app.query_one(
                    f"#{ids.with_ns(ids.ARENA_VIOLATION_CHIPS, ns)}"
                )
            except Exception:
                continue
            # remove() is deferred; remount only after children are gone.
            self._app.run_worker(
                self._rebuild_violation_chips(row, chips),
                exclusive=False,
                name=f"rebuild-cq-chips{ns or '-main'}",
            )

    async def _rebuild_violation_chips(
        self,
        row,
        chips: list[tuple[int, str, str]],
    ) -> None:
        from textual.widgets import Button

        from sfctl.badges import badge_css_classes

        await row.remove_children()
        await row.mount(
            Button(
                "+ Mark Code Quality",
                classes=badge_css_classes("primary", "violation-chip", "violation-mark"),
                compact=True,
                flat=True,
            )
        )
        for model_idx, _choice_id, title in chips:
            letter = ids.model_letter(model_idx)
            short = title if len(title) <= 42 else title[:39] + "…"
            btn = Button(
                f"{letter}  {short}",
                classes=badge_css_classes("error", "violation-chip"),
                compact=True,
                flat=True,
            )
            btn._viol_model = model_idx  # type: ignore[attr-defined]
            btn._viol_label = title  # type: ignore[attr-defined]
            btn.tooltip = f"Note why · {letter} · {title}"
            await row.mount(btn)

    def _apply_violation_note(
        self, model_idx: int, rule_label: str, why: str
    ) -> None:
        letter = ids.model_letter(model_idx)
        current = self._app.review.justification_text(_RESPONSE_JUST_KEY)
        if self._editing_key == _RESPONSE_JUST_KEY:
            try:
                editor = self._app.query_one(
                    f"#{ids.just_editor_id(_RESPONSE_JUST_KEY)}", TextArea
                )
                current = editor.text
            except Exception:
                pass
        updated = append_violation_note(
            current,
            model_letter=letter,
            rule_label=rule_label,
            why=why,
        )
        self.save_justification(_RESPONSE_JUST_KEY, updated)
        if self._editing_key == _RESPONSE_JUST_KEY:
            try:
                editor = self._app.query_one(
                    f"#{ids.just_editor_id(_RESPONSE_JUST_KEY)}", TextArea
                )
                editor.text = updated
            except Exception:
                pass
        else:
            try:
                preview = self._app.query_one(
                    f"#{ids.just_preview_id(_RESPONSE_JUST_KEY)}", Markdown
                )
                preview.update(self._preview_text(_RESPONSE_JUST_KEY))
            except Exception:
                pass
        self._app._status(f"Noted {letter} · {rule_label} in Response Quality")
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
                self._app._status("Comment added.")

        self._app.push_screen(ReviewCommentModal(snippet, context, lang), _on_result)

    def edit_comments(self) -> None:
        def _on_result(text: str) -> None:
            self.save_comments(text)

        self._app.push_screen(CommentsModal(self._app.review.comments), _on_result)

    def copy_comments(self) -> None:
        if not self._app.review.comments.strip():
            self._app._status("No comments to copy.")
            return
        self._app.copy_to_clipboard(self._app.review.comments)
        self._app._status("Comments copied to the clipboard.")

    def copy_summary(self) -> None:
        text = build_clipboard_text(
            self._app.task_id,
            self._app.rankings_summary(),
            self._clipboard_body(),
        )
        if not text.strip():
            self._app._status("Nothing to copy.")
            return
        self._app.copy_to_clipboard(text)
        self._app._status("Rankings and justification copied to the clipboard.")

    def _append_markdown_block(self, target: str, block: str) -> str:
        text = target or ""
        if text:
            if not text.endswith("\n"):
                text += "\n"
            if not text.endswith("\n\n"):
                text += "\n"
        return text + block

    def yank_file(self) -> None:
        focused = self._app.focused
        if not isinstance(focused, DiffDisplay):
            self._app._status("Focus a diff first (click or Tab into it).")
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
            line_ref = diff_line_ref(
                focused.diff_text, 0, len(focused.diff_text.splitlines()) - 1
            )
        if not snippet.strip():
            self._app._status("No diff content to copy.")
            return
        filename = focused.filename
        model_index = self._app.current_model_index
        name = (focused.model_name or "").strip()
        if name:
            ch = name[0].upper()
            if "A" <= ch <= "C":
                model_index = ord(ch) - ord("A")

        def _on_result(result: tuple[int, str] | None) -> None:
            if not result:
                return
            _, block = result
            if self.is_arena():
                current = self._app.review.justification_text(_CODE_JUST_KEY)
                if self._editing_key == _CODE_JUST_KEY:
                    try:
                        editor = self._app.query_one(
                            f"#{ids.just_editor_id(_CODE_JUST_KEY)}", TextArea
                        )
                        current = editor.text
                    except Exception:
                        pass
                updated = self._append_markdown_block(current, block)
                self.save_justification(_CODE_JUST_KEY, updated)
                if self._editing_key == _CODE_JUST_KEY:
                    try:
                        editor = self._app.query_one(
                            f"#{ids.just_editor_id(_CODE_JUST_KEY)}", TextArea
                        )
                        editor.text = updated
                    except Exception:
                        pass
                else:
                    self.refresh_overview_annotations()
                self._app._status(
                    f"Copied snippet from {filename} into Code Quality"
                )
                return
            updated = self._append_markdown_block(self._app.review.summary, block)
            self._app.review.summary = updated
            self.save_summary(self._app.review.summary)
            self.refresh_overview_annotations()
            self._app._status(f"Copied snippet from {filename}")

        self._app.push_screen(
            YankCommentModal(
                model_index,
                focused.model_name,
                filename,
                snippet,
                line_ref,
            ),
            _on_result,
        )

    def handle_escape_from_editor(self, event) -> bool:
        """Handle escape from justification editor. Returns True if handled."""
        focused = self._app.focused
        if not isinstance(focused, TextArea):
            return False
        wid = getattr(focused, "id", None)
        if wid == ids.JUST_EDITOR:
            event.prevent_default()
            event.stop()
            self.show_justification_preview()
            return True
        if isinstance(wid, str):
            key = ids.justification_key_from_widget_id(wid)
            if key:
                event.prevent_default()
                event.stop()
                self.show_justification_preview(key)
                self._app._status("Saved.")
                return True
        return False
