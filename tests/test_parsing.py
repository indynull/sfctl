"""Tests for parsing, text helpers, and trace formatting."""

from __future__ import annotations

import json

TASK_ID = "t-EXAMPLE001"


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
                assert hasattr(ev, "name")

    def test_messages_parsed(self, parsed):
        for m in parsed.models:
            assert len(m.messages) > 0
            for msg in m.messages:
                assert "role" in msg


class TestExtractFileDiffs:
    def test_split(self):
        from sfctl.diff import extract_file_diffs

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
        from sfctl.diff import extract_file_diffs

        assert extract_file_diffs("") == []
        assert extract_file_diffs("   ") == []

    def test_short_diff_git_header(self):
        from sfctl.diff import extract_file_diffs

        diff = "diff --git a/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x\n+y\n"
        files = extract_file_diffs(diff)
        assert len(files) == 1
        assert files[0].filename == "foo.py"

    def test_plus_plus_plus_fallback(self):
        from sfctl.diff import extract_file_diffs

        diff = "+++ b/hello.py\n@@ -1 +1 @@\n-x\n+y\n"
        files = extract_file_diffs(diff)
        assert len(files) == 1
        assert files[0].filename == "hello.py"

    def test_unknown_file_fallback(self):
        from sfctl.diff import extract_file_diffs

        diff = "some random diff content\n@@ -1 +1 @@\n-x\n+y\n"
        files = extract_file_diffs(diff)
        assert len(files) == 1
        assert files[0].filename == "unknown-file"


class TestBuildDiffLineMap:
    def test_hunk_header_mapping(self):
        from sfctl.diff import build_diff_line_map

        diff = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -10,3 +10,3 @@\n context\n-old\n+new\n"
        )
        line_map = build_diff_line_map(diff)
        assert 3 not in line_map  # hunk header excluded
        assert line_map[4] == 10  # context line (new-file)
        assert line_map[5] == 11  # deletion (old-file)
        assert line_map[6] == 11  # addition (new-file)


class TestDiffLineRef:
    def test_single_line(self):
        from sfctl.diff import diff_line_ref

        diff = "@@ -1,3 +10,3 @@\n context\n-old\n+new\n"
        assert diff_line_ref(diff, 1, 1) == "L10"

    def test_range(self):
        from sfctl.diff import diff_line_ref

        diff = "@@ -1,3 +10,3 @@\n ctx\n-old\n+new\n"
        ref = diff_line_ref(diff, 1, 3)
        assert ref.startswith("L")
        assert "-L" in ref

    def test_reversed_range(self):
        from sfctl.diff import diff_line_ref

        diff = "@@ -1,3 +10,3 @@\n ctx\n-old\n+new\n"
        ref = diff_line_ref(diff, 3, 1)
        assert "-L" in ref


class TestBuildHighlightedSides:
    def test_splits_old_new(self):
        from sfctl.diff import build_highlighted_sides, parse_diff_lines

        diff = "@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n ctx2"
        dl = parse_diff_lines(diff)
        new_lines, new_map, old_lines, old_map = build_highlighted_sides(dl)
        # New side: hunk(blank), ctx, +new, ctx2
        assert "new" in new_lines
        assert "old" not in new_lines
        # Old side: hunk(blank), ctx, -old, ctx2
        assert "old" in old_lines
        assert "new" not in old_lines

    def test_orphaned_triple_quote_balanced(self):
        from sfctl.diff import build_highlighted_sides, parse_diff_lines

        diff = '@@ -1,3 +1,3 @@\n ctx\n """\n-old\n+new'
        dl = parse_diff_lines(diff)
        new_lines, new_map, _, _ = build_highlighted_sides(dl)
        tq_count = sum(line.count('"""') for line in new_lines)
        assert tq_count % 2 == 0, "triple-quotes should be balanced"

    def test_synthetic_lines_mapped_as_minus_one(self):
        from sfctl.diff import build_highlighted_sides, parse_diff_lines

        diff = '@@ -1,3 +1,3 @@\n """\n-old\n+new'
        dl = parse_diff_lines(diff)
        new_lines, new_map, _, _ = build_highlighted_sides(dl)
        for i, m in enumerate(new_map):
            if m == -1:
                assert new_lines[i] == '"""'


