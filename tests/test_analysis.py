"""Analysis pipeline tests."""

from __future__ import annotations

import json
from pathlib import Path

from sfctl.analysis import AnalysisContext, Rule
from sfctl.task_types import TaskType

SNAPSHOTS = Path(__file__).parent.parent / "snapshots"


def _ranking_ctx(
    models: list[dict] | None = None,
    history: list[dict] | None = None,
    action_history: list[dict] | None = None,
    session_ms: int = 0,
    total_diff_lines: int = 0,
    justification: str | None = None,
) -> AnalysisContext:
    hist = history or []
    if justification is None:
        justification = (hist[-1].get("justification", {}) or {}).get("value", "") if hist else ""
    return AnalysisContext(
        task_type=TaskType.CODE_REVIEW,
        history=hist,
        entry=hist[-1] if hist else {},
        action_history=action_history or [],
        justification=justification,
        model_traces=models or [],
        session_ms=session_ms,
        total_diff_lines=total_diff_lines,
    )


def _proposal_ctx(
    entry: dict | None = None,
    action_history: list[dict] | None = None,
    elapsed_ms: int | None = None,
    solved: str = "",
    bash_commands: list[tuple[str, str]] | None = None,
    web_search_queries: list[str] | None = None,
    tool_call_count: int = 0,
    reasoning_chars: int = 0,
) -> AnalysisContext:
    e = entry or {}
    return AnalysisContext(
        task_type=TaskType.PROJECT_PROPOSAL,
        history=[e] if e else [],
        entry=e,
        action_history=action_history or [],
        justification="",
        elapsed_ms=elapsed_ms,
        solved=solved,
        bash_commands=bash_commands or [],
        web_search_queries=web_search_queries or [],
        tool_call_count=tool_call_count,
        reasoning_chars=reasoning_chars,
    )


def _get_rule(name: str) -> Rule:
    from sfctl.analysis import RULES
    return next(r for r in RULES if r.name == name)


def _check(rule_name: str, ctx: AnalysisContext):
    result = _get_rule(rule_name).check(ctx)
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


