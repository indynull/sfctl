"""Tests for scoring and justification persistence."""

from __future__ import annotations

TASK_ID = "t-EXAMPLE001"


class TestScoringPersistence:
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
            [
                Annotation(
                    filename="a.py",
                    line_ref="L1",
                    snippet="+x",
                    comment="good",
                    context="code",
                    sentiment=1,
                )
            ],
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

    def test_load_annotations_seeds_from_history(self):
        from sfctl.scoring import load_annotations

        history = [
            {"justification": {"_sf_rich": True, "value": "L0 justification"}, "reviewLevel": 0},
            {"justification": {"_sf_rich": True, "value": "L1 revised justification"}, "reviewLevel": 1},
        ]
        _anns, summary = load_annotations("t-nonexistent-history-seed", 3, history)
        assert summary == "L1 revised justification"

    def test_load_annotations_updates_on_new_revision(self):
        from sfctl.models import Annotation
        from sfctl.scoring import load_annotations, save_annotations

        anns = [[Annotation(context="code", sentiment=1)], [], []]
        save_annotations("t-revision-test", anns, "L0 justification", "L0 justification")

        l1_history = [
            {"justification": {"_sf_rich": True, "value": "L0 justification"}, "reviewLevel": 0},
            {"justification": {"_sf_rich": True, "value": "L1 revised justification"}, "reviewLevel": 1},
        ]
        loaded_anns, summary = load_annotations("t-revision-test", 3, l1_history)
        assert summary == "L1 revised justification"
        assert len(loaded_anns[0]) == 1
        assert loaded_anns[0][0].sentiment == 1

    def test_load_annotations_keeps_local_when_server_unchanged(self):
        from sfctl.models import Annotation
        from sfctl.scoring import load_annotations, save_annotations

        history = [
            {"justification": {"_sf_rich": True, "value": "server just"}, "reviewLevel": 0},
        ]
        anns = [[Annotation(context="code", sentiment=1)], [], []]
        save_annotations("t-keep-local", anns, "my custom edits", "server just")

        _loaded_anns, summary = load_annotations("t-keep-local", 3, history)
        assert summary == "my custom edits"

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
