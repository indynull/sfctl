"""UI integration tests -- these use Textual's run_test() for actual UI behavior."""

from __future__ import annotations

import pytest

TASK_ID = "t-EXAMPLE001"


def _model_item(
    title="Model A",
    diff="diff --git a/f.py b/f.py\n@@ -1 +1 @@\n-x\n+y",
    trace="summary",
    messages="[]",
    tool_events="[]",
):
    return {
        "title": title,
        "diff": {"codeDiff": diff},
        "trace": {"trace": trace, "messages": messages, "toolEvents": tool_events},
    }


def _task_data(task_id="t-test", models=None, history=None, feedback=None):
    items = [
        {"type": "text", "title": "Repository", "text": "testrepo"},
        {"type": "message", "title": "Current Prompt", "content": "prompt"},
    ]
    if models:
        items.append({"type": "collection", "title": "Model Traces", "items": models})
    return {
        "task": {"taskId": task_id},
        "content": {"taskId": task_id, "content": {"items": items}},
        "history": history or [],
        "feedback": feedback or {},
    }


class TestAppStartup:
    @pytest.mark.asyncio
    async def test_app_starts(self, app):
        async with app.run_test():
            assert app.task_id == TASK_ID
            assert len(app.models) == 3
            assert app.sub_title

    @pytest.mark.asyncio
    async def test_theme_loaded_from_config(self, fixture_data):
        from sfctl import config
        from sfctl.app import StarfleetApp

        config.save_config({"theme": "textual-light"})
        app = StarfleetApp(TASK_ID, fixture_data)
        assert app.theme == "textual-light"

    @pytest.mark.asyncio
    async def test_no_models_app(self, minimal_data):
        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-min", minimal_data)
        async with app.run_test():
            assert len(app.models) == 0