class TestBumpHeadings:
    def test_shift_up(self):
        from sfctl.formatting import bump_headings

        text = "# Title\n## Section\n### Sub"
        result = bump_headings(text, parent_level=2)
        assert result.startswith("### Title")
        assert "#### Section" in result
        assert "##### Sub" in result

    def test_no_headings(self):
        from sfctl.formatting import bump_headings

        assert bump_headings("plain text") == "plain text"

    def test_empty(self):
        from sfctl.formatting import bump_headings

        assert bump_headings("") is not None


class TestRankingHelpers:
    def test_get_full_ranking_preference(self, fixture_data):
        from sfctl.history import get_full_ranking

        ranking = get_full_ranking(fixture_data["history"][0], "preference_ranking")
        assert ranking.index("C") < ranking.index("A") < ranking.index("B")

    def test_get_full_ranking_response_quality(self, fixture_data):
        from sfctl.history import get_full_ranking

        ranking = get_full_ranking(fixture_data["history"][0], "response_quality_ranking")
        assert "C" in ranking and "A" in ranking and "B" in ranking

    def test_get_full_ranking_missing_key(self, fixture_data):
        from sfctl.history import get_full_ranking

        assert get_full_ranking(fixture_data["history"][0], "nonexistent_ranking") == ""

    def test_get_full_ranking_empty_value(self):
        from sfctl.history import get_full_ranking

        assert get_full_ranking({"r": {"value": []}}, "r") == ""

    def test_get_full_ranking_items_without_id(self):
        from sfctl.history import get_full_ranking

        assert get_full_ranking({"r": {"value": [{"id": ""}, {"id": None}]}}, "r") == ""

    def test_to_label(self):
        from sfctl.history import to_label

        assert to_label("model_a") == "A"
        assert to_label("model_b") == "B"
        assert to_label("Model A") == "A"
        assert to_label("") == ""

    def test_rank_color(self):
        from sfctl.formatting import rank_color

        assert rank_color(0, 3) == "green"
        assert rank_color(1, 3) == "yellow"
        assert rank_color(2, 3) == "red"
        assert rank_color(0, 1) == "green"


class TestTraceFormatting:
    def test_clean_event_name(self):
        from sfctl.formatting import clean_event_name

        assert clean_event_name("__sf_tool_event_thinking__") == "thinking"
        assert clean_event_name("list_dir") == "list_dir"
        assert clean_event_name("") == "unknown"

    def test_group_events(self, parsed):
        from sfctl.formatting import group_events

        groups = group_events(parsed.models[0].tool_events)
        assert "thinking" in groups
        assert "list_dir" in groups
        for m in parsed.models:
            assert len(group_events(m.tool_events)) >= 2

    def test_format_event_line_normal(self):
        from sfctl.formatting import format_event_line

        ev = {"name": "list_dir", "exit_code": "no_error", "wall_time": 0}
        line = format_event_line(ev)
        assert "list_dir" in line
        assert "no_error" not in line

    def test_format_event_line_error(self):
        from sfctl.formatting import format_event_line

        ev = {"name": "run_terminal_cmd", "exit_code": "error", "wall_time": 500}
        line = format_event_line(ev)
        assert "error" in line
        assert "500ms" in line

    def test_trace_type_color_cycles(self):
        from sfctl.formatting import trace_type_color

        assert trace_type_color(0) == trace_type_color(10)
        assert len({trace_type_color(i) for i in range(10)}) > 1


class TestToolNameFromInput:
    def test_pascal_case_variant(self):
        from sfctl.diff import tool_name_from_input

        assert tool_name_from_input('{"variant":"ReadFile"}') == "read_file"
        assert tool_name_from_input('{"variant":"SearchReplace"}') == "search_replace"
        assert tool_name_from_input('{"variant":"Grep"}') == "grep"
        assert tool_name_from_input('{"variant":"ListDir"}') == "list_dir"
        assert tool_name_from_input('{"variant":"Bash"}') == "bash"
        assert tool_name_from_input('{"variant":"TodoWrite"}') == "todo_write"
        assert tool_name_from_input('{"variant":"UpdateGoal"}') == "update_goal"

    def test_dict_input(self):
        from sfctl.diff import tool_name_from_input

        assert tool_name_from_input({"variant": "ReadFile"}) == "read_file"

    def test_empty_input(self):
        from sfctl.diff import tool_name_from_input

        assert tool_name_from_input("") == ""
        assert tool_name_from_input("{}") == ""
        assert tool_name_from_input({}) == ""


