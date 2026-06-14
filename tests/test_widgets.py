"""Tests for widget helpers and DiffDisplay."""

from __future__ import annotations

import pytest


class TestSanitize:
    def test_strips_brackets_and_newlines(self):
        from sfctl.widgets import sanitize

        assert sanitize("foo[bar]\nbaz") == "foo(bar) baz"

    def test_truncates(self):
        from sfctl.widgets import sanitize

        assert len(sanitize("x" * 300, 100)) <= 100

    def test_empty(self):
        from sfctl.widgets import sanitize

        assert sanitize("") == ""


class TestParseDiffLines:
    def test_parses_kinds(self):
        from sfctl.diff import parse_diff_lines

        diff = "@@ -1,3 +1,3 @@\n-old line\n+new line\n context"
        lines = parse_diff_lines(diff)
        assert len(lines) == 4
        assert lines[0].kind == "hunk"
        assert lines[1].kind == "del"
        assert lines[2].kind == "add"
        assert lines[3].kind == "ctx"

    def test_strips_prefix(self):
        from sfctl.diff import parse_diff_lines

        diff = "@@ -1,2 +1,2 @@\n-old\n+new\n ctx"
        lines = parse_diff_lines(diff)
        assert lines[1].text == "old"
        assert lines[2].text == "new"
        assert lines[3].text == "ctx"

    def test_empty_text(self):
        from sfctl.diff import parse_diff_lines

        lines = parse_diff_lines("")
        assert isinstance(lines, list)
        assert len(lines) == 1


class TestFormatValue:
    def test_none(self):
        from sfctl.widgets import format_value

        assert "null" in format_value(None)

    def test_bool(self):
        from sfctl.widgets import format_value

        assert "True" in format_value(True)

    def test_int(self):
        from sfctl.widgets import format_value

        assert "42" in format_value(42)

    def test_float(self):
        from sfctl.widgets import format_value

        assert "3.14" in format_value(3.14)

    def test_string(self):
        from sfctl.widgets import format_value

        assert "hello" in format_value("hello")

    def test_empty_string(self):
        from sfctl.widgets import format_value

        assert '""' in format_value("")

    def test_list_empty(self):
        from sfctl.widgets import format_value

        assert "empty list" in format_value([])

    def test_list_short(self):
        from sfctl.widgets import format_value

        assert "1" in format_value([1, 2, 3])

    def test_list_long(self):
        from sfctl.widgets import format_value

        assert "+5" in format_value(list(range(10)))

    def test_dict_empty(self):
        from sfctl.widgets import format_value

        assert "{...}" in format_value({})

    def test_dict_short(self):
        from sfctl.widgets import format_value

        assert "a" in format_value({"a": 1})

    def test_dict_long(self):
        from sfctl.widgets import format_value

        assert "+6" in format_value({str(i): i for i in range(10)})

    def test_other_type(self):
        from sfctl.widgets import format_value

        assert "object" in format_value(object())


class TestTryParse:
    def test_json_dict(self):
        from sfctl.widgets import try_parse

        assert try_parse('{"a":1}') == {"a": 1}

    def test_json_list(self):
        from sfctl.widgets import try_parse

        assert try_parse("[1,2]") == [1, 2]

    def test_non_json(self):
        from sfctl.widgets import try_parse

        assert try_parse("not json") == "not json"

    def test_json_scalar(self):
        from sfctl.widgets import try_parse

        assert try_parse("42") == "42"

    def test_non_string(self):
        from sfctl.widgets import try_parse

        assert try_parse(123) == 123


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

    def test_variant_key_skipped(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"input": {"variant": "ReadFile", "target_file": "a.py"}}
        widgets = trace_event_detail_widgets(ev)
        texts = [str(w._Static__content) for w in widgets]
        combined = "\n".join(texts)
        assert "target_file" in combined
        assert "variant" not in combined.lower().split("target_file")[0]

    def test_none_values_skipped(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"input": {"pattern": "foo", "glob": None, "type": None}}
        widgets = trace_event_detail_widgets(ev)
        texts = [str(w._Static__content) for w in widgets]
        combined = "\n".join(texts)
        assert "pattern" in combined
        assert "null" not in combined

    def test_multiline_output_preserved(self):
        from sfctl.widgets import trace_event_detail_widgets
        from sfctl.models import TraceEvent

        ev = TraceEvent(name="run", output="line1\nline2\nline3")
        widgets = trace_event_detail_widgets(ev)
        texts = [str(w._Static__content) for w in widgets]
        combined = "\n".join(texts)
        assert "line1" in combined
        assert "line2" in combined

    def test_byte_list_decoded(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"output": {"type": "Grep", "stdout": list(b"hello world"), "stderr": []}}
        widgets = trace_event_detail_widgets(ev)
        texts = [str(w._Static__content) for w in widgets]
        combined = "\n".join(texts)
        assert "hello world" in combined

    def test_nested_output_unwrapped(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"output": {"type": "ReadFile", "FileContent": {"content": "file data"}}}
        widgets = trace_event_detail_widgets(ev)
        texts = [str(w._Static__content) for w in widgets]
        combined = "\n".join(texts)
        assert "file data" in combined

    def test_output_for_prompt_preferred(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"output": {
            "type": "Bash", "output": list(b"/usr/bin/ndisasm\n"),
            "output_for_prompt": "exit: 0\n/usr/bin/ndisasm\n",
            "exit_code": 0, "command": "which ndisasm",
        }}
        widgets = trace_event_detail_widgets(ev)
        texts = [str(w._Static__content) for w in widgets]
        combined = "\n".join(texts)
        assert "exit: 0" in combined
        assert "output_for_prompt" not in combined

    def test_false_booleans_skipped_in_input(self):
        from sfctl.widgets import trace_event_detail_widgets

        ev = {"input": {"command": "ls", "is_background": False, "variant": "Bash"}}
        widgets = trace_event_detail_widgets(ev)
        texts = [str(w._Static__content) for w in widgets]
        combined = "\n".join(texts)
        assert "command" in combined
        assert "is_background" not in combined
        assert "variant" not in combined.lower().split("command")[0]


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
        async with app.run_test(size=(80, 24)):
            dd = app.query_one(DiffDisplay)
            assert dd.diff_text == diff_text
            assert dd.model_name == "A"
            assert dd.filename == "test.py"
            assert len(dd._diff_lines) == 4

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
    async def test_syntax_language_set(self):
        from textual.app import App, ComposeResult

        from sfctl.widgets import DiffDisplay

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffDisplay("@@ -1 +1 @@\n-x\n+y", "A", "f.py")

        app = TestApp()
        async with app.run_test(size=(80, 24)):
            dd = app.query_one(DiffDisplay)
            assert dd.language == "python"