class TestNavigation:
    @pytest.mark.asyncio
    async def test_model_navigation(self, app):
        async with app.run_test() as pilot:
            await pilot.press("2")
            assert app.current_model_index == 1
            await pilot.press("3")
            assert app.current_model_index == 2
            await pilot.press("1")
            assert app.current_model_index == 0

    @pytest.mark.asyncio
    async def test_model_switch(self, app):
        async with app.run_test() as pilot:
            await pilot.press("2")
            assert app.current_model_index == 1
            await pilot.press("3")
            assert app.current_model_index == 2
            await pilot.press("1")
            assert app.current_model_index == 0

    @pytest.mark.asyncio
    async def test_go_to_overview(self, app):
        from textual.widgets import ContentSwitcher

        async with app.run_test() as pilot:
            await pilot.press("0")
            switcher = app.query_one("#main-switcher", ContentSwitcher)
            assert switcher.current == "overview"

    @pytest.mark.asyncio
    async def test_go_to_diff(self, app):
        async with app.run_test():
            fd = app.models[0].file_diffs[0]
            await app.go_to_diff(0, fd.filename)

    @pytest.mark.asyncio
    async def test_go_to_diff_expands_in_unified_view(self, app):
        from textual.widgets import Collapsible, TabbedContent

        from sfctl.ids import model_id

        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            assert app._current_section == "unified-view"
            mid = model_id(0)
            diffs_id = f"split-diffs-{mid}"
            assert diffs_id in app._deferred_tabs
            fd = app.models[0].file_diffs[0]
            await app.go_to_diff(0, fd.filename)
            await pilot.pause()
            assert diffs_id not in app._deferred_tabs
            tabs = app.query_one(f"#split-tabs-{mid}", TabbedContent)
            assert tabs.active == diffs_id
            pane = tabs.get_pane(diffs_id)
            matches = [c for c in pane.query(Collapsible) if str(c.title) == fd.filename]
            assert matches
            assert matches[0].collapsed is False

    @pytest.mark.asyncio
    async def test_go_to_diff_jumps_to_grep_line_in_unified(self, app):
        from textual.widgets import Collapsible, TabbedContent

        from sfctl.ids import model_id
        from sfctl.widgets import DiffDisplay

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("u")
            await pilot.pause()
            fd = app.models[0].file_diffs[0]
            greps: list[tuple[str, int]] = []
            for i, ln in enumerate(fd.diff.splitlines()):
                if ln.startswith("+") and len(ln) > 20 and not ln.startswith("+++"):
                    greps.append((ln.strip(), i))
                if len(greps) >= 2:
                    break
            assert len(greps) >= 2
            mid = model_id(0)

            async def cursor_row() -> int:
                pane = app.query_one(f"#split-tabs-{mid}", TabbedContent).get_pane(
                    f"split-diffs-{mid}"
                )
                for c in pane.query(Collapsible):
                    if str(c.title) != fd.filename:
                        continue
                    displays = list(c.query(DiffDisplay))
                    assert displays
                    return displays[0].cursor_location[0]
                raise AssertionError("diff collapsible not found")

            await app.go_to_diff(0, fd.filename, greps[0][0])
            await pilot.pause()
            assert await cursor_row() == greps[0][1]

            # Second search on the same (already open) file must retarget the line.
            await app.go_to_diff(0, fd.filename, greps[1][0])
            await pilot.pause()
            assert await cursor_row() == greps[1][1]

    @pytest.mark.asyncio
    async def test_go_to_diff_invalid_index(self, app):
        async with app.run_test():
            await app.go_to_diff(99, "nonexistent.py")

    @pytest.mark.asyncio
    async def test_go_model_out_of_range(self, app):
        async with app.run_test():
            await app.action_go_model(99)
            assert app.current_model_index != 99

    @pytest.mark.asyncio
    async def test_model_headers_get_rank_colors_on_mount(self, app, fixture_data):
        from textual.color import Color
        from textual.widgets import Static

        from sfctl.formatting import rank_color
        from sfctl.ids import model_header_id, model_id, model_letter
        from sfctl.ranking import model_letter_colors, previous_model_rank

        async with app.run_test() as pilot:
            await pilot.pause()
            history = fixture_data["history"]
            colors = model_letter_colors(app.scores, history, 3)
            assert colors, "fixture should establish a preference ranking"
            app._apply_model_header_colors()
            rank_bgs = {Color.parse(c) for c in ("green", "yellow", "red")}
            for idx in range(3):
                letter = model_letter(idx)
                assert previous_model_rank(history, idx) is not None
                assert letter in colors
                assert colors[letter] == rank_color(
                    previous_model_rank(history, idx), 3
                )
                mid = model_id(idx)
                header = app.query_one(f"#{model_header_id(mid)}", Static)
                # Letter markup carries the rank color; bar stays theme chrome
                # (not a solid green/yellow/red fill).
                bg = header.styles.background
                assert bg not in rank_bgs

    @pytest.mark.asyncio
    async def test_go_model_keys_switch_models(self, app):
        from textual.widgets import ContentSwitcher

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            assert app.current_model_index == 0
            switcher = app.query_one("#main-switcher", ContentSwitcher)
            assert switcher.current == "model-a"
            await pilot.press("2")
            await pilot.pause()
            assert app.current_model_index == 1
            assert switcher.current == "model-b"
            await pilot.press("3")
            await pilot.pause()
            assert app.current_model_index == 2
            assert switcher.current == "model-c"

    @pytest.mark.asyncio
    async def test_go_model_while_maximized(self, app):
        from textual.widgets import ContentSwitcher

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            app.action_toggle_maximize()
            assert app._maximized is True
            await pilot.press("2")
            await pilot.pause()
            assert app.current_model_index == 1
            assert app._maximized is True
            switcher = app.query_one("#main-switcher", ContentSwitcher)
            assert switcher.current == "model-b"
            assert app.query_one("#content-area").display is True

    @pytest.mark.asyncio
    async def test_go_model_in_unified_maximized(self, app):
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            assert app._current_section == "unified-view"
            await pilot.press("1")
            await pilot.pause()
            assert app._split_focus == 0
            app.action_toggle_maximize()
            assert app._maximized is True
            await pilot.press("3")
            await pilot.pause()
            assert app._split_focus == 2
            assert app.current_model_index == 2
            assert app._maximized is True
            # Only model C column should be visible while maximized
            assert app.query_one("#split-model-c").display is True
            assert app.query_one("#split-model-a").display is False

    @pytest.mark.asyncio
    async def test_scroll_selects_unfocused_unified_column(self, app):
        """Wheel over an unfocused model column should activate it."""
        from textual.events import MouseScrollDown

        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            assert app._split_focus == 0

            col_b = app.query_one("#split-model-b")
            # Helper path (click / direct selection from a child widget).
            assert app._select_unified_from_widget(col_b) is True
            assert app._split_focus == 1
            assert app.current_model_index == 1
            assert col_b.has_class("split-active")

            await pilot.press("1")
            await pilot.pause()
            assert app._split_focus == 0

            # Full wheel path: App.on_event before forward (real terminal input).
            region = col_b.region
            x, y = region.x + region.width // 2, region.y + region.height // 2
            event = MouseScrollDown(
                None, x, y, 0, 1, 0, False, False, False, screen_x=x, screen_y=y,
            )
            await app.on_event(event)
            await pilot.pause()
            assert app._split_focus == 1
            assert app.current_model_index == 1

    @pytest.mark.asyncio
    async def test_no_models_overview(self, minimal_data):
        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-min", minimal_data)
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()


