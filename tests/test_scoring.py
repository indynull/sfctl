"""Tests for scoring and justification persistence."""

from __future__ import annotations

TASK_ID = "t-D2F1God7q8QRa48qVJpO1"


class TestScoringPersistence:
    def test_save_load_round_trip(self):
        from sftui import scoring
        from sftui.models import ModelScores

        scores = [
            ModelScores(overall=3, response=1, code=2),
            ModelScores(overall=-1),
            ModelScores(),
        ]
        scoring.save_scores(TASK_ID, scores)
        loaded = scoring.load_scores(TASK_ID, 3)
        assert loaded[0] == scores[0]
        assert loaded[1].overall == -1
        assert loaded[2].total() == 0

    def test_load_scores_no_file(self):
        from sftui import scoring

        loaded = scoring.load_scores("t-nonexistent", 3)
        assert len(loaded) == 3
        assert all(s.total() == 0 for s in loaded)

    def test_justification_round_trip(self):
        from sftui import scoring

        text = "Model C is best because of narrowly scoped changes."
        scoring.save_justification(TASK_ID, text)
        assert scoring.load_justification(TASK_ID, []) == text

    def test_justification_from_history(self):
        from sftui import scoring

        history = [{"justification": {"value": "from history"}}]
        assert scoring.load_justification("t-nosaved", history) == "from history"

    def test_justification_empty_history(self):
        from sftui import scoring

        assert scoring.load_justification("t-nosaved", []) == ""

    def test_justification_single_dict_history(self):
        from sftui import scoring

        history = {"justification": {"value": "single entry"}}
        assert scoring.load_justification("t-single", history) == "single entry"

    def test_justification_single_dict_no_justification(self):
        from sftui import scoring

        history = {"other": "data"}
        assert scoring.load_justification("t-nojust", history) == ""

    def test_safe_task_id(self):
        from sftui.scoring import _safe_task_id

        assert _safe_task_id("t-abc123") == "t-abc123"
        assert _safe_task_id("t-abc/def") == "t-abc_def"
        assert _safe_task_id("bad!chars@here") == "bad_chars_here"
