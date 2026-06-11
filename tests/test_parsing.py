"""Tests for parsing, text helpers, and trace formatting."""

from __future__ import annotations

TASK_ID = "t-D2F1God7q8QRa48qVJpO1"


class TestParseContent:
    def test_task_id(self, parsed):
        assert parsed.task_id == TASK_ID

    def test_repository(self, parsed):
        assert parsed.repository == "transformers"

    def test_model_count_and_names(self, parsed):
        assert len(parsed.models) == 3
        assert [m.name for m in parsed.models] == ["Model A", "Model B", "Model C"]

    def test_model_a_diff(self, parsed):
        m = parsed.models[0]
        assert m.diff.startswith("diff --git")
        assert "benchmark_runner.py" in m.diff
        assert len(m.file_diffs) > 10

    def test_model_b_diff(self, parsed):
        m = parsed.models[1]
        assert "modeling_utils.py" in m.file_diffs[0].filename

    def test_model_c_single_file(self, parsed):
        m = parsed.models[2]
        assert m.diff.startswith("diff --git")
        assert len(m.file_diffs) == 1

    def test_trace_summaries_exist(self, parsed):
        for m in parsed.models:
            assert m.trace_summary is not None
            assert len(m.trace_summary) > 100

    def test_tool_events_have_expected_keys(self, parsed):
        for m in parsed.models:
            assert len(m.tool_events) > 0
            for ev in m.tool_events:
                assert "name" in ev

    def test_messages_parsed(self, parsed):
        for m in parsed.models:
            assert len(m.messages) > 0
            for msg in m.messages:
                assert "role" in msg


class TestExtractFileDiffs:
    def test_split(self):
        from sftui.parsing import extract_file_diffs

        diff = (
            "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n"
            "@@ -1,3 +1,3 @@\n-old\n+new\n"
            "diff --git a/bar.py b/bar.py\n--- a/bar.py\n+++ b/bar.py\n"
            "@@ -1 +1 @@\n-x\n+y\n"
        )
        files = extract_file_diffs(diff)
        assert len(files) == 2
        assert files[0].filename == "foo.py"
        assert files[1].filename == "bar.py"
        assert "+new" in files[0].diff

    def test_empty(self):
        from sftui.parsing import extract_file_diffs

        assert extract_file_diffs("") == []
        assert extract_file_diffs("   ") == []

    def test_short_diff_git_header(self):
        from sftui.parsing import extract_file_diffs

        diff = "diff --git a/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x\n+y\n"
        files = extract_file_diffs(diff)
        assert len(files) == 1
        assert files[0].filename == "foo.py"

    def test_plus_plus_plus_fallback(self):
        from sftui.parsing import extract_file_diffs

        diff = "+++ b/hello.py\n@@ -1 +1 @@\n-x\n+y\n"
        files = extract_file_diffs(diff)
        assert len(files) == 1
        assert files[0].filename == "hello.py"

    def test_unknown_file_fallback(self):
        from sftui.parsing import extract_file_diffs

        diff = "some random diff content\n@@ -1 +1 @@\n-x\n+y\n"
        files = extract_file_diffs(diff)
        assert len(files) == 1
        assert files[0].filename == "unknown-file"


class TestBuildDiffLineMap:
    def test_hunk_header_mapping(self):
        from sftui.parsing import build_diff_line_map

        diff = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -10,3 +10,3 @@\n context\n-old\n+new\n"
        )
        line_map = build_diff_line_map(diff)
        assert line_map[3] == 10  # hunk header
        assert line_map[4] == 10  # context line
        assert line_map[5] == 11  # deletion
        assert line_map[6] == 11  # addition


class TestDiffLineRef:
    def test_single_line(self):
        from sftui.parsing import diff_line_ref

        diff = "@@ -1,3 +10,3 @@\n context\n-old\n+new\n"
        assert diff_line_ref(diff, 1, 1) == "L10"

    def test_range(self):
        from sftui.parsing import diff_line_ref

        diff = "@@ -1,3 +10,3 @@\n ctx\n-old\n+new\n"
        ref = diff_line_ref(diff, 1, 3)
        assert ref.startswith("L")
        assert "-L" in ref

    def test_reversed_range(self):
        from sftui.parsing import diff_line_ref

        diff = "@@ -1,3 +10,3 @@\n ctx\n-old\n+new\n"
        ref = diff_line_ref(diff, 3, 1)
        assert "-L" in ref