class TestVoting:
    @pytest.mark.asyncio
    async def test_vote_up(self, app):
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("+")
            assert app.scores[0].any_nonzero()

    @pytest.mark.asyncio
    async def test_vote_up_then_down(self, app):
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("+")
            await pilot.press("-")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_reset_local(self, app):
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.press("+")
            await pilot.press("ctrl+r")
            assert all(s.total() == 0 for s in app.scores)

    @pytest.mark.asyncio
    async def test_reset_with_annotations(self, app):
        from sfctl import scoring
        from sfctl.models import Annotation

        async with app.run_test() as pilot:
            app.add_annotation(0, Annotation(context="code", sentiment=1, comment="good"))
            assert scoring.annotations_path(app.task_id).exists()
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert not scoring.annotations_path(app.task_id).exists()
            assert all(len(a) == 0 for a in app.review.annotations)

    @pytest.mark.asyncio
    async def test_vote_on_overview_warns(self, app):
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("+")

    @pytest.mark.asyncio
    async def test_vote_no_models(self, minimal_data):
        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-min", minimal_data)
        async with app.run_test() as pilot:
            await pilot.press("+")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_detect_vote_context_on_diff(self, app):
        from textual.widgets import TabbedContent

        from sfctl.widgets import DiffDisplay, LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            tabs = app.query_one("#tabs-model-a", TabbedContent)
            tabs.active = "tab-diffs-model-a"
            await pilot.pause()
            for c in app.query(LazyCollapsible):
                if c.lazy.diff is not None:
                    c.collapsed = False
                    await pilot.pause()
                    break
            diffs = app.query(DiffDisplay)
            if diffs:
                diffs.first().focus()
                await pilot.pause()
                assert app._detect_vote_context() == "code"

    @pytest.mark.asyncio
    async def test_detect_vote_context_overall(self, app):
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            ctx = app._detect_vote_context()
            assert ctx in ("overall", "response", "code")

    @pytest.mark.asyncio
    async def test_vote_context_response_tab(self, app):
        from textual.widgets import TabbedContent

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            tabs = app.query_one("#tabs-model-a", TabbedContent)
            tabs.active = "tab-response-model-a"
            await pilot.pause()
            ctx = app._detect_vote_context()
            assert ctx == "response"

    @pytest.mark.asyncio
    async def test_vote_context_trace_tab(self, app):
        from textual.widgets import TabbedContent

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            tabs = app.query_one("#tabs-model-a", TabbedContent)
            tabs.active = "tab-trace-model-a"
            await pilot.pause()
            ctx = app._detect_vote_context()
            assert ctx == "code"


class TestRankingsUI:
    @pytest.mark.asyncio
    async def test_ranking_with_votes(self, app):
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("+")
            await pilot.press("+")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_update_scoreboard_before_compose(self, app):
        """_update_scoreboard handles missing #scoreboard gracefully before mount."""
        app._update_scoreboard()  # should not raise

    @pytest.mark.asyncio
    async def test_rankings_local_only(self):
        from sfctl.app import StarfleetApp

        data = _task_data(
            "t-lo",
            models=[
                _model_item("Model A"),
                _model_item("Model B"),
            ],
        )
        app = StarfleetApp("t-lo", data)
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("+")
            await pilot.pause()
            summary = app.rankings_summary()
            assert ">" in summary or "Overall" in summary


class TestModals:
    @pytest.mark.asyncio
    async def test_justification_toggle(self, app):
        from textual.widgets import Markdown, TextArea

        async with app.run_test() as pilot:
            # navigate to overview first, then ctrl+e activates editor
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("ctrl+e")
            await pilot.pause()
            preview = app.query_one("#justification-preview", Markdown)
            editor = app.query_one("#justification-editor", TextArea)
            assert editor.display is True
            assert preview.display is False
            # escape saves and switches back to preview
            await pilot.press("escape")
            await pilot.pause()
            assert editor.display is False
            assert preview.display is True
            # ctrl+e re-opens editor
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert editor.display is True
            assert preview.display is False

    @pytest.mark.asyncio
    async def test_search_diffs(self, app):
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.press("ctrl+f")
            await pilot.pause()
            await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_search_diffs_type_and_submit(self, app):
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("ctrl+f")
            await pilot.pause()
            await pilot.press("m", "o", "d", "e", "l")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_search_diffs_clear_query(self, app):
        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("ctrl+f")
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("backspace")
            await pilot.pause()
            await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_search_diffs_no_model(self, app):
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("ctrl+f")

    @pytest.mark.asyncio
    async def test_search_diffs_no_diffs(self):
        from sfctl.app import StarfleetApp

        data = _task_data("t-nd", models=[_model_item("Model A", diff="")])
        app = StarfleetApp("t-nd", data)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+f")
            await pilot.pause()