class TestParseJsonField:
    def test_valid_json(self):
        from sfctl.diff import parse_json_field

        assert parse_json_field("[1,2,3]") == [1, 2, 3]

    def test_none(self):
        from sfctl.diff import parse_json_field

        assert parse_json_field(None) == []

    def test_empty_string(self):
        from sfctl.diff import parse_json_field

        assert parse_json_field("") == []

    def test_invalid_json(self):
        from sfctl.diff import parse_json_field

        assert parse_json_field("not json") == []


class TestSfValue:
    def test_string_value(self):
        from sfctl.proposal import sf_value

        assert sf_value({"_sf_rich": True, "value": "hello"}) == "hello"

    def test_list_value(self):
        from sfctl.proposal import sf_value

        assert sf_value({"_sf_rich": True, "value": ["a", "b"]}) == "a, b"

    def test_none_field(self):
        from sfctl.proposal import sf_value

        assert sf_value(None) == ""

    def test_empty_dict(self):
        from sfctl.proposal import sf_value

        assert sf_value({}) == ""

    def test_missing_value(self):
        from sfctl.proposal import sf_value

        assert sf_value({"_sf_rich": True}) == ""


class TestExtractRubrics:
    def test_basic(self):
        from sfctl.proposal import extract_rubrics

        rubrics = {
            "items": [
                {"nestedAnnotations": {"rubric": {"_sf_rich": True, "value": "Rubric one"}}},
                {"nestedAnnotations": {"rubric": {"_sf_rich": True, "value": "Rubric two"}}},
            ]
        }
        result = extract_rubrics(rubrics)
        assert result == ["Rubric one", "Rubric two"]

    def test_empty(self):
        from sfctl.proposal import extract_rubrics

        assert extract_rubrics(None) == []
        assert extract_rubrics({}) == []
        assert extract_rubrics({"items": []}) == []


