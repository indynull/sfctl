"""Tests for scoring and justification persistence."""

from __future__ import annotations

TASK_ID = "t-EXAMPLE001"


class TestScoringPersistence:
    def test_safe_task_id(self):
        from sfctl.scoring import safe_task_id

        assert safe_task_id("t-abc123") == "t-abc123"
        assert safe_task_id("t-abc/def") == "t-abc_def"
        assert safe_task_id("bad!chars@here") == "bad_chars_here"


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
        save_annotations(TASK_ID, anns, "my summary", review_comments="note 1")
        loaded_anns, summary, comments, justifications = load_annotations(TASK_ID, 3)
        assert summary == "my summary"
        assert comments == "note 1"
        assert justifications["response_justification"] == ""
        assert len(loaded_anns[0]) == 1
        assert loaded_anns[0][0].filename == "a.py"
        assert loaded_anns[0][0].sentiment == 1
        assert len(loaded_anns[1]) == 1
        assert loaded_anns[1][0].context == "response"
        assert len(loaded_anns[2]) == 0

    def test_multi_justification_round_trip(self):
        from sfctl.scoring import load_annotations, save_annotations

        anns = [[], [], []]
        multi = {
            "response_justification": "resp notes",
            "code_quality_justification": "code notes",
            "overall_justification": "overall notes",
        }
        save_annotations(
            "t-multi-just",
            anns,
            "",
            justifications=multi,
            server_multi_justifications=multi,
        )
        _a, _s, _c, just = load_annotations("t-multi-just", 3)
        assert just["response_justification"] == "resp notes"
        assert just["code_quality_justification"] == "code notes"
        assert just["overall_justification"] == "overall notes"

    def test_multi_justification_seeds_from_history(self):
        from sfctl.scoring import load_annotations

        history = [
            {
                "response_justification": {"value": "server resp"},
                "code_quality_justification": {"value": "server code"},
                "overall_justification": {"value": "server overall"},
                "reviewLevel": 0,
            },
        ]
        _a, _s, _c, just = load_annotations(
            "t-multi-seed-history", 3, history
        )
        assert just["response_justification"] == "server resp"
        assert just["code_quality_justification"] == "server code"
        assert just["overall_justification"] == "server overall"

    def test_multi_justification_accepts_plain_string_fields(self):
        """Some server history entries ship bare strings, not {_sf_rich,value}."""
        from sfctl.arena import _text_field, server_editable_justifications
        from sfctl.scoring import load_annotations

        entry = {
            "response_justification": {"value": "rich resp"},
            "code_quality_justification": "plain code notes",
            "overall_justification": None,
            "reviewLevel": 1,
        }
        assert _text_field(entry, "response_justification") == "rich resp"
        assert _text_field(entry, "code_quality_justification") == "plain code notes"
        assert _text_field(entry, "overall_justification") == ""
        multi = server_editable_justifications(entry)
        assert multi["code_quality_justification"] == "plain code notes"
        assert multi["overall_justification"] == ""

        _a, _s, _c, just = load_annotations("t-plain-string-just", 3, [entry])
        assert just["response_justification"] == "rich resp"
        assert just["code_quality_justification"] == "plain code notes"
        assert just["overall_justification"] == ""

    def test_multi_justification_keeps_local_when_server_unchanged(self):
        from sfctl.scoring import load_annotations, save_annotations

        history = [
            {
                "response_justification": {"value": "server resp"},
                "code_quality_justification": {"value": "server code"},
                "overall_justification": {"value": "server overall"},
                "reviewLevel": 0,
            },
        ]
        multi_local = {
            "response_justification": "my resp edits",
            "code_quality_justification": "server code",
            "overall_justification": "server overall",
        }
        multi_server = {
            "response_justification": "server resp",
            "code_quality_justification": "server code",
            "overall_justification": "server overall",
        }
        save_annotations(
            "t-multi-keep-local",
            [[], [], []],
            "",
            justifications=multi_local,
            server_multi_justifications=multi_server,
        )
        _a, _s, _c, just = load_annotations("t-multi-keep-local", 3, history)
        assert just["response_justification"] == "my resp edits"

    def test_load_annotations_no_file(self):
        from sfctl.scoring import load_annotations

        anns, summary, _comments, just = load_annotations("t-nonexistent-ann", 2)
        assert len(anns) == 2
        assert all(len(a) == 0 for a in anns)
        assert summary == ""
        assert just["response_justification"] == ""

    def test_load_annotations_seeds_from_history(self):
        from sfctl.scoring import load_annotations

        history = [
            {"justification": {"_sf_rich": True, "value": "L0 justification"}, "reviewLevel": 0},
            {"justification": {"_sf_rich": True, "value": "L1 revised justification"}, "reviewLevel": 1},
        ]
        _anns, summary, _comments, _just = load_annotations(
            "t-nonexistent-history-seed", 3, history
        )
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
        loaded_anns, summary, _comments, _just = load_annotations(
            "t-revision-test", 3, l1_history
        )
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

        _loaded_anns, summary, _comments, _just = load_annotations(
            "t-keep-local", 3, history
        )
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

    def test_review_state_set_justification(self):
        from sfctl.scoring import ReviewState

        state = ReviewState("t-review-set-just", 3)
        state.set_justification("code_quality_justification", "yanked code")
        assert state.justification_text("code_quality_justification") == "yanked code"
        assert "Code Quality" in state.combined_justifications()
