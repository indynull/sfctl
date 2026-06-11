"""Tests for scoring and justification persistence."""

from __future__ import annotations

TASK_ID = "t-D2F1God7q8QRa48qVJpO1"


class TestScoringPersistence:
    def test_save_load_round_trip(self):
        from sfctl import scoring
        from sfctl.models import ModelScores

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
        from sfctl import scoring

        loaded = scoring.load_scores("t-nonexistent", 3)
        assert len(loaded) == 3
        assert all(s.total() == 0 for s in loaded)

    def test_justification_round_trip(self):
        from sfctl import scoring

        text = "Model C is best because of narrowly scoped changes."
        scoring.save_justification(TASK_ID, text)
        assert scoring.load_justification(TASK_ID, []) == text

    def test_justification_from_history(self):
        from sfctl import scoring

        history = [{"justification": {"value": "from history"}}]
        assert scoring.load_justification("t-nosaved", history) == "from history"

    def test_justification_empty_history(self):
        from sfctl import scoring

        assert scoring.load_justification("t-nosaved", []) == ""

    def test_justification_single_dict_history(self):
        from sfctl import scoring

        history = {"justification": {"value": "single entry"}}
        assert scoring.load_justification("t-single", history) == "single entry"

    def test_justification_single_dict_no_justification(self):
        from sfctl import scoring

        history = {"other": "data"}
        assert scoring.load_justification("t-nojust", history) == ""

    def test_safe_task_id(self):
        from sfctl.scoring import _safe_task_id

        assert _safe_task_id("t-abc123") == "t-abc123"
        assert _safe_task_id("t-abc/def") == "t-abc_def"
        assert _safe_task_id("bad!chars@here") == "bad_chars_here"


class TestAnnotationPersistence:
    def test_annotation_round_trip(self):
        from sfctl.models import Annotation
        from sfctl.scoring import load_annotations, save_annotations

        anns = [
            [Annotation(filename="a.py", line_ref="L1", snippet="+x", comment="good", context="code", sentiment=1)],
            [Annotation(context="response", sentiment=-1)],
            [],
        ]
        save_annotations(TASK_ID, anns, "my summary")
        loaded_anns, summary = load_annotations(TASK_ID, 3)
        assert summary == "my summary"
        assert len(loaded_anns[0]) == 1
        assert loaded_anns[0][0].filename == "a.py"
        assert loaded_anns[0][0].sentiment == 1
        assert len(loaded_anns[1]) == 1
        assert loaded_anns[1][0].context == "response"
        assert len(loaded_anns[2]) == 0

    def test_load_annotations_no_file(self):
        from sfctl.scoring import load_annotations

        anns, summary = load_annotations("t-nonexistent-ann", 2)
        assert len(anns) == 2
        assert all(len(a) == 0 for a in anns)
        assert summary == ""

    def test_scores_from_annotations(self):
        from sfctl.models import Annotation
        from sfctl.scoring import scores_from_annotations

        anns = [
            [
                Annotation(context="code", sentiment=1),
                Annotation(context="code", sentiment=1),
                Annotation(context="response", sentiment=-1),
            ],
            [Annotation(context="overall", sentiment=1)],
        ]
        scores = scores_from_annotations(anns)
        assert scores[0].code == 2
        assert scores[0].response == -1
        assert scores[0].overall == 0
        assert scores[1].overall == 1

    def test_render_annotations_md(self):
        from sfctl.models import Annotation
        from sfctl.scoring import render_annotations_md

        anns = [
            [Annotation(filename="foo.py", line_ref="L12", snippet="+x=1", comment="nice", context="code", sentiment=1)],
            [],
        ]
        md = render_annotations_md(anns, "overall good")
        assert "## Model A" in md
        assert "(+1) nice" in md
        assert "`foo.py:L12`" in md
        assert "```diff" in md
        assert "## Summary" in md
        assert "overall good" in md

    def test_render_empty(self):
        from sfctl.scoring import render_annotations_md

        md = render_annotations_md([[], []], "")
        assert md.strip() == ""

    def test_migrate_legacy_scores(self):
        from sfctl.models import ModelScores
        from sfctl.scoring import load_annotations, save_scores

        save_scores(TASK_ID, [ModelScores(code=2), ModelScores(overall=-1)])
        anns, _summary = load_annotations(TASK_ID, 2)
        # code=2 -> two Annotation(context="code", sentiment=1)
        assert len(anns[0]) == 2
        assert all(a.context == "code" and a.sentiment == 1 for a in anns[0])
        # overall=-1 -> one Annotation(context="overall", sentiment=-1)
        assert len(anns[1]) == 1
        assert anns[1][0].context == "overall"
        assert anns[1][0].sentiment == -1
