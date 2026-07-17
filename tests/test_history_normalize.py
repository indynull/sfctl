"""Tests for history payload normalization (clean / unreviewed tasks)."""

from __future__ import annotations


class TestAsHistoryList:
    def test_none_and_empty(self):
        from sfctl.history import as_history_list

        assert as_history_list(None) == []
        assert as_history_list([]) == []
        assert as_history_list({}) == []

    def test_list_of_entries(self):
        from sfctl.history import as_history_list

        raw = [{"reviewLevel": 0}, {"reviewLevel": 1}]
        assert as_history_list(raw) == raw

    def test_single_object(self):
        from sfctl.history import as_history_list

        raw = {"reviewLevel": 0, "email": "a@b.c"}
        assert as_history_list(raw) == [raw]

    def test_drops_non_dicts(self):
        from sfctl.history import as_history_list

        assert as_history_list([{"a": 1}, "x", None, 3]) == [{"a": 1}]


class TestCleanTaskApp:
    def test_app_accepts_null_history(self, make_app, fixture_data):
        data = dict(fixture_data)
        data["history"] = None
        app = make_app(data=data)
        assert app._get_history() == []
        assert isinstance(app.rankings_summary(), str)

    def test_app_accepts_empty_history(self, make_app, fixture_data):
        data = dict(fixture_data)
        data["history"] = []
        app = make_app(data=data)
        assert app._get_history() == []
        assert app.task_type.value in ("code_review", "arena_ranking", "unknown")
