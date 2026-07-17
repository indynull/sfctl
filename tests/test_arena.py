"""Tests for arena ranking (clarity checklist) task type."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "arena_sample.json"
REAL_PATH = Path(__file__).resolve().parents[1] / "new_format_arena.json"


@pytest.fixture
def arena_data() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


class TestDetectArena:
    def test_fixture_is_arena(self, arena_data):
        from sfctl.task_types import TaskType, detect_task_type

        assert detect_task_type(arena_data) == TaskType.ARENA_RANKING

    def test_classic_ranking_not_arena(self, fixture_data):
        from sfctl.task_types import TaskType, detect_task_type

        assert detect_task_type(fixture_data) == TaskType.CODE_REVIEW

    def test_real_file_if_present(self):
        if not REAL_PATH.exists():
            pytest.skip("new_format_arena.json not in tree")
        from sfctl.task_types import TaskType, detect_task_type

        data = json.loads(REAL_PATH.read_text())
        assert detect_task_type(data) == TaskType.ARENA_RANKING


class TestArenaParse:
    def test_meta_and_rules(self, arena_data):
        from sfctl.arena import parse_arena_meta

        meta = parse_arena_meta(arena_data)
        assert meta.label_map["A"] == "plasticcup"
        assert meta.label_map["B"] == "tahoma"
        assert meta.label_map["C"] == "opus48-xhigh"
        assert meta.anchor == "opus48-xhigh"
        # Labels come from the task question options, not a local rule catalog
        assert meta.rule_labels.get("o4_violated") == "No bloated body"

    def test_checklist_from_history(self, arena_data):
        from sfctl.arena import (
            checklist_from_entry,
            checklist_violation_summary,
            format_checklist_markup,
            parse_arena_meta,
        )

        meta = parse_arena_meta(arena_data)
        entry = arena_data["history"][0]
        cl = checklist_from_entry(entry, meta.rule_labels)
        assert cl is not None
        assert cl.col_headers == ["Model A", "Model B", "Model C"]
        # Model C Organisation has o4_violated -> title from option text
        assert "No bloated body" in cl.cells[0][2]
        summary = checklist_violation_summary(cl)
        assert "C:1" in summary
        markup = format_checklist_markup(cl)
        assert "No bloated body" in markup
        assert "Organisation" in markup or "Organization" in markup

    def test_justifications(self, arena_data):
        from sfctl.arena import combined_justification, justification_sections

        entry = arena_data["history"][0]
        sections = justification_sections(entry)
        labels = {lab for lab, _ in sections}
        assert "Prompt Understanding" in labels
        assert "Response Justification" in labels
        assert "Code Justification" in labels
        combined = combined_justification(entry)
        assert "## Prompt Understanding" in combined
        assert "## Code Justification" in combined

    def test_append_violation_note(self):
        from sfctl.arena import append_violation_note

        out = append_violation_note(
            "",
            model_letter="C",
            rule_label="No bloated body",
            why="wall of text",
        )
        assert "### Model C" in out
        assert "#### No bloated body" in out
        assert "wall of text" in out

        existing = "### Model A\n\n#### Prior rule\n\n"
        out2 = append_violation_note(
            existing,
            model_letter="A",
            rule_label="No wall of text",
            why="",
        )
        assert "#### Prior rule" in out2
        assert "#### No wall of text" in out2
        assert out2.index("Prior rule") < out2.index("No wall of text")

    def test_list_checklist_violations(self, arena_data):
        from sfctl.arena import list_checklist_violations, parse_arena_meta

        meta = parse_arena_meta(arena_data)
        entry = arena_data["history"][0]
        viols = list_checklist_violations(entry, meta.rule_labels)
        assert any(idx == 2 and "bloated" in title.lower() for idx, _cid, title in viols)

    def test_has_changes_on_checklist(self, arena_data):
        from sfctl.arena import has_arena_changes

        prev = arena_data["history"][0]
        curr = json.loads(json.dumps(prev))
        assert not has_arena_changes(prev, curr)
        cells = curr["response_clarity_checklist"]["cells"]
        cells[1][0] = {"_sf_rich": True, "value": ["p14_violated"]}
        assert has_arena_changes(prev, curr)

    def test_has_changes_on_justification(self, arena_data):
        from sfctl.arena import has_arena_changes

        prev = arena_data["history"][0]
        curr = json.loads(json.dumps(prev))
        curr["code_quality_justification"] = {"value": "totally different"}
        assert has_arena_changes(prev, curr)

    def test_checklist_change_lines_list_rule_swaps(self, arena_data):
        """History Changes must name rules, not only A:1→A:1 counts."""
        from sfctl.arena import (
            arena_checklist_change_lines,
            has_arena_changes,
            parse_arena_meta,
        )

        meta = parse_arena_meta(arena_data)
        prev = arena_data["history"][0]
        curr = json.loads(json.dumps(prev))
        # Swap C's Organisation violation for a Prose one — count may stay C:1.
        cells = curr["response_clarity_checklist"]["cells"]
        # Clear C Organisation (row 0 col 2)
        cells[0][2] = {"_sf_rich": True, "value": []}
        # Mark C Prose (row 1 col 2)
        cells[1][2] = {"_sf_rich": True, "value": ["p14_violated"]}
        assert has_arena_changes(prev, curr)
        lines = arena_checklist_change_lines(prev, curr, meta.rule_labels)
        joined = "\n".join(lines)
        assert "Checklist:" in joined
        assert "+" in joined and "-" in joined
        # Old C rule removed, new rule added (titles from catalog when known)
        assert "C:" in joined
        # Must not be only a no-op count line without add/remove detail
        assert any(ln.strip().startswith("+") or "]+" in ln for ln in lines)

    def test_checklist_change_lines_empty_when_same(self, arena_data):
        from sfctl.arena import arena_checklist_change_lines, parse_arena_meta

        meta = parse_arena_meta(arena_data)
        entry = arena_data["history"][0]
        assert arena_checklist_change_lines(entry, entry, meta.rule_labels) == []

class TestArenaHandler:
    def test_handler_parse_and_headers(self, arena_data, make_app):
        from sfctl.handlers.arena import ArenaHandler
        from sfctl.task_types import TaskType

        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        assert app.task_type == TaskType.ARENA_RANKING
        assert isinstance(app.handler, ArenaHandler)
        assert len(app.models) == 3
        assert app.handler.model_header_label(0) == "[bold]A[/bold]"
        assert app.handler.model_header_label(1) == "[bold]B[/bold]"

    def test_scoreboard_includes_cq(self, arena_data, make_app):
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        parts = app.handler.scoreboard_parts()
        joined = " ".join(parts)
        assert "Checklist" in joined or "C:1" in joined

    def test_edit_justification_enabled(self, arena_data, make_app):
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        assert "edit_justification" not in app.handler.hidden_actions()

    @pytest.mark.asyncio
    async def test_overview_mounts_multi_editors(self, arena_data, make_app):
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Button, TextArea

            for key in (
                "response_justification",
                "code_quality_justification",
                "overall_justification",
            ):
                editor = app.query_one(f"#just-editor-{key}", TextArea)
                assert editor.display is False
                preview = app.query_one(f"#just-preview-{key}")
                assert preview is not None

            mark = [b for b in app.query(Button) if b.has_class("violation-mark")]
            assert len(mark) == 1
            chips = [
                b
                for b in app.query(Button)
                if b.has_class("violation-chip") and not b.has_class("violation-mark")
            ]
            assert any("bloated" in str(c.label).lower() for c in chips)

    def test_checklist_catalog_and_local_toggle(self, arena_data, make_app):
        from sfctl.arena import checklist_from_selections, parse_checklist_catalog

        questions = arena_data["content"]["questions"]
        catalog = parse_checklist_catalog(questions)
        assert len(catalog) >= 10
        assert any(r.choice_id == "o4_violated" for r in catalog)

        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        assert (2, "o4_violated") in app.review.checklist_selections
        now_on = app.review.toggle_checklist_selection(0, "o1_violated")
        assert now_on is True
        assert (0, "o1_violated") in app.review.checklist_selections
        now_off = app.review.toggle_checklist_selection(0, "o1_violated")
        assert now_off is False

        cl = checklist_from_selections(
            app.review.checklist_selections,
            catalog,
            n_models=3,
            rule_labels=app.handler.meta.rule_labels,
        )
        assert cl is not None
        assert cl.row_headers

    @pytest.mark.asyncio
    async def test_edit_and_save_arena_section(self, arena_data, make_app):
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import TextArea

            await app.action_edit_justification()
            await pilot.pause()
            editor = app.query_one("#just-editor-response_justification", TextArea)
            assert editor.display is True
            editor.text = "### Model A\n\ncustom note\n"
            app._editor.show_justification_preview("response_justification")
            assert (
                app.review.justification_text("response_justification")
                == "### Model A\n\ncustom note\n"
            )

    @pytest.mark.asyncio
    async def test_ctrl_e_cycles_arena_sections(self, arena_data, make_app):
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import TextArea

            await app.action_edit_justification()
            await pilot.pause()
            assert app.query_one(
                "#just-editor-response_justification", TextArea
            ).display is True
            await app.action_edit_justification()
            await pilot.pause()
            assert app.query_one(
                "#just-editor-response_justification", TextArea
            ).display is False
            assert app.query_one(
                "#just-editor-code_quality_justification", TextArea
            ).display is True
            await app.action_edit_justification()
            await pilot.pause()
            assert app.query_one(
                "#just-editor-overall_justification", TextArea
            ).display is True

    @pytest.mark.asyncio
    async def test_click_opens_specific_justification(self, arena_data, make_app):
        """Clicking a justification section opens that field's editor."""
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import TextArea

            await app.go_to("overview")
            await pilot.pause()
            preview = app.query_one("#just-preview-code_quality_justification")
            app._try_edit_justification_from_widget(preview)
            await pilot.pause()
            # Worker may still be running; wait a beat.
            for _ in range(10):
                await pilot.pause()
                ed = app.query_one(
                    "#just-editor-code_quality_justification", TextArea
                )
                if ed.display:
                    break
            assert app.query_one(
                "#just-editor-code_quality_justification", TextArea
            ).display is True
            assert app.query_one(
                "#just-editor-response_justification", TextArea
            ).display is False

    @pytest.mark.asyncio
    async def test_unified_overview_has_cq_and_justification_editors(
        self, arena_data, make_app
    ):
        """Side-by-side overview mounts full CQ UI and multi-field editors."""
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Static, TextArea

            from sfctl import ids as app_ids

            await app.action_split_view()
            await pilot.pause()
            assert app._current_section == app_ids.UNIFIED_VIEW
            # Namespaced widgets on unified overview.
            app.query_one(f"#{app_ids.ARENA_CHECKLIST}-u", Static)
            app.query_one(f"#{app_ids.ARENA_VIOLATION_CHIPS}-u")
            app.query_one("#just-editor-response_justification-u", TextArea)
            app.query_one("#just-preview-code_quality_justification-u")
            await app.action_edit_justification()
            await pilot.pause()
            for _ in range(10):
                await pilot.pause()
                ed = app.query_one(
                    "#just-editor-response_justification-u", TextArea
                )
                if ed.display:
                    break
            assert app.query_one(
                "#just-editor-response_justification-u", TextArea
            ).display is True
            # Stays in unified view.
            assert app._current_section == app_ids.UNIFIED_VIEW


