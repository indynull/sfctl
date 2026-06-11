"""Pydantic model validation tests."""

from __future__ import annotations

TASK_ID = "t-EXAMPLE001"


class TestTaskResponse:
    def test_task_id(self, fixture_data):
        from sfctl.models import TaskResponse

        resp = TaskResponse.model_validate(fixture_data["task"])
        assert resp.taskId == TASK_ID

    def test_status_and_review_level(self, fixture_data):
        from sfctl.models import TaskResponse

        resp = TaskResponse.model_validate(fixture_data["task"])
        assert resp.status == "ready"
        assert resp.reviewLevel == 2

    def test_action_history(self, fixture_data):
        from sfctl.models import TaskResponse

        resp = TaskResponse.model_validate(fixture_data["task"])
        assert len(resp.actionHistory) == 2
        ah0 = resp.actionHistory[0]
        assert ah0.type == "transition"
        assert ah0.fromLevel == 0
        assert ah0.toLevel == 1
        assert ah0.userId == "user2@example.com"
        assert ah0.passQA is None
        ah1 = resp.actionHistory[1]
        assert ah1.fromLevel == 1
        assert ah1.toLevel == 2
        assert ah1.passQA is True


class TestHistoryEntry:
    def test_rankings_present(self, fixture_data):
        from sfctl.models import HistoryEntry

        for h in fixture_data["history"]:
            entry = HistoryEntry.model_validate(h)
            assert entry.preference_ranking is not None
            assert entry.response_quality_ranking is not None
            assert entry.code_quality_ranking is not None

    def test_ranking_values(self, fixture_data):
        from sfctl.models import HistoryEntry

        entry = HistoryEntry.model_validate(fixture_data["history"][0])
        ids = [item.id for item in entry.preference_ranking.value]
        assert ids == ["model_c", "model_a", "model_b"]

    def test_justification(self, fixture_data):
        from sfctl.models import HistoryEntry

        entry = HistoryEntry.model_validate(fixture_data["history"][0])
        assert isinstance(entry.justification.value, str)
        assert "model a" in entry.justification.value.lower()

    def test_confidence(self, fixture_data):
        from sfctl.models import HistoryEntry

        entry = HistoryEntry.model_validate(fixture_data["history"][0])
        assert entry.confidence.value == "high"

    def test_email_and_review_level(self, fixture_data):
        from sfctl.models import HistoryEntry

        e0 = HistoryEntry.model_validate(fixture_data["history"][0])
        assert e0.email == "user2@example.com"
        assert e0.reviewLevel == 0
        e1 = HistoryEntry.model_validate(fixture_data["history"][1])
        assert e1.email == "user1@example.com"
        assert e1.reviewLevel == 1

    def test_feedback_nested(self, fixture_data):
        from sfctl.models import HistoryEntry

        e1 = HistoryEntry.model_validate(fixture_data["history"][1])
        assert e1.feedback is not None
        assert len(e1.feedback.entries) == 1
        assert e1.feedback.entries[0].score == 7


class TestFeedbackResponse:
    def test_entries(self, fixture_data):
        from sfctl.models import FeedbackResponse

        resp = FeedbackResponse.model_validate(fixture_data["feedback"])
        assert len(resp.entries) == 1
        fb = resp.entries[0]
        assert fb.reviewLevel == 1
        assert fb.score == 7
        assert fb.email == "user1@example.com"
        assert fb.timestamp == 1781053990265
        assert "clear summary" in fb.message


class TestContentResponse:
    def test_structure(self, fixture_data):
        from sfctl.models import ContentResponse

        resp = ContentResponse.model_validate(fixture_data["content"])
        assert resp.taskId == TASK_ID
        assert resp.content is not None
        assert len(resp.content.items) >= 4


class TestModelScores:
    def test_defaults(self):
        from sfctl.models import ModelScores

        s = ModelScores()
        assert s.total() == 0
        assert not s.any_nonzero()

    def test_total(self):
        from sfctl.models import ModelScores

        s = ModelScores(overall=3, response=2, code=1)
        assert s.total() == 6
        assert s.any_nonzero()

    def test_round_trip(self):
        from sfctl.models import ModelScores

        s = ModelScores(overall=5, response=-1, code=3)
        assert s == ModelScores.from_dict(s.to_dict())

    def test_from_dict_partial(self):
        from sfctl.models import ModelScores

        s = ModelScores.from_dict({"overall": 2})
        assert s.overall == 2
        assert s.response == 0
        assert s.code == 0

    def test_negative_scores(self):
        from sfctl.models import ModelScores

        s = ModelScores(overall=-3, response=-2, code=-1)
        assert s.total() == -6
        assert s.any_nonzero()