class TestBumpHeadings:
    def test_shift_up(self):
        from sftui.parsing import bump_headings

        text = "# Title\n## Section\n### Sub"
        result = bump_headings(text, parent_level=2)
        assert result.startswith("### Title")
        assert "#### Section" in result
        assert "##### Sub" in result

    def test_no_headings(self):
        from sftui.parsing import bump_headings

        assert bump_headings("plain text") == "plain text"

    def test_empty(self):
        from sftui.parsing import bump_headings

        assert bump_headings("") is not None


class TestRankingHelpers:
    def test_get_full_ranking_preference(self, fixture_data):
        from sftui.parsing import get_full_ranking

        ranking = get_full_ranking(fixture_data["history"][0], "preference_ranking")
        assert ranking.index("C") < ranking.index("A") < ranking.index("B")

    def test_get_full_ranking_response_quality(self, fixture_data):
        from sftui.parsing import get_full_ranking

        ranking = get_full_ranking(fixture_data["history"][0], "response_quality_ranking")
        assert "C" in ranking and "A" in ranking and "B" in ranking

    def test_get_full_ranking_missing_key(self, fixture_data):
        from sftui.parsing import get_full_ranking

        assert get_full_ranking(fixture_data["history"][0], "nonexistent_ranking") == ""

    def test_get_full_ranking_empty_value(self):
        from sftui.parsing import get_full_ranking

        assert get_full_ranking({"r": {"value": []}}, "r") == ""

    def test_get_full_ranking_items_without_id(self):
        from sftui.parsing import get_full_ranking

        assert get_full_ranking({"r": {"value": [{"id": ""}, {"id": None}]}}, "r") == ""

    def test_to_label(self):
        from sftui.parsing import to_label

        assert to_label("model_a") == "A"
        assert to_label("model_b") == "B"
        assert to_label("Model A") == "A"
        assert to_label("") == ""

    def test_rank_color(self):
        from sftui.parsing import rank_color

        assert rank_color(0, 3) == "green"
        assert rank_color(1, 3) == "yellow"
        assert rank_color(2, 3) == "red"
        assert rank_color(0, 1) == "green"


class TestTraceFormatting:
    def test_clean_event_name(self):
        from sftui.parsing import clean_event_name

        assert clean_event_name("__sf_tool_event_thinking__") == "thinking"
        assert clean_event_name("list_dir") == "list_dir"
        assert clean_event_name("") == "unknown"

    def test_group_events(self, parsed):
        from sftui.parsing import group_events

        groups = group_events(parsed.models[0])
        assert "thinking" in groups
        assert "list_dir" in groups
        for m in parsed.models:
            assert len(group_events(m)) >= 2

    def test_format_event_line_normal(self):
        from sftui.parsing import format_event_line

        ev = {"name": "list_dir", "exit_code": "no_error", "wall_time": 0}
        line = format_event_line(ev)
        assert "list_dir" in line
        assert "no_error" not in line

    def test_format_event_line_error(self):
        from sftui.parsing import format_event_line

        ev = {"name": "run_terminal_cmd", "exit_code": "error", "wall_time": 500}
        line = format_event_line(ev)
        assert "error" in line
        assert "500ms" in line

    def test_trace_type_color_cycles(self):
        from sftui.parsing import trace_type_color

        assert trace_type_color(0) == trace_type_color(10)
        assert len({trace_type_color(i) for i in range(10)}) > 1


class TestFeedbackDedup:
    def test_fixture_dedupes(self, fixture_data):
        from sftui.parsing import dedupe_feedback

        unique = dedupe_feedback(fixture_data["history"], fixture_data["feedback"])
        assert len(unique) == 1
        assert unique[0]["score"] == 7

    def test_empty(self):
        from sftui.parsing import dedupe_feedback

        assert dedupe_feedback([], {}) == []

    def test_no_duplicates(self):
        from sftui.parsing import dedupe_feedback

        history = [
            {"feedback": {"entries": [{"timestamp": 1, "msg": "a"}]}},
            {"feedback": {"entries": [{"timestamp": 2, "msg": "b"}]}},
        ]
        unique = dedupe_feedback(history, {"entries": []})
        assert len(unique) == 2


class TestParseJsonField:
    def test_valid_json(self):
        from sftui.parsing import _parse_json_field

        assert _parse_json_field("[1,2,3]") == [1, 2, 3]

    def test_none(self):
        from sftui.parsing import _parse_json_field

        assert _parse_json_field(None) == []

    def test_empty_string(self):
        from sftui.parsing import _parse_json_field

        assert _parse_json_field("") == []

    def test_invalid_json(self):
        from sftui.parsing import _parse_json_field

        assert _parse_json_field("not json") == []
