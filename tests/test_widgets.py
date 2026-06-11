"""Tests for widget helpers and DiffDisplay."""

from __future__ import annotations

import pytest


class TestSanitize:
    def test_strips_brackets_and_newlines(self):
        from sfctl.widgets import _sanitize

        assert _sanitize("foo[bar]\nbaz") == "foo(bar) baz"

    def test_truncates(self):
        from sfctl.widgets import _sanitize

        assert len(_sanitize("x" * 300, 100)) <= 100

    def test_empty(self):
        from sfctl.widgets import _sanitize

        assert _sanitize("") == ""


class TestBuildLineStyles:
    def test_returns_styles_for_diff(self):
        from sfctl.widgets import _build_line_styles

        diff = "@@ -1,3 +1,3 @@\n-old line\n+new line\n context"
        styles = _build_line_styles(diff)
        assert len(styles) == 4

    def test_empty_text(self):
        from sfctl.widgets import _build_line_styles

        styles = _build_line_styles("")
        assert isinstance(styles, list)


class TestFormatValue:
    def test_none(self):
        from sfctl.widgets import _format_value

        assert "null" in _format_value(None)

    def test_bool(self):
        from sfctl.widgets import _format_value

        assert "True" in _format_value(True)

    def test_int(self):
        from sfctl.widgets import _format_value

        assert "42" in _format_value(42)

    def test_float(self):
        from sfctl.widgets import _format_value

        assert "3.14" in _format_value(3.14)

    def test_string(self):
        from sfctl.widgets import _format_value

        assert "hello" in _format_value("hello")

    def test_empty_string(self):
        from sfctl.widgets import _format_value

        assert '""' in _format_value("")

    def test_list_empty(self):
        from sfctl.widgets import _format_value

        assert "empty list" in _format_value([])

    def test_list_short(self):
        from sfctl.widgets import _format_value

        assert "1" in _format_value([1, 2, 3])

    def test_list_long(self):
        from sfctl.widgets import _format_value

        assert "+5" in _format_value(list(range(10)))

    def test_dict_empty(self):
        from sfctl.widgets import _format_value

        assert "{...}" in _format_value({})

    def test_dict_short(self):
        from sfctl.widgets import _format_value

        assert "a" in _format_value({"a": 1})

    def test_dict_long(self):
        from sfctl.widgets import _format_value

        assert "+6" in _format_value({str(i): i for i in range(10)})

    def test_other_type(self):
        from sfctl.widgets import _format_value

        assert "object" in _format_value(object())


class TestTryParse:
    def test_json_dict(self):
        from sfctl.widgets import _try_parse

        assert _try_parse('{"a":1}') == {"a": 1}

    def test_json_list(self):
        from sfctl.widgets import _try_parse

        assert _try_parse("[1,2]") == [1, 2]

    def test_non_json(self):
        from sfctl.widgets import _try_parse

        assert _try_parse("not json") == "not json"

    def test_json_scalar(self):
        from sfctl.widgets import _try_parse

        assert _try_parse("42") == "42"

    def test_non_string(self):
        from sfctl.widgets import _try_parse

        assert _try_parse(123) == 123


class TestTraceEventDetailWidgets:
    def test_dict_args_and_dict_output(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {
            "args": {"path": "/tmp", "cmd": "ls"},
            "output": {"status": "ok", "result": "done"},
        }
        widgets = trace_event_detail_widgets(ev)
        assert len(widgets) >= 4  # args header + 2 arg lines + output header + 2 output lines

    def test_list_args(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"arguments": [1, 2, 3]}
        assert len(trace_event_detail_widgets(ev)) >= 1

    def test_string_args(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"input": "hello world"}
        assert len(trace_event_detail_widgets(ev)) >= 1

    def test_list_output(self):
        from sfctl.widgets import trace_event_detail_widgets

        assert len(trace_event_detail_widgets({"result": [1, 2]})) >= 1

    def test_string_output(self):
        from sfctl.widgets import trace_event_detail_widgets

        assert len(trace_event_detail_widgets({"response": "some output"})) >= 1

    def test_empty_event(self):
        from sfctl.widgets import trace_event_detail_widgets

        assert trace_event_detail_widgets({}) == []

    def test_json_string_args(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"args": '{"key": "val"}'}
        widgets = trace_event_detail_widgets(ev)
        assert len(widgets) >= 1

    def test_empty_string_args_skipped(self):
        from sfctl.widgets import trace_event_detail_widgets

        assert trace_event_detail_widgets({"args": ""}) == []

    def test_empty_string_output_skipped(self):
        from sfctl.widgets import trace_event_detail_widgets

        assert trace_event_detail_widgets({"output": ""}) == []


class TestLazyCollapsible:
    def test_for_diff(self):
        from sfctl.widgets import LazyCollapsible

        lc = LazyCollapsible.for_diff("file.py", "diff content", "A")
        assert lc.lazy.diff == "diff content"
        assert lc.lazy.letter == "A"
        assert lc.lazy.filename == "file.py"
        assert lc.collapsed is True

    def test_for_trace(self):
        from sfctl.widgets import LazyCollapsible

        events = [{"name": "test"}]
        lc = LazyCollapsible.for_trace(title="Trace", events=events)
        assert lc.lazy.events == events
        assert lc.lazy.populated is False

    def test_default_payload(self):
        from sfctl.widgets import LazyCollapsible

        lc = LazyCollapsible(title="empty")
        assert lc.lazy.diff is None
        assert lc.lazy.events == []


class TestDiffDisplay:
    @pytest.mark.asyncio
    async def test_renders(self):
        from textual.app import App, ComposeResult

        from sfctl.widgets import DiffDisplay

        diff_text = "@@ -1,3 +10,3 @@\n-old line\n+new line\n context"

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffDisplay(diff_text, "A", "test.py")

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            dd = app.query_one(DiffDisplay)
            assert dd.diff_text == diff_text
            assert dd.model_name == "A"
            assert dd.filename == "test.py"
            # Wait for tokenization
            for _ in range(20):
                if dd._line_styles is not None:
                    break
                await pilot.pause()

    @pytest.mark.asyncio
    async def test_empty_diff(self):
        from textual.app import App, ComposeResult

        from sfctl.widgets import DiffDisplay

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffDisplay("", "B", "empty.py")

        app = TestApp()
        async with app.run_test(size=(80, 24)):
            dd = app.query_one(DiffDisplay)
            assert dd._gutter_width >= 1

    @pytest.mark.asyncio
    async def test_double_start_tokenize(self):
        from textual.app import App, ComposeResult

        from sfctl.widgets import DiffDisplay

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffDisplay("@@ -1 +1 @@\n-x\n+y", "A", "f.py")

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            dd = app.query_one(DiffDisplay)
            await pilot.pause()
            dd._start_tokenize()  # second call -- should early-return
            assert dd._tokenize_started
