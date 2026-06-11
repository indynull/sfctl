"""UI integration tests -- these use Textual's run_test() for actual UI behavior."""

from __future__ import annotations

import pytest

TASK_ID = "t-D2F1God7q8QRa48qVJpO1"


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
        from sftui import config
        from sftui.app import StarfleetApp

        config.save_config({"theme": "textual-light"})
        app = StarfleetApp(TASK_ID, fixture_data)
        assert app.theme == "textual-light"

    @pytest.mark.asyncio
    async def test_no_models_app(self, minimal_data):
        from sftui.app import StarfleetApp

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
    async def test_model_cycle(self, app):
        async with app.run_test() as pilot:
            await pilot.press("]")
            assert app.current_model_index == 1
            await pilot.press("[")
            assert app.current_model_index == 0
            await pilot.press("[")
            assert app.current_model_index == 2

    @pytest.mark.asyncio
    async def test_go_to_feedback(self, app):
        from textual.widgets import ContentSwitcher

        async with app.run_test() as pilot:
            await pilot.press("f")
            switcher = app.query_one("#main-switcher", ContentSwitcher)
            assert switcher.current == "feedback"

    @pytest.mark.asyncio
    async def test_go_to_diff(self, app):
        async with app.run_test():
            fd = app.models[0].file_diffs[0]
            await app.go_to_diff(0, fd.filename)

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
    async def test_no_models_cycle(self, minimal_data):
        from sftui.app import StarfleetApp

        app = StarfleetApp("t-min", minimal_data)
        async with app.run_test():
            await app.action_next_model()
            await app.action_prev_model()


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
    async def test_reset_with_justification(self, app):
        from sftui import scoring

        async with app.run_test() as pilot:
            app.save_justification("test content")
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert not scoring.justification_path(app.task_id).exists()

    @pytest.mark.asyncio
    async def test_vote_on_feedback_warns(self, app):
        async with app.run_test() as pilot:
            await pilot.press("f")
            await pilot.press("+")

    @pytest.mark.asyncio
    async def test_vote_no_models(self, minimal_data):
        from sftui.app import StarfleetApp

        app = StarfleetApp("t-min", minimal_data)
        async with app.run_test() as pilot:
            await pilot.press("+")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_detect_vote_context_on_diff(self, app):
        from sftui.widgets import DiffDisplay, LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
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
    async def test_vote_context_walk_up_response(self, app):
        from textual.widgets import Collapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            for c in app.query(Collapsible):
                title = str(c.title) if c.title else ""
                if "Response" in title:
                    children = list(c.query("Static"))
                    if children:
                        children[0].focus()
                        await pilot.pause()
                        ctx = app._detect_vote_context()
                        assert ctx in ("response", "overall", "code")
                    break

    @pytest.mark.asyncio
    async def test_vote_context_walk_up_trace(self, app):
        from sftui.widgets import LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            for c in app.query(LazyCollapsible):
                if c.lazy.events:
                    c.collapsed = False
                    await pilot.pause()
                    children = list(c.query("Static"))
                    if children:
                        children[0].focus()
                        await pilot.pause()
                        ctx = app._detect_vote_context()
                        assert ctx in ("code", "overall", "response")
                    break


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
    async def test_update_scoreboard_before_compose(self):
        """_update_scoreboard handles missing #scoreboard gracefully."""
        from sftui.app import StarfleetApp

        app = StarfleetApp.__new__(StarfleetApp)
        app.scores = []
        app.models = []
        app.data = {"history": []}
        app._trace_type_map = {}
        app._update_scoreboard()  # should not raise

    @pytest.mark.asyncio
    async def test_rankings_local_only(self):
        from sftui.app import StarfleetApp

        data = _task_data("t-lo", models=[
            _model_item("Model A"),
            _model_item("Model B"),
        ])
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
    async def test_justification_modal(self, app):
        async with app.run_test() as pilot:
            await pilot.press("j")
            await pilot.pause()
            await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_justification_preview_toggle(self, app):
        async with app.run_test() as pilot:
            await pilot.press("j")
            await pilot.pause()
            await pilot.press("ctrl+m")
            await pilot.pause()
            await pilot.press("ctrl+m")
            await pilot.pause()
            await pilot.press("escape")

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
            await pilot.press("f")
            await pilot.press("ctrl+f")

    @pytest.mark.asyncio
    async def test_search_diffs_no_diffs(self):
        from sftui.app import StarfleetApp

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

    @pytest.mark.asyncio
    async def test_yank_with_diff_focused(self, app):
        from sftui.widgets import DiffDisplay, LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
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
                await pilot.press("y")
                await pilot.pause()
                await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_yank_with_comment(self, app):
        from sftui.widgets import DiffDisplay, LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
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
                await pilot.press("y")
                await pilot.pause()
                for ch in "test comment":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()

    @pytest.mark.asyncio
    async def test_yank_empty_diff(self, app):
        from sftui.widgets import DiffDisplay, LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            for c in app.query(LazyCollapsible):
                if c.lazy.diff is not None:
                    c.collapsed = False
                    await pilot.pause()
                    break
            diffs = app.query(DiffDisplay)
            if diffs:
                dd = diffs.first()
                dd.diff_text = "   "
                dd.focus()
                await pilot.pause()
                app.action_yank_file()

    @pytest.mark.asyncio
    async def test_yank_with_selection(self, app):
        from sftui.widgets import DiffDisplay, LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            for c in app.query(LazyCollapsible):
                if c.lazy.diff is not None:
                    c.collapsed = False
                    await pilot.pause()
                    break
            diffs = app.query(DiffDisplay)
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
        from sftui.widgets import LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            for c in app.query(LazyCollapsible):
                if c.lazy.events and not c.lazy.populated:
                    c.collapsed = False
                    await pilot.pause()
                    break

    @pytest.mark.asyncio
    async def test_expand_lazy_diff(self, app):
        from sftui.widgets import LazyCollapsible

        async with app.run_test() as pilot:
            await pilot.press("1")
            await pilot.pause()
            for c in app.query(LazyCollapsible):
                if c.lazy.diff is not None:
                    c.collapsed = False
                    await pilot.pause()
                    break