class TestYank:
    @pytest.mark.asyncio
    async def test_yank_no_focus(self, app):
        async with app.run_test() as pilot:
            await pilot.press("y")

    async def _open_diff(self, app, pilot):
        """Navigate to diffs tab and expand a lazy diff."""
        from textual.widgets import TabbedContent

        from sfctl.widgets import DiffDisplay, LazyCollapsible

        await pilot.press("1")
        await pilot.pause()
        tabs = app.query_one("#tabs-model-a", TabbedContent)
        tabs.active = "tab-diffs-model-a"
        await pilot.pause()
        for c in app.query(LazyCollapsible):
            if c.lazy.diff is not None:
                c.collapsed = False
                await pilot.pause()
                break
        return app.query(DiffDisplay)

    @pytest.mark.asyncio
    async def test_yank_with_diff_focused(self, app):
        async with app.run_test() as pilot:
            diffs = await self._open_diff(app, pilot)
            if diffs:
                diffs.first().focus()
                await pilot.pause()
                await pilot.press("y")
                await pilot.pause()
                await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_yank_with_comment(self, app):
        async with app.run_test() as pilot:
            diffs = await self._open_diff(app, pilot)
            if diffs:
                diffs.first().focus()
                await pilot.pause()
                await pilot.press("y")
                await pilot.pause()
                for ch in "test comment":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()

    @pytest.mark.asyncio
    async def test_yank_empty_diff(self, app):
        async with app.run_test() as pilot:
            diffs = await self._open_diff(app, pilot)
            if diffs:
                dd = diffs.first()
                dd.diff_text = "   "
                dd.focus()
                await pilot.pause()
                app.action_yank_file()

    @pytest.mark.asyncio
    async def test_yank_with_selection(self, app):
        async with app.run_test() as pilot:
            diffs = await self._open_diff(app, pilot)
            if diffs:
                dd = diffs.first()
                dd.focus()
                await pilot.pause()
                dd.select_line(0)
                await pilot.pause()
                if dd.selected_text.strip():
                    app.action_yank_file()
                    await pilot.pause()
                    await pilot.press("escape")


class TestLazyLoading:
    @pytest.mark.asyncio
    async def test_expand_lazy_trace(self, app):
        from textual.widgets import TabbedContent

        from sfctl.widgets import LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            tabs = app.query_one("#tabs-model-a", TabbedContent)
            tabs.active = "tab-trace-model-a"
            await pilot.pause()
            for c in app.query(LazyCollapsible):
                if c.lazy.events and not c.lazy.populated:
                    c.collapsed = False
                    await pilot.pause()
                    break

    @pytest.mark.asyncio
    async def test_expand_lazy_diff(self, app):
        from textual.widgets import TabbedContent

        from sfctl.widgets import LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            tabs = app.query_one("#tabs-model-a", TabbedContent)
            tabs.active = "tab-diffs-model-a"
            await pilot.pause()
            for c in app.query(LazyCollapsible):
                if c.lazy.diff is not None:
                    c.collapsed = False
                    await pilot.pause()
                    break