class TestChecklistMarkModal:
    @pytest.mark.asyncio
    async def test_digit_keys_switch_model_while_filter_focused(self):
        """1/2/3 select models even when the filter Input has focus."""
        from textual.app import App, ComposeResult
        from textual.widgets import Input, Static

        from sfctl import ids
        from sfctl.arena import ChecklistRule
        from sfctl.screens import ChecklistMarkModal

        catalog = [
            ChecklistRule(choice_id="r1", title="No wall of text", category="prose"),
            ChecklistRule(
                choice_id="r2", title="Clear structure", category="organisation"
            ),
        ]

        class Host(App):
            def compose(self) -> ComposeResult:
                yield Static("host")

            def on_mount(self) -> None:
                self.push_screen(
                    ChecklistMarkModal(
                        catalog, [], n_models=3, initial_model=0, lock_model=False
                    ),
                    lambda _r: None,
                )

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ChecklistMarkModal)
            filt = modal.query_one(f"#{ids.CHECKLIST_MARK_FILTER}", Input)
            # Focus filter so we exercise digit intercept on the Input.
            filt.focus()
            await pilot.pause()
            assert modal._model_idx == 0

            await pilot.press("2")
            await pilot.pause()
            assert modal._model_idx == 1
            assert filt.value == ""

            await pilot.press("3")
            await pilot.pause()
            assert modal._model_idx == 2
            assert filt.value == ""

            await pilot.press("1")
            await pilot.pause()
            assert modal._model_idx == 0

            await pilot.press("w")
            await pilot.pause()
            assert filt.value == "w"

    @pytest.mark.asyncio
    async def test_locked_model_ignores_digit_switch(self):
        """From a model response, v locks to that model — digits do not retarget."""
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from sfctl.arena import ChecklistRule
        from sfctl.screens import ChecklistMarkModal

        catalog = [
            ChecklistRule(choice_id="r1", title="No wall of text", category="prose"),
        ]
        toggles: list[tuple[int, str, bool]] = []

        class Host(App):
            def compose(self) -> ComposeResult:
                yield Static("host")

            def on_mount(self) -> None:
                self.push_screen(
                    ChecklistMarkModal(
                        catalog,
                        [],
                        n_models=3,
                        initial_model=1,
                        lock_model=True,
                        on_toggle=lambda m, c, s: toggles.append((m, c, s)),
                    ),
                    lambda _r: None,
                )

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ChecklistMarkModal)
            assert modal._model_idx == 1
            assert modal._lock_model
            await pilot.press("1")
            await pilot.pause()
            assert modal._model_idx == 1
            await pilot.press("enter")
            await pilot.pause()
            assert toggles and toggles[0][0] == 1
            assert toggles[0][2] is True
            # Modal stays open for multi-mark.
            assert isinstance(app.screen, ChecklistMarkModal)