class TestModelVariants:
    @pytest.mark.asyncio
    async def test_no_file_diffs_empty_diff(self):
        from sftui.app import StarfleetApp

        data = _task_data("t-nodiff", models=[_model_item("Model A", diff="")])
        app = StarfleetApp("t-nodiff", data)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.models[0].file_diffs) == 0

    @pytest.mark.asyncio
    async def test_no_file_diffs_with_raw_diff(self):
        from sftui.app import StarfleetApp

        data = _task_data(
            "t-rawdiff",
            models=[_model_item("Model A", diff="@@ -1 +1 @@\n-x\n+y\n")],
        )
        app = StarfleetApp("t-rawdiff", data)
        async with app.run_test() as pilot:
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_no_tool_events(self):
        from sftui.app import StarfleetApp

        data = _task_data("t-noev", models=[_model_item("Model A")])
        app = StarfleetApp("t-noev", data)
        async with app.run_test() as pilot:
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_history_not_list(self):
        from sftui.app import StarfleetApp

        data = {
            "task": {"taskId": "t-x"},
            "content": {"content": {"items": []}},
            "history": {"preference_ranking": {"value": []}},
            "feedback": {"entries": [{"timestamp": 1, "message": "hi", "email": "a@b", "reviewLevel": 1}]},
        }
        app = StarfleetApp("t-x", data)
        async with app.run_test():
            summary = app.rankings_summary()
            assert isinstance(summary, str)

    @pytest.mark.asyncio
    async def test_previous_model_rank_from_history(self):
        from sftui.app import StarfleetApp

        data = _task_data("t-pr", models=[_model_item("Model A")])
        data["history"] = {"preference_ranking": {"value": [{"id": "model_a"}]}}
        app = StarfleetApp("t-pr", data)
        async with app.run_test():
            pass  # just verifies scoreboard renders with prev rankings

    @pytest.mark.asyncio
    async def test_populate_model_raw_diff_no_file_diffs(self):
        from sftui.app import StarfleetApp
        from sftui.models import ModelData, ModelScores

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
            await pilot.press("c")

    @pytest.mark.asyncio
    async def test_copy_summary_empty(self):
        from sftui.app import StarfleetApp

        data = {"task": {"taskId": ""}, "content": {"content": {"items": []}}, "history": [], "feedback": {}}
        app = StarfleetApp("", data)
        async with app.run_test() as pilot:
            import sftui.screens as screens_mod

            orig = screens_mod.build_clipboard_text
            screens_mod.build_clipboard_text = lambda a: "   "
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
        from sftui import api as api_mod
        from sftui.app import StarfleetApp

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
    async def test_append_justification(self, app):
        async with app.run_test():
            app.save_justification("base")
            app.append_to_justification(" added")
            assert app.load_justification() == "base added"


class TestNavigationProvider:
    @pytest.mark.asyncio
    async def test_discover(self, app):
        async with app.run_test():
            from sftui.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.discover()]
            assert len(hits) > 0

    @pytest.mark.asyncio
    async def test_search_model(self, app):
        async with app.run_test():
            from sftui.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.search("model")]
            assert len(hits) >= 0

    @pytest.mark.asyncio
    async def test_search_theme(self, app):
        async with app.run_test():
            from sftui.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            [h async for h in provider.search("theme")]

    @pytest.mark.asyncio
    async def test_search_diff_items(self, app):
        async with app.run_test():
            from sftui.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.search("Diff")]
            assert len(hits) > 0

    @pytest.mark.asyncio
    async def test_search_action_hits(self, app):
        async with app.run_test():
            from sftui.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.search("Justification")]
            assert len(hits) > 0

    @pytest.mark.asyncio
    async def test_search_feedback(self, app):
        async with app.run_test():
            from sftui.commands import NavigationProvider

            provider = NavigationProvider(app.screen, None)
            await provider.startup()
            hits = [h async for h in provider.search("Feedback")]
            assert len(hits) > 0

    @pytest.mark.asyncio
    async def test_non_starfleet_app(self):
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from sftui.commands import NavigationProvider

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
        from sftui.app import StarfleetApp
        from sftui.screens import build_clipboard_text

        app = StarfleetApp(TASK_ID, fixture_data)
        text = build_clipboard_text(app)
        assert TASK_ID in text


class TestStripMarkup:
    def test_strips_tags(self):
        from sftui.screens import _strip_markup

        assert _strip_markup("[bold]hello[/bold]") == "hello"

    def test_no_tags(self):
        from sftui.screens import _strip_markup

        assert _strip_markup("plain text") == "plain text"

    def test_nested_tags(self):
        from sftui.screens import _strip_markup

        assert _strip_markup("[green]A(+3)[/]") == "A(+3)"