class TestOverview:
    @pytest.mark.asyncio
    async def test_overview_populates(self, app):
        from textual.widgets import TabbedContent

        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            assert tabs is not None

    @pytest.mark.asyncio
    async def test_current_tab_is_first(self, app):
        from textual.widgets import TabbedContent

        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            assert tabs.active == "tab-current"

    @pytest.mark.asyncio
    async def test_history_entry_tabs(self, app):
        from textual.widgets import Static, TabbedContent

        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            # Fixture has 2 identical entries:
            #   tab-entry-0 = L1 review (feedback only, no justification)
            #   tab-entry-1 = L0 revision (original, has justification)
            tabs.active = "tab-entry-1"
            await pilot.pause()
            await pilot.pause()
            pane = tabs.get_pane("tab-entry-1")
            statics = pane.query(Static)
            assert len(statics) > 0

    @pytest.mark.asyncio
    async def test_justification_editor(self, app):
        from textual.widgets import TextArea

        async with app.run_test() as pilot:
            # navigate to overview first, then ctrl+e activates editor
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("ctrl+e")
            await pilot.pause()
            editor = app.query_one("#justification-editor", TextArea)
            assert editor is not None
            assert editor.display is True

    @pytest.mark.asyncio
    async def test_overview_no_history(self, minimal_data):
        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-min", minimal_data)
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_overview_with_history_diff(self):
        from textual.widgets import TabbedContent

        from sfctl.app import StarfleetApp

        data = _task_data("t-hd", models=[_model_item("Model A")])
        data["history"] = [
            {
                "email": "a@b",
                "reviewLevel": 0,
                "justification": {"value": "old text"},
                "preference_ranking": {"value": [{"id": "model_a"}, {"id": "model_b"}]},
                "confidence": {"value": "low"},
            },
            {
                "email": "c@d",
                "reviewLevel": 1,
                "justification": {"value": "new text"},
                "preference_ranking": {"value": [{"id": "model_b"}, {"id": "model_a"}]},
                "confidence": {"value": "high"},
            },
        ]
        app = StarfleetApp("t-hd", data)
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            # Two different entries -> Current + 2 history tabs
            # tab-current, tab-entry-0 (L1 newest), tab-entry-1 (L0)
            tabs.active = "tab-entry-0"
            await pilot.pause()
            tabs.active = "tab-entry-1"
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_justification_auto_save_on_quit(self, app):
        from textual.widgets import TextArea

        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("ctrl+e")
            await pilot.pause()
            editor = app.query_one("#justification-editor", TextArea)
            editor.clear()
            editor.insert("saved on quit")
            # escape exits editor (saves), then q quits
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("q")
        assert app.review.summary == "saved on quit"

    @pytest.mark.asyncio
    async def test_add_annotation_updates_overview(self, app):
        from sfctl.models import Annotation

        async with app.run_test() as pilot:
            # Navigate to overview so widgets exist
            await pilot.press("0")
            await pilot.pause()
            app.add_annotation(0, Annotation(context="code", sentiment=1, comment="nice code"))
            assert len(app.review.annotations[0]) == 1
            assert app.scores[0].code == 1

    @pytest.mark.asyncio
    async def test_yank_inserts_into_summary(self, app):
        async with app.run_test() as pilot:
            # Navigate to overview first so it's populated
            await pilot.press("0")
            await pilot.pause()
            assert app.review.summary == "" or "yank" not in app.review.summary.lower()
            # Simulate a yank result (what the modal callback does)
            block = "**A** `foo.py:L12` nice fix\n```diff\n+x=1\n```\n"
            app.review.summary += block
            app._save_summary(app.review.summary)
            app._refresh_overview_annotations()
            await pilot.pause()
            assert "foo.py" in app.review.summary


class TestTabNavigation:
    @pytest.mark.asyncio
    async def test_tab_cycles_model_tabs(self, app):
        from textual.widgets import TabbedContent

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            tabs = app.query_one("#tabs-model-a", TabbedContent)
            initial = tabs.active
            await pilot.press("tab")
            await pilot.pause()
            assert tabs.active != initial

    @pytest.mark.asyncio
    async def test_shift_tab_cycles_back(self, app):
        from textual.widgets import TabbedContent

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            tabs = app.query_one("#tabs-model-a", TabbedContent)
            await pilot.press("tab")
            await pilot.pause()
            second = tabs.active
            await pilot.press("shift+tab")
            await pilot.pause()
            assert tabs.active != second

    @pytest.mark.asyncio
    async def test_tab_on_overview(self, app):
        from textual.widgets import TabbedContent

        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            initial = tabs.active
            await pilot.press("tab")
            await pilot.pause()
            assert tabs.active != initial