class TestFactChecks:
    def test_broken_trace_empty_messages(self):
        ctx = _ranking_ctx(models=[
            {"title": "Model A", "trace": {"messages": ""}, "diff": {"codeDiff": "x"}},
        ])
        signals = _check("broken_trace", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "quarantine"
        assert signals[0].name == "broken_trace"

    def test_broken_trace_invalid_json(self):
        ctx = _ranking_ctx(models=[
            {"title": "Model A", "trace": {"messages": "not json"}, "diff": {"codeDiff": "x"}},
        ])
        signals = _check("broken_trace", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "quarantine"

    def test_broken_trace_empty_list(self):
        ctx = _ranking_ctx(models=[
            {"title": "Model A", "trace": {"messages": "[]"}, "diff": {"codeDiff": "x"}},
        ])
        signals = _check("broken_trace", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "quarantine"

    def test_valid_trace_passes(self):
        ctx = _ranking_ctx(models=[
            {"title": "Model A", "trace": {"messages": '[{"role": "assistant"}]'}, "diff": {"codeDiff": "x"}},
        ])
        signals = _check("broken_trace", ctx)
        assert len(signals) == 0

    def test_truncated_trace_detected(self):
        ctx = _ranking_ctx(models=[
            {"title": "Model A", "trace": {"messages": '[{"role": "tool_call"}]'}, "diff": {"codeDiff": "x"}},
        ])
        signals = _check("broken_trace", ctx)
        assert len(signals) == 1
        assert signals[0].name == "truncated_trace"
        assert signals[0].severity == "quarantine"

    def test_completed_tool_call_not_truncated(self):
        ctx = _ranking_ctx(models=[
            {"title": "Model A", "trace": {"messages": '[{"role": "tool_call", "status": "completed"}]'}, "diff": {"codeDiff": "x"}},
        ])
        signals = _check("broken_trace", ctx)
        assert len(signals) == 0

    def test_empty_diff_detected(self):
        ctx = _ranking_ctx(models=[
            {"title": "Model A", "trace": {"messages": "[{}]"}, "diff": {"codeDiff": ""}},
            {"title": "Model B", "trace": {"messages": "[{}]"}, "diff": {"codeDiff": "diff --git a/x"}},
        ])
        signals = _check("empty_diff", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "fail"
        assert "Model A" in signals[0].description

    def test_identical_diffs_detected(self):
        same_diff = "diff --git a/x\n+hello"
        ctx = _ranking_ctx(models=[
            {"title": "Model A", "trace": {"messages": "[{}]"}, "diff": {"codeDiff": same_diff}},
            {"title": "Model B", "trace": {"messages": "[{}]"}, "diff": {"codeDiff": same_diff}},
            {"title": "Model C", "trace": {"messages": "[{}]"}, "diff": {"codeDiff": "diff --git a/y\n+other"}},
        ])
        signals = _check("identical_diffs", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"
        assert "Model A" in signals[0].description
        assert "Model B" in signals[0].description

    def test_no_diff_file_refs_detected(self):
        ctx = _ranking_ctx(
            models=[
                {"title": "Model A", "diff": {"codeDiff": "diff --git a/src/handler.py b/src/handler.py\n+fix"}},
                {"title": "Model B", "diff": {"codeDiff": "diff --git a/src/utils.py b/src/utils.py\n+fix"}},
            ],
            history=[{"justification": {"value": (
                "Model A is better because it handles the edge case properly. "
                "Model B also works but the approach is less clean. " * 5
            )}}],
        )
        signals = _check("no_diff_file_refs", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"

    def test_no_diff_file_refs_passes_when_referenced(self):
        ctx = _ranking_ctx(
            models=[
                {"title": "Model A", "diff": {"codeDiff": "diff --git a/src/handler.py b/src/handler.py\n+fix"}},
            ],
            history=[{"justification": {"value": (
                "Model A correctly fixes the bug in handler.py by checking the "
                "return value before proceeding. " * 5
            )}}],
        )
        signals = _check("no_diff_file_refs", ctx)
        assert len(signals) == 0

    def test_no_diff_file_refs_passes_with_function_ref(self):
        diff = (
            "diff --git a/src/service.py b/src/service.py\n"
            "@@ -10,5 +10,7 @@ def handle_request(\n"
            "+    return response\n"
        )
        ctx = _ranking_ctx(
            models=[{"title": "Model A", "diff": {"codeDiff": diff}}],
            history=[{"justification": {"value": (
                "Model A correctly implements handle_request with proper "
                "error handling and return value checks. " * 5
            )}}],
        )
        signals = _check("no_diff_file_refs", ctx)
        assert len(signals) == 0

    def test_no_diff_file_refs_passes_with_class_ref(self):
        diff = (
            "diff --git a/src/models.py b/src/models.py\n"
            "+class UserRepository:\n"
            "+    def save(self): pass\n"
        )
        ctx = _ranking_ctx(
            models=[{"title": "Model A", "diff": {"codeDiff": diff}}],
            history=[{"justification": {"value": (
                "Model A creates a UserRepository class that properly encapsulates "
                "all database operations and handles errors. " * 5
            )}}],
        )
        signals = _check("no_diff_file_refs", ctx)
        assert len(signals) == 0

    def test_short_session_warns(self):
        ctx = _ranking_ctx(session_ms=60000)
        signals = _check("short_session", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"
        assert signals[0].name == "short_session"

    def test_normal_session_passes(self):
        ctx = _ranking_ctx(session_ms=1800000)
        signals = _check("short_session", ctx)
        assert len(signals) == 0

    def test_suspicious_wps_detected(self):
        long_text = "Model A is better. " * 60
        ctx = _ranking_ctx(
            history=[{"justification": {"value": long_text}}],
            session_ms=10000,
        )
        signals = _check("suspicious_wps", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "fail"
        assert "words/sec" in signals[0].description

    def test_suspicious_wps_normal_speed(self):
        long_text = "Model A is better. " * 60
        ctx = _ranking_ctx(
            history=[{"justification": {"value": long_text}}],
            session_ms=600000,
        )
        signals = _check("suspicious_wps", ctx)
        assert len(signals) == 0

    def test_suspicious_wps_skips_short_justification(self):
        ctx = _ranking_ctx(
            history=[{"justification": {"value": "short text"}}],
            session_ms=5000,
        )
        signals = _check("suspicious_wps", ctx)
        assert len(signals) == 0

    def test_empty_justification_fails(self):
        ctx = _ranking_ctx(history=[{"justification": {"value": ""}}])
        signals = _check("empty_justification", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "fail"

    def test_short_justification_warns(self):
        ctx = _ranking_ctx(history=[{"justification": {"value": "looks good to me"}}])
        signals = _check("short_justification", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"

    def test_justification_with_code_refs_passes(self):
        ctx = _ranking_ctx(history=[{
            "justification": {"value": (
                "Model A correctly implements the fix in `src/handler.py:42`. "
                "The bug was caused by an off-by-one error in the loop at line 55. "
                "Model B also fixes it but introduces a regression in `test_handler.py`. "
                "Model C's approach is overcomplicated. " * 3
            )},
        }])
        signals = _check("no_code_refs", ctx)
        assert len(signals) == 0

    def test_empty_code_patch_warns(self):
        ctx = _proposal_ctx(entry={
            "coding_question": {"rollouts": {"A": {"turns": [{"codePatch": None}]}}},
        })
        signals = _check("empty_code_patch", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"

    def test_empty_code_patch_skips_when_earlier_turn_has_patch(self):
        ctx = _proposal_ctx(entry={
            "coding_question": {"rollouts": {"A": {"turns": [
                {"codePatch": "diff --git a/foo.py\n+hello"},
                {"codePatch": None},
                {"codePatch": None},
            ]}}},
        })
        signals = _check("empty_code_patch", ctx)
        assert len(signals) == 0

    def test_solved_no_patch_fails(self):
        ctx = _proposal_ctx(
            entry={"coding_question": {"rollouts": {"A": {"turns": [{"codePatch": None}]}}}},
            solved="yes",
        )
        signals = _check("empty_code_patch", ctx)
        assert len(signals) == 1
        assert signals[0].name == "solved_no_patch"
        assert signals[0].severity == "fail"

    def test_model_looping_detected(self):
        ctx = _proposal_ctx(bash_commands=[("cd /test && make build 2>&1", "error")] * 10)
        signals = _check("model_looping", ctx)
        assert len(signals) == 1
        assert "10x" in signals[0].description

    def test_low_reasoning_detected(self):
        ctx = _proposal_ctx(tool_call_count=100, reasoning_chars=2000)
        signals = _check("low_reasoning", ctx)
        assert len(signals) == 1
        assert "20 chars" in signals[0].description

    def test_low_reasoning_not_triggered_when_sufficient(self):
        ctx = _proposal_ctx(tool_call_count=100, reasoning_chars=10000)
        signals = _check("low_reasoning", ctx)
        assert len(signals) == 0

    def test_low_reasoning_not_triggered_few_tools(self):
        ctx = _proposal_ctx(tool_call_count=10, reasoning_chars=100)
        signals = _check("low_reasoning", ctx)
        assert len(signals) == 0

    def test_model_looping_not_triggered_under_threshold(self):
        ctx = _proposal_ctx(bash_commands=[("cd /test && make build 2>&1", "")] * 9)
        signals = _check("model_looping", ctx)
        assert len(signals) == 0

    def test_solved_with_issues_warns(self):
        ctx = _proposal_ctx(
            entry={"opus_issues_partial": {"_sf_rich": True, "value": "x" * 60}},
            solved="full",
        )
        signals = _check("solved_with_issues", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"

    def test_fast_solve_under_5min(self):
        ctx = _proposal_ctx(elapsed_ms=180000, solved="yes")
        signals = _check("fast_solve", ctx)
        assert len(signals) == 1

    def test_fast_solve_over_5min_passes(self):
        ctx = _proposal_ctx(elapsed_ms=360000, solved="yes")
        signals = _check("fast_solve", ctx)
        assert len(signals) == 0

    def test_duration_mismatch_detected(self):
        ctx = _proposal_ctx(
            entry={"opus_duration": {"_sf_rich": True, "value": "10-20m"}},
            elapsed_ms=180000,
        )
        signals = _check("duration_mismatch", ctx)
        assert len(signals) == 1

    def test_duration_mismatch_within_range(self):
        ctx = _proposal_ctx(
            entry={"opus_duration": {"_sf_rich": True, "value": "10-20m"}},
            elapsed_ms=900000,
        )
        signals = _check("duration_mismatch", ctx)
        assert len(signals) == 0

    def test_model_installs_deps_pip(self):
        ctx = _proposal_ctx(bash_commands=[("pip install flask pytest 2>&1 | tail -5", "Successfully installed flask")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 1

    def test_model_installs_deps_cargo(self):
        ctx = _proposal_ctx(bash_commands=[("cargo install cargo-expand", "")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 1

    def test_model_installs_deps_npm_named(self):
        ctx = _proposal_ctx(bash_commands=[("npm install lodash", "")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 1

    def test_model_installs_deps_npm_bare_skipped(self):
        ctx = _proposal_ctx(bash_commands=[("cd /testbed && npm install 2>&1 | tail -20", "added 50 packages")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 0

    def test_model_installs_deps_dotnet_add(self):
        ctx = _proposal_ctx(bash_commands=[("dotnet add src/App.csproj package Newtonsoft.Json", "")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 1

    def test_model_installs_deps_dotnet_restore_skipped(self):
        ctx = _proposal_ctx(bash_commands=[("dotnet restore 2>&1 | tail -5", "Restored project")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 0

    def test_model_installs_deps_editable_skipped(self):
        ctx = _proposal_ctx(bash_commands=[("pip install -e ./opentelemetry-api -e ./opentelemetry-sdk", "")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 0

    def test_model_installs_deps_requirements_skipped(self):
        ctx = _proposal_ctx(bash_commands=[("pip install -r requirements.txt", "")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 0

    def test_model_installs_deps_bundle_skipped(self):
        ctx = _proposal_ctx(bash_commands=[("bundle install 2>&1 | tail -30", "Bundle complete!")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 0

    def test_model_installs_deps_apt(self):
        ctx = _proposal_ctx(bash_commands=[("apt-get install -y cmake build-essential 2>&1 | tail -5", "Setting up cmake")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 1

    def test_model_installs_deps_clean(self):
        ctx = _proposal_ctx(bash_commands=[("python -m pytest tests/ -v", "")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 0

    def test_model_installs_deps_already_satisfied(self):
        ctx = _proposal_ctx(bash_commands=[("pip install cython 2>&1 | tail -5", "Requirement already satisfied: cython in /opt/venv")])
        signals = _check("model_installs_deps", ctx)
        assert len(signals) == 0

    def test_web_search_detected(self):
        ctx = _proposal_ctx(web_search_queries=["how to fix segfault in rust"])
        signals = _check("web_search_used", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "fail"
        assert "how to fix segfault" in signals[0].description

    def test_web_search_not_present(self):
        ctx = _proposal_ctx(web_search_queries=[])
        signals = _check("web_search_used", ctx)
        assert len(signals) == 0

    def test_empty_trace_quarantines(self):
        ctx = _proposal_ctx(tool_call_count=0)
        signals = _check("sparse_trace", ctx)
        assert len(signals) == 1
        assert signals[0].name == "empty_trace"
        assert signals[0].severity == "quarantine"

    def test_sparse_trace_fails(self):
        ctx = _proposal_ctx(tool_call_count=10)
        signals = _check("sparse_trace", ctx)
        assert len(signals) == 1
        assert signals[0].name == "sparse_trace"
        assert signals[0].severity == "fail"

    def test_sparse_trace_not_triggered_above_threshold(self):
        ctx = _proposal_ctx(tool_call_count=25)
        signals = _check("sparse_trace", ctx)
        assert len(signals) == 0

    def test_duration_mismatch_severe_fails(self):
        ctx = _proposal_ctx(
            entry={"opus_duration": {"_sf_rich": True, "value": "10-20m"}},
            elapsed_ms=60000,
        )
        signals = _check("duration_mismatch", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "fail"

    def test_duration_mismatch_moderate_warns(self):
        ctx = _proposal_ctx(
            entry={"opus_duration": {"_sf_rich": True, "value": "10-20m"}},
            elapsed_ms=240000,
        )
        signals = _check("duration_mismatch", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"

    def test_rushed_review_detected(self):
        ctx = _ranking_ctx(session_ms=30000, total_diff_lines=500)
        signals = _check("rushed_review", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"
        assert "per 100 lines" in signals[0].description

    def test_rushed_review_normal_pace(self):
        ctx = _ranking_ctx(session_ms=600000, total_diff_lines=500)
        signals = _check("rushed_review", ctx)
        assert len(signals) == 0

    def test_rushed_review_skips_small_diffs(self):
        ctx = _ranking_ctx(session_ms=10000, total_diff_lines=50)
        signals = _check("rushed_review", ctx)
        assert len(signals) == 0

    def test_llm_justification_high_score_fails(self):
        text = (
            "For this prompt, the most important aspects to take into account are "
            "correct implementation of the RoPE math and config support for factor, "
            "original max position and beta knobs. This matters because a wrong RoPE "
            "cache or weak synthetic test can compile cleanly while producing wrong "
            "attention phases during long generation. "
            "Model B is the best because it implemented proper YaRN rotation math in "
            "the Llama rotary path with correct cos sin cache initialization. It is "
            "better than Model A because Model A skipped the cache rebuild when "
            "generation goes past the initial cache length. "
            "As shown by: the rotary embedding class initializes cos_sin correctly. "
            "It is better than Model C because Model C used an incorrect scaling "
            "factor that produces wrong attention scores at long context lengths. "
            "As shown by: the forward pass produces wrong attention phases. "
            "As shown by: the test suite covers fixed rotated qk values plus long "
            "context PPL against an unscaled baseline. "
            "Model A is the worst because it did not handle long context generation "
            "and the cache is never extended beyond the initial size. "
            "The implementation quality and test coverage of Model B demonstrate "
            "a thorough understanding of the rotary position embedding mathematics. "
            "The code changes are minimal and focused on the correct computation. "
            "Model C attempted the right approach but the scaling factor error makes "
            "the implementation incorrect for sequences beyond the original window. "
            "The ranking is clear from the mathematical correctness of each solution. "
        )
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "fail"
        assert "Score" in signals[0].description

    def test_llm_justification_moderate_score_warns(self):
        text = (
            "For this prompt, the most important aspects are correct handling of edge cases "
            "and proper error propagation through the call chain. This matters because "
            "silent error swallowing leads to data corruption in production workloads. "
            "Model B is the best because it covers all scenarios properly and handles "
            "the edge case where the input stream is empty. "
            "It is better than Model A because the approach is more robust overall and "
            "it does not silently swallow errors in the deserialization path. "
            "It is better than Model C because Model C missed the boundary condition "
            "when the buffer overflows during concurrent writes. "
            "As shown by: the test_concurrent_write test in test_buffer.py. "
            "As shown by: the error handling in stream_processor.py line 142. "
            "Model A is the worst because it silently drops errors and has no test "
            "coverage for the concurrent write scenario. "
            "Overall the ranking is clear from the code quality differences. "
        ) + "The implementation handles all specified requirements correctly. " * 5
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"

    def test_llm_justification_human_passes(self):
        text = (
            "Model A is better imo - it's got the cleanest approach and doesn't break "
            "existing tests. B's solution kinda works but I think the error handling "
            "is flaky. C's diff is huge and honestly I'm not sure why they rewrote "
            "half the module. The handler.py changes in A are minimal and correct. "
            "I'd probably rank it A > B > C. B isn't terrible but it's definitely "
            "not as clean. C seems overcomplicated for what's needed here. "
        ) * 3
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 0

    def test_llm_justification_skips_short(self):
        ctx = _ranking_ctx(justification="Model A is best. Short review.")
        signals = _check("llm_justification", ctx)
        assert len(signals) == 0

    def test_llm_justification_formulaic_opener(self):
        text = (
            "For this prompt, the most important aspects to take into account are "
            "correctness of the implementation and test coverage. "
            "Model A is the best because it handled all edge cases properly. "
            "Model B is the worst because it missed the boundary condition entirely. "
            "The ranking is clear from the code quality differences observed "
            "across the three implementations submitted for this evaluation. "
        ) * 6
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 1
        assert "formulaic opener" in signals[0].description

    def test_llm_justification_best_worst_template(self):
        text = (
            "The key thing here is whether the model correctly handles retries. "
            "Model B is the best because it covers all failure modes properly. "
            "Model A is second best because the retry logic works but is fragile. "
            "Model C is the worst because it silently swallows errors throughout. "
            "The differences are stark when you compare the error handling paths "
            "and how each model approaches the retry backoff strategy. "
        ) * 6
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 1
        assert '"Model X is the best/worst because"' in signals[0].description

    def test_llm_justification_human_noise_reduces_score(self):
        text = (
            "For this prompt, the most important aspects to take into account are "
            "correctness and test coverage... honestly the differences are stark. "
            "Model A is the best because it doesnt break anything and handles "
            "the edge cases. But the retry logic is kinda fragile tho. "
            "Model B is the worst because it didnt even bother with error handling! "
            "And the tests are missing entirely! Also the code style is inconsistent... "
            "Model C is second best because it shouldnt have rewritten half the module. "
            "So overall A is clearly the winner here. "
        ) * 4
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 0

    def test_llm_justification_grammar_errors_reduce_score(self):
        text = (
            "For this prompt, the most important aspects are correctness "
            "and test coverage. Model B is the best because the model a do "
            "not handle the edge case and it don't run the tests properly. "
            "It is better then Model A because the implementation of A is "
            "incomplete. Model C is the worst because it don't even compile. "
            "As shown by: the test suite fails on all three edge cases. "
            "As shown by: the build log shows compilation errors throughout. "
            "As shown by: the retry logic has rather then correct backoff. "
        ) * 4
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 0 or signals[0].severity == "warn"

    def test_llm_justification_noise_needs_multiple_dims(self):
        text = (
            "For this prompt, the most important aspects to take into account are "
            "correctness of the implementation and test coverage. "
            "Model A is the best because it handled all edge cases properly. "
            "Model B is the worst because it missed the boundary condition entirely. "
            "The ranking is clear from the code quality differences observed "
            "across the three implementations submitted for this evaluation. "
        ) * 6
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 1
        assert "human noise" not in signals[0].description

    def test_llm_justification_model_because_density(self):
        text = (
            "Model A because it has the correct implementation of the rotation math. "
            "Model B because it skipped the cache rebuild step entirely. "
            "Model C because the scaling factor is wrong for long sequences. "
            "Model A because the test suite validates rotated qk values properly. "
            "Model B because it attempted the right approach but made an error. "
            "The ranking is clear from the mathematical correctness of each solution. "
            "As shown by: the test suite validates rotated qk values properly. "
            "As shown by: the scaling factor is within range for all tested inputs. "
            "As shown by: the cache rebuild triggers on the correct boundary condition. "
        ) * 4
        ctx = _ranking_ctx(justification=text)
        signals = _check("llm_justification", ctx)
        assert len(signals) == 1
        assert '"Model X because"' in signals[0].description


class TestStructuralSignals:
    def test_ranking_flip_same_level(self):
        ctx = _ranking_ctx(history=[
            {
                "preference_ranking": {"value": [{"id": "model_a"}, {"id": "model_b"}]},
                "response_quality_ranking": {"value": []},
                "code_quality_ranking": {"value": []},
                "reviewLevel": 1,
            },
            {
                "preference_ranking": {"value": [{"id": "model_b"}, {"id": "model_a"}]},
                "response_quality_ranking": {"value": []},
                "code_quality_ranking": {"value": []},
                "reviewLevel": 1,
            },
        ])
        signals = _check("ranking_flip", ctx)
        assert len(signals) == 1
        assert signals[0].severity == "warn"

    def test_ranking_change_different_level_not_flagged(self):
        ctx = _ranking_ctx(history=[
            {
                "preference_ranking": {"value": [{"id": "model_a"}, {"id": "model_b"}]},
                "response_quality_ranking": {"value": []},
                "code_quality_ranking": {"value": []},
                "reviewLevel": 0,
            },
            {
                "preference_ranking": {"value": [{"id": "model_b"}, {"id": "model_a"}]},
                "response_quality_ranking": {"value": []},
                "code_quality_ranking": {"value": []},
                "reviewLevel": 0.5,
            },
        ])
        signals = _check("ranking_flip", ctx)
        assert len(signals) == 0


class TestRecommendation:
    def test_no_signal_on_empty(self):
        from sfctl.analysis import _recommend

        assert _recommend([]) == "no_signal"

    def test_send_back_on_fail(self):
        from sfctl.analysis import _recommend
        from sfctl.models import QualitySignal

        signals = [
            QualitySignal(name="empty_justification", severity="fail"),
        ]
        assert _recommend(signals) == "send_back"

    def test_quarantine_on_broken_trace(self):
        from sfctl.analysis import _recommend
        from sfctl.models import QualitySignal

        signals = [
            QualitySignal(name="broken_trace", severity="quarantine", description="Model A: empty"),
        ]
        assert _recommend(signals) == "quarantine"

    def test_quarantine_overrides_fail(self):
        from sfctl.analysis import _recommend
        from sfctl.models import QualitySignal

        signals = [
            QualitySignal(name="broken_trace", severity="quarantine", description="Model A: empty"),
            QualitySignal(name="empty_justification", severity="fail"),
        ]
        assert _recommend(signals) == "quarantine"

    def test_send_back_on_three_warns(self):
        from sfctl.analysis import _recommend
        from sfctl.models import QualitySignal

        signals = [
            QualitySignal(name="no_diff_file_refs", severity="warn"),
            QualitySignal(name="short_justification", severity="warn"),
            QualitySignal(name="no_code_refs", severity="warn"),
        ]
        assert _recommend(signals) == "send_back"

    def test_pass_on_two_warns(self):
        from sfctl.analysis import _recommend
        from sfctl.models import QualitySignal

        signals = [
            QualitySignal(name="no_diff_file_refs", severity="warn"),
            QualitySignal(name="short_justification", severity="warn"),
        ]
        assert _recommend(signals) == "no_signal"


class TestAnalysisPipeline:
    def test_analyze_ranking_snapshot(self):
        from sfctl.analysis import analyze_task

        data = json.loads((SNAPSHOTS / "ranking.json").read_text())
        result = analyze_task(data)
        assert result.task_id == "t-qDRdMGmSeVpQYYUcR7IHC"
        assert result.task_type == "ranking"
        assert result.action in ("no_signal", "send_back", "quarantine")
        assert len(result.signals) > 0

    def test_analyze_proposal_snapshot(self):
        from sfctl.analysis import analyze_task

        data = json.loads((SNAPSHOTS / "pp.json").read_text())
        result = analyze_task(data)
        assert result.task_type == "proposal"
        assert result.action in ("no_signal", "send_back", "quarantine")

    def test_all_snapshots_dont_crash(self):
        from sfctl.analysis import analyze_task

        for f in sorted(SNAPSHOTS.glob("*.json")):
            try:
                data = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            result = analyze_task(data)
            assert result.action in ("no_signal", "send_back", "quarantine"), f"{f.name}: bad action"


class TestCLIAnalyze:
    def test_analyze_json_output(self):
        import subprocess

        result = subprocess.run(
            ["uv", "run", "python", "-m", "sfctl", "analyze",
             "--fixture", "snapshots/ranking.json", "--json"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "task_id" in data
        assert "action" in data
        assert "signals" in data
        assert "confidence" not in data
        assert "ai_detection" not in data