class TestParseProposal:
    def test_basic_fields(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert p.repo_url == "https://github.com/example/repo"
        assert p.repo_description == "A test repo for testing"
        assert p.difficulty == "Medium difficulty task"
        assert p.domain == "other"
        assert p.duration == "1h-2h"
        assert p.solved == "partial"

    def test_rubrics(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert len(p.rubrics) == 4
        assert p.rubrics[0] == "Rubric one"
        assert p.rubrics[-1] == "Rubric four"

    def test_prompt(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert p.prompt == "Implement feature X"

    def test_code_patch(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert "diff --git" in p.code_patch
        assert len(p.file_diffs) == 1
        assert p.file_diffs[0].filename == "foo.py"

    def test_bash_history(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert len(p.bash_history) == 2
        assert p.bash_history[1]["command"] == "uv run pytest"

    def test_issues(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert p.issues == "Model failed on edge case"
        assert len(p.issue_comments) == 1

    def test_model_id(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert p.model_id == "test-model-v1"

    def test_trace_ref(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert p.trace_ref == "coding-question/worker/session/trace.json"

    def test_trace_data_real_format(self, proposal_data):
        from sfctl.proposal import parse_proposal

        trace = {
            "trace": [
                {"role": "user", "content": [{"type": "text", "text": "Do the thing"}]},
                {"role": "tool_call", "timestamp": 1000, "toolCallId": "tc1",
                 "title": "List `.`", "status": "completed",
                 "rawInput": '{"variant":"ListDir"}', "rawOutput": "file1\nfile2"},
                {"role": "assistant_thinking", "content": "Let me think...", "timestamp": 1500},
                {"role": "tool_call", "timestamp": 2000, "toolCallId": "tc2",
                 "title": "Read `foo.py`", "status": "completed",
                 "rawInput": '{"variant":"ReadFile"}', "rawOutput": "contents"},
                {"role": "assistant", "content": "Short note"},
                {"role": "assistant", "content": "x" * 300},
            ]
        }
        p = parse_proposal(proposal_data["history"], trace)
        assert len(p.tool_events) == 3  # 2 tool_calls + 1 thinking
        assert p.tool_events[0].name == "list_dir"
        assert p.tool_events[0].title == "List `.`"
        assert p.tool_events[1].name == "thinking"
        assert p.tool_events[2].name == "read_file"
        assert not any(e.name == "assistant" for e in p.tool_events)
        assert p.trace_summary == "x" * 300
        assert len(p.messages) == 3

    def test_trace_data_legacy_format(self, proposal_data):
        from sfctl.proposal import parse_proposal

        trace = {
            "trace": "Summary of work done",
            "messages": json.dumps([
                {"role": "tool_call", "title": "list_dir", "timestamp": 1,
                 "rawInput": {"variant": "ListDir", "target_directory": "."},
                 "rawOutput": {"text": "file1\nfile2"}, "status": "completed"},
                {"role": "assistant", "content": "done"},
            ]),
        }
        p = parse_proposal(proposal_data["history"], trace)
        assert p.trace_summary == "Summary of work done"
        assert len(p.tool_events) == 1
        assert p.tool_events[0].name == "list_dir"
        assert len(p.messages) == 1

    def test_no_trace(self, proposal_data):
        from sfctl.proposal import parse_proposal

        p = parse_proposal(proposal_data["history"])
        assert p.trace_summary == ""
        assert p.tool_events == []
        assert p.messages == []

    def test_empty_history(self):
        from sfctl.proposal import parse_proposal

        p = parse_proposal([])
        assert p.repo_url == ""
        assert p.rubrics == []


class TestProposalRubricChanges:
    def test_additions(self):
        from sfctl.proposal import proposal_rubric_changes

        prev = ["A", "B"]
        curr = ["A", "B", "C"]
        changes = proposal_rubric_changes(prev, curr)
        assert len(changes) == 1
        assert "C" in changes[0]
        assert "[green]" in changes[0]

    def test_removals(self):
        from sfctl.proposal import proposal_rubric_changes

        prev = ["A", "B", "C"]
        curr = ["A", "C"]
        changes = proposal_rubric_changes(prev, curr)
        assert len(changes) == 1
        assert "B" in changes[0]
        assert "[red]" in changes[0]

    def test_no_changes(self):
        from sfctl.proposal import proposal_rubric_changes

        assert proposal_rubric_changes(["A", "B"], ["A", "B"]) == []


class TestProposalRunElapsed:
    def test_model_run_change_detected(self):
        from sfctl.proposal import has_proposal_changes, proposal_all_changes

        prev = {"coding_question": {"rollouts": {"A": {
            "traceRef": "trace/run1.json",
            "finalSessionSummary": {
                "created_at": "2026-06-11T17:00:00Z",
                "updated_at": "2026-06-11T17:20:00Z",
            },
        }}}}
        curr = {"coding_question": {"rollouts": {"A": {
            "traceRef": "trace/run2.json",
            "finalSessionSummary": {
                "created_at": "2026-06-12T10:00:00Z",
                "updated_at": "2026-06-12T10:40:00Z",
            },
        }}}}
        assert has_proposal_changes(prev, curr)
        changes = proposal_all_changes(prev, curr)
        combined = "\n".join(changes)
        assert "Model run" in combined
        assert "20.0m" in combined
        assert "40.0m" in combined

    def test_same_trace_ref_no_run_change(self):
        from sfctl.proposal import proposal_all_changes

        entry = {"coding_question": {"rollouts": {"A": {
            "traceRef": "trace/same.json",
            "finalSessionSummary": {
                "created_at": "2026-06-11T17:00:00Z",
                "updated_at": "2026-06-11T17:20:00Z",
            },
        }}}}
        changes = proposal_all_changes(entry, entry)
        assert not any("Model run" in c for c in changes)