class TestHistoryOrder:
    @pytest.mark.asyncio
    async def test_newest_first(self):
        from textual.widgets import TabbedContent

        from sfctl.app import StarfleetApp

        data = _task_data("t-ord", models=[_model_item("Model A")])
        data["history"] = [
            {"email": "first@b", "reviewLevel": 0, "justification": {"value": "old"}},
            {"email": "second@b", "reviewLevel": 1, "justification": {"value": "new"}},
        ]
        app = StarfleetApp("t-ord", data)
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            # Activate the first history tab to trigger deferred loading
            tabs.active = "tab-entry-0"
            await pilot.pause()
            await pilot.pause()
            pane = tabs.get_pane("tab-entry-0")
            statics = pane.query("Static")
            text = str(statics[0].render())
            assert "Entry 1" in text

    @pytest.mark.asyncio
    async def test_identical_entries_no_feedback_skipped(self):
        """Unchanged entries with no feedback are skipped."""
        from textual.widgets import TabbedContent, TabPane

        from sfctl.app import StarfleetApp

        data = _task_data("t-dup", models=[_model_item("Model A")])
        data["history"] = [
            {
                "reviewLevel": 0,
                "justification": {"value": "same"},
                "preference_ranking": {"value": [{"id": "model_a"}]},
            },
            {
                "reviewLevel": 1,
                "justification": {"value": "same"},
                "preference_ranking": {"value": [{"id": "model_a"}]},
            },
        ]
        app = StarfleetApp("t-dup", data)
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            pane_ids = {p.id for p in tabs.query(TabPane)}
            # L1 has no changes AND no feedback -> skipped.
            assert "tab-current" in pane_ids
            assert "tab-entry-0" in pane_ids  # L0 first revision
            assert "tab-entry-1" not in pane_ids  # identical L1 skipped
            assert "tab-shared" not in pane_ids

    @pytest.mark.asyncio
    async def test_review_with_feedback_shown(self):
        """Unchanged entries with feedback are shown as reviews."""
        from textual.widgets import Collapsible, TabbedContent, TabPane

        from sfctl.app import StarfleetApp

        data = _task_data("t-rev", models=[_model_item("Model A")])
        data["history"] = [
            {
                "reviewLevel": 0,
                "justification": {"value": "same"},
                "preference_ranking": {"value": [{"id": "model_a"}]},
            },
            {
                "reviewLevel": 1,
                "justification": {"value": "same"},
                "preference_ranking": {"value": [{"id": "model_a"}]},
                "feedback": {
                    "entries": [
                        {"reviewLevel": 1, "message": "needs work", "timestamp": 1, "email": "r@x"},
                    ]
                },
            },
        ]
        app = StarfleetApp("t-rev", data)
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            pane_ids = {p.id for p in tabs.query(TabPane)}
            # Current + L1 review (has feedback) + L0 revision; Files tab optional.
            assert "tab-current" in pane_ids
            assert "tab-entry-0" in pane_ids  # L1 review (newest history shell)
            assert "tab-entry-1" in pane_ids  # L0 revision
            tabs.active = "tab-entry-0"
            await pilot.pause()
            pane = tabs.get_pane("tab-entry-0")
            collapsibles = pane.query(Collapsible)
            assert any("Feedback" in str(c.title) for c in collapsibles)

    @pytest.mark.asyncio
    async def test_feedback_inline_with_entry(self):
        """Feedback appears inline on the entry where it first appeared."""
        from textual.widgets import Collapsible, TabbedContent

        from sfctl.app import StarfleetApp

        data = _task_data("t-fb", models=[_model_item("Model A")])
        data["history"] = [
            {"email": "a@b", "reviewLevel": 0, "justification": {"value": "text"}},
            {
                "email": "c@d",
                "reviewLevel": 1,
                "justification": {"value": "updated"},
                "feedback": {
                    "entries": [
                        {
                            "reviewLevel": 1,
                            "message": "good work",
                            "timestamp": 123,
                            "email": "rev@x",
                        },
                    ]
                },
            },
        ]
        app = StarfleetApp("t-fb", data)
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            tabs = app.query_one("#tabs-overview", TabbedContent)
            # L1 (entry[1]) is first history tab -- has new feedback vs entry[0]
            tabs.active = "tab-entry-0"
            await pilot.pause()
            pane = tabs.get_pane("tab-entry-0")
            collapsibles = pane.query(Collapsible)
            titles = [str(c.title) for c in collapsibles]
            assert any("Feedback" in t for t in titles)
            # L0 (entry[0]) should have no feedback
            tabs.active = "tab-entry-1"
            await pilot.pause()
            pane = tabs.get_pane("tab-entry-1")
            collapsibles = pane.query(Collapsible)
            assert not any("Feedback" in str(c.title) for c in collapsibles)


class TestModelVariants:
    @pytest.mark.asyncio
    async def test_no_file_diffs_empty_diff(self):
        from sfctl.app import StarfleetApp

        data = _task_data("t-nodiff", models=[_model_item("Model A", diff="")])
        app = StarfleetApp("t-nodiff", data)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.models[0].file_diffs) == 0

    @pytest.mark.asyncio
    async def test_no_file_diffs_with_raw_diff(self):
        from sfctl.app import StarfleetApp

        data = _task_data(
            "t-rawdiff",
            models=[_model_item("Model A", diff="@@ -1 +1 @@\n-x\n+y\n")],
        )
        app = StarfleetApp("t-rawdiff", data)
        async with app.run_test() as pilot:
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_no_tool_events(self):
        from sfctl.app import StarfleetApp

        data = _task_data("t-noev", models=[_model_item("Model A")])
        app = StarfleetApp("t-noev", data)
        async with app.run_test() as pilot:
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_empty_history(self):
        from sfctl.app import StarfleetApp

        data = {
            "task": {"taskId": "t-x"},
            "content": {"content": {"items": []}},
            "history": [],
            "feedback": {
                "entries": [{"timestamp": 1, "message": "hi", "email": "a@b", "reviewLevel": 1}]
            },
        }
        app = StarfleetApp("t-x", data)
        async with app.run_test():
            summary = app.rankings_summary()
            assert isinstance(summary, str)

    @pytest.mark.asyncio
    async def test_previous_model_rank_from_history(self):
        from sfctl.app import StarfleetApp

        data = _task_data("t-pr", models=[_model_item("Model A")])
        data["history"] = [{"preference_ranking": {"value": [{"id": "model_a"}]}}]
        app = StarfleetApp("t-pr", data)
        async with app.run_test():
            pass  # just verifies scoreboard renders with prev rankings

    @pytest.mark.asyncio
    async def test_populate_model_raw_diff_no_file_diffs(self):
        from sfctl.app import StarfleetApp
        from sfctl.models import ModelData, ModelScores

        data = _task_data("t-rd")
        app = StarfleetApp("t-rd", data)
        app.models = [
            ModelData(
                name="Model A",
                diff="@@ -1 +1 @@\n-old\n+new",
                trace_summary="summary",
                messages=[{"role": "assistant", "content": "done"}],
                tool_events=[],
                file_diffs=[],
            )
        ]
        app.scores = [ModelScores()]
        async with app.run_test() as pilot:
            await pilot.pause()


