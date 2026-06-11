"""Tests for ranking computation -- pure functions, no TUI needed."""

from __future__ import annotations

from sfctl.models import ModelData, ModelScores
from sfctl.ranking import (
    diff_items,
    local_ranking_summary,
    model_id,
    model_letter,
    model_rank,
    model_summary_text,
    nav_items,
    previous_model_rank,
    previous_ranking_summary,
    ranking_for_category,
    rankings_summary,
)


def _make_models(n=3):
    return [
        ModelData(
            name=f"Model {chr(65 + i)}",
            diff="",
            trace_summary="summary",
            messages=[],
            tool_events=[],
        )
        for i in range(n)
    ]


class TestModelIdentification:
    def test_model_letter(self):
        assert model_letter(0) == "A"
        assert model_letter(1) == "B"
        assert model_letter(2) == "C"

    def test_model_id(self):
        assert model_id(0) == "model-a"
        assert model_id(1) == "model-b"
        assert model_id(2) == "model-c"


class TestNavItems:
    def test_includes_models_and_feedback(self):
        models = _make_models(3)
        items = nav_items(models)
        assert ("A", "model-a") in items
        assert ("B", "model-b") in items
        assert ("C", "model-c") in items
        assert ("Overview", "overview") in items

    def test_empty_models(self):
        items = nav_items([])
        assert items == [("Overview", "overview")]


class TestDiffItems:
    def test_with_file_diffs(self):
        from sfctl.models import FileDiff

        models = _make_models(2)
        models[0].file_diffs = [FileDiff("foo.py", "diff"), FileDiff("bar.py", "diff")]
        models[1].file_diffs = [FileDiff("baz.py", "diff")]
        items = diff_items(models)
        assert len(items) == 3
        assert items[0] == ("Diff: A / foo.py", 0, "foo.py")
        assert items[2] == ("Diff: B / baz.py", 1, "baz.py")

    def test_no_diffs(self):
        assert diff_items(_make_models(2)) == []


class TestRankingForCategory:
    def test_with_votes(self):
        scores = [ModelScores(overall=3), ModelScores(overall=-1), ModelScores(overall=1)]
        result = ranking_for_category(scores, "overall")
        assert "A" in result
        assert "B" in result
        assert "C" in result
        assert ">" in result

    def test_all_zeros(self):
        scores = [ModelScores(), ModelScores(), ModelScores()]
        assert ranking_for_category(scores, "overall") == ""

    def test_single_model(self):
        scores = [ModelScores(response=5)]
        result = ranking_for_category(scores, "response")
        assert "A" in result


class TestModelRank:
    def test_ordered(self):
        scores = [ModelScores(overall=3), ModelScores(overall=1), ModelScores(overall=2)]
        assert model_rank(scores, 0) == 0  # highest
        assert model_rank(scores, 2) == 1  # middle
        assert model_rank(scores, 1) == 2  # lowest

    def test_all_equal(self):
        scores = [ModelScores(), ModelScores(), ModelScores()]
        # All have same total, rank by sort stability
        assert isinstance(model_rank(scores, 0), int)


class TestLocalRankingSummary:
    def test_with_votes(self):
        scores = [ModelScores(overall=3), ModelScores(overall=-1)]
        result = local_ranking_summary(scores)
        assert "Overall:" in result
        assert "A" in result

    def test_no_votes(self):
        scores = [ModelScores(), ModelScores()]
        assert local_ranking_summary(scores) == ""


class TestPreviousRankingSummary:
    def test_with_history(self, fixture_data):
        result = previous_ranking_summary(fixture_data["history"])
        assert "Overall:" in result

    def test_empty_history(self):
        assert previous_ranking_summary([]) == ""

    def test_history_as_dict(self):
        history = {"preference_ranking": {"value": [{"id": "model_a"}, {"id": "model_b"}]}}
        result = previous_ranking_summary(history)
        assert "Overall:" in result


class TestPreviousModelRank:
    def test_with_history(self, fixture_data):
        # fixture has preference_ranking with model_c first
        rank = previous_model_rank(fixture_data["history"], 2)  # model C
        assert rank == 0

    def test_empty_history(self):
        assert previous_model_rank([], 0) is None

    def test_history_as_dict(self):
        history = {"preference_ranking": {"value": [{"id": "model_a"}, {"id": "model_b"}]}}
        assert previous_model_rank(history, 0) == 0
        assert previous_model_rank(history, 1) == 1
        assert previous_model_rank(history, 2) is None


class TestRankingsSummary:
    def test_both_local_and_previous(self, fixture_data):
        scores = [ModelScores(overall=3), ModelScores(overall=-1), ModelScores(overall=1)]
        result = rankings_summary(scores, fixture_data["history"])
        assert "Last:" in result
        assert "Yours:" in result

    def test_previous_only(self, fixture_data):
        scores = [ModelScores(), ModelScores(), ModelScores()]
        result = rankings_summary(scores, fixture_data["history"])
        assert "Last:" in result
        assert "Yours:" not in result

    def test_local_only(self):
        scores = [ModelScores(overall=1), ModelScores(overall=-1)]
        result = rankings_summary(scores, [])
        assert "Last:" not in result
        assert ">" in result

    def test_neither(self):
        scores = [ModelScores(), ModelScores()]
        assert rankings_summary(scores, []) == ""


class TestModelSummaryText:
    def test_from_messages(self):
        m = ModelData(
            name="A", diff="", trace_summary="trace",
            messages=[{"role": "assistant", "content": "# Hello"}],
            tool_events=[],
        )
        result = model_summary_text(m)
        assert "Hello" in result

    def test_from_trace_summary(self):
        m = ModelData(
            name="A", diff="", trace_summary="trace fallback",
            messages=[], tool_events=[],
        )
        result = model_summary_text(m)
        assert "trace fallback" in result

    def test_no_content(self):
        m = ModelData(
            name="A", diff="", trace_summary=None,
            messages=[], tool_events=[],
        )
        result = model_summary_text(m)
        assert "No summary" in result