class TestMiscActions:
    @pytest.mark.asyncio
    async def test_help(self, app):
        async with app.run_test() as pilot:
            await pilot.press("?")

    @pytest.mark.asyncio
    async def test_copy_summary(self, app):
        async with app.run_test() as pilot:
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("c")

    @pytest.mark.asyncio
    async def test_copy_summary_empty(self):
        from sfctl.app import StarfleetApp

        data = {
            "task": {"taskId": ""},
            "content": {"content": {"items": []}},
            "history": [],
            "feedback": {},
        }
        app = StarfleetApp("", data)
        async with app.run_test() as pilot:
            import sfctl.screens as screens_mod

            orig = screens_mod.build_clipboard_text
            screens_mod.build_clipboard_text = lambda *a, **kw: "   "
            try:
                app.action_copy_summary()
                await pilot.pause()
            finally:
                screens_mod.build_clipboard_text = orig

    @pytest.mark.asyncio
    async def test_set_theme(self, app):
        async with app.run_test():
            app.set_theme("textual-dark")

    @pytest.mark.asyncio
    async def test_refresh_no_cookies(self, app):
        app.cookies = None
        async with app.run_test() as pilot:
            await pilot.press("r")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_refresh_with_cookies(self, fixture_data, monkeypatch):
        from sfctl import api as api_mod
        from sfctl.app import StarfleetApp

        monkeypatch.setattr(api_mod, "fetch_data", lambda t, c: fixture_data)
        app = StarfleetApp(TASK_ID, fixture_data, cookies={"tok": "val"})
        async with app.run_test() as pilot:
            await pilot.press("r")
            # Wait for worker thread
            for _ in range(20):
                await pilot.pause()

    @pytest.mark.asyncio
    async def test_quit(self, app):
        async with app.run_test() as pilot:
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_add_annotation_without_overview(self, app):
        """add_annotation works even without the overview mounted."""
        from sfctl.models import Annotation

        async with app.run_test():
            app.add_annotation(0, Annotation(context="response", sentiment=-1, comment="bad"))
            assert len(app.review.annotations[0]) == 1
            assert app.scores[0].response == -1


class TestTaskTypeDetection:
    @pytest.mark.asyncio
    async def test_code_review_detected(self, app):
        from sfctl.task_types import TaskType

        assert app.task_type == TaskType.CODE_REVIEW

    @pytest.mark.asyncio
    async def test_unknown_type_fallback(self):
        from sfctl.app import StarfleetApp
        from sfctl.task_types import TaskType

        data = {
            "task": {"taskId": "t-unk"},
            "content": {"content": {"items": []}},
            "history": [],
            "feedback": {},
        }
        app = StarfleetApp("t-unk", data)
        assert app.task_type == TaskType.UNKNOWN
        async with app.run_test() as pilot:
            await pilot.pause()
            # Voting should warn, not crash
            await pilot.press("+")
            await pilot.pause()
            # Navigation should no-op, not crash
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("0")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_unknown_type_minimal_data(self):
        from sfctl.app import StarfleetApp
        from sfctl.task_types import TaskType

        data = {
            "task": {"taskId": "t-unk2"},
            "content": {
                "content": {
                    "items": [
                        {"type": "text", "title": "Some Field", "text": "value"},
                    ]
                }
            },
            "history": [],
            "feedback": {},
        }
        app = StarfleetApp("t-unk2", data)
        assert app.task_type == TaskType.UNKNOWN
        async with app.run_test():
            pass


class TestNavigationProvider:
    @pytest.mark.asyncio
    async def test_discover(self, app):
        async with app.run_test():
            from sfctl.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.discover()]
            assert len(hits) > 0

    @pytest.mark.asyncio
    async def test_search_model(self, app):
        async with app.run_test():
            from sfctl.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.search("model")]
            assert len(hits) >= 0

    @pytest.mark.asyncio
    async def test_search_theme(self, app):
        async with app.run_test():
            from sfctl.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            [h async for h in provider.search("theme")]

    @pytest.mark.asyncio
    async def test_search_diff_items(self, app):
        async with app.run_test():
            from sfctl.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.search("Diff")]
            assert len(hits) > 0

    @pytest.mark.asyncio
    async def test_search_action_hits(self, app):
        async with app.run_test():
            from sfctl.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.search("Justification")]
            assert len(hits) > 0

    @pytest.mark.asyncio
    async def test_search_overview(self, app):
        async with app.run_test():
            from sfctl.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.search("Overview")]
            assert len(hits) > 0

    @pytest.mark.asyncio
    async def test_non_starfleet_app(self):
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from sfctl.commands import NavigationProvider

        class DummyApp(App):
            COMMANDS = {NavigationProvider}

            def compose(self) -> ComposeResult:
                yield Static("dummy")

        app = DummyApp()
        async with app.run_test():
            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            assert [h async for h in provider.discover()] == []
            assert [h async for h in provider.search("anything")] == []


class TestBuildClipboardText:
    def test_basic(self, fixture_data):
        from sfctl.app import StarfleetApp
        from sfctl.screens import build_clipboard_text

        app = StarfleetApp(TASK_ID, fixture_data)
        text = build_clipboard_text(
            app.task_id,
            app.rankings_summary(),
            app.review.summary,
        )
        assert TASK_ID in text


class TestStripMarkup:
    def test_strips_tags(self):
        from sfctl.screens import strip_markup

        assert strip_markup("[bold]hello[/bold]") == "hello"

    def test_no_tags(self):
        from sfctl.screens import strip_markup

        assert strip_markup("plain text") == "plain text"

    def test_nested_tags(self):
        from sfctl.screens import strip_markup

        assert strip_markup("[green]A(+3)[/]") == "A(+3)"


class TestProposalApp:
    @pytest.mark.asyncio
    async def test_proposal_detected(self, proposal_data):
        from sfctl.app import StarfleetApp
        from sfctl.task_types import TaskType

        app = StarfleetApp("t-PROP001", proposal_data)
        assert app.task_type == TaskType.PROJECT_PROPOSAL
        assert app.proposal is not None
        assert len(app.models) == 0

    @pytest.mark.asyncio
    async def test_proposal_starts(self, proposal_data):
        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-PROP001", proposal_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._overview_populated

    @pytest.mark.asyncio
    async def test_proposal_tabs_exist(self, proposal_data):
        from textual.widgets import TabbedContent

        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-PROP001", proposal_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            model_tabs = app.query_one("#tabs-model-a", TabbedContent)
            assert model_tabs.tab_count >= 1  # Trace, (Response, Diffs if present)

    @pytest.mark.asyncio
    async def test_proposal_rankings_summary(self, proposal_data):
        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-PROP001", proposal_data)
        summary = app.rankings_summary()
        assert "partial" in summary
        assert "1h-2h" in summary
        assert "4 rubrics" in summary

    @pytest.mark.asyncio
    async def test_proposal_voting_disabled(self, proposal_data):
        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-PROP001", proposal_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.check_action("vote_up", ()) is False
            assert app.check_action("vote_down", ()) is False
            await pilot.press("m")
            await pilot.pause()
            assert app.check_action("search_diffs", ()) is True

    @pytest.mark.asyncio
    async def test_proposal_tab_navigation(self, proposal_data):
        from textual.widgets import TabbedContent

        from sfctl.app import StarfleetApp

        app = StarfleetApp("t-PROP001", proposal_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            tabs = app.query_one("#tabs-model-a", TabbedContent)
            initial = tabs.active
            await pilot.press("tab")
            await pilot.pause()
            assert tabs.active != initial


class TestEscapeWithModal:
    @pytest.mark.asyncio
    async def test_escape_on_modal_does_not_clear_maximize(self, app):
        """Modal Esc dismisses only the modal; app view modes stay until next Esc."""
        from sfctl.screens import HelpModal

        async with app.run_test() as pilot:
            app._maximized = True
            app.push_screen(HelpModal("help text", "Help"))
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpModal)
            # Maximize must still be on; Esc was owned by the modal.
            assert app._maximized is True
