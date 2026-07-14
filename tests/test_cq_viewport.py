"""Tests for arena response terminal-width toggle."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "arena_sample.json"


class TestResponseTerminalWidth:
    def test_toggle_class_and_state(self, make_app):
        from sfctl.cq_viewport import RESPONSE_TERMINAL_WIDTH, RESPONSE_WIDTH_CLASS
        from sfctl.handlers.arena import ArenaHandler
        from sfctl.task_types import TaskType

        data = json.loads(FIXTURE_PATH.read_text())
        app = make_app(task_id=data["task"]["taskId"], data=data)
        assert app.task_type == TaskType.ARENA_RANKING
        assert app.check_action("toggle_response_width", ()) is True
        handler = app.handler
        assert isinstance(handler, ArenaHandler)
        assert handler.narrow_response is False
        assert handler.response_body_classes() == ""
        assert "response-wrap-narrow" not in handler.response_wrap_classes()
        assert RESPONSE_TERMINAL_WIDTH == 80

        assert handler.toggle_response_width() is True
        assert handler.narrow_response is True
        assert RESPONSE_WIDTH_CLASS in handler.response_body_classes()
        assert "response-wrap-narrow" in handler.response_wrap_classes()

        assert handler.toggle_response_width() is False
        assert handler.response_body_classes() == ""
        assert "response-wrap-narrow" not in handler.response_wrap_classes()

    def test_hidden_for_classic(self, make_app, fixture_data):
        app = make_app(data=fixture_data)
        assert app.check_action("toggle_response_width", ()) is False

    def test_escape_clears_width(self, make_app):
        from sfctl.handlers.arena import ArenaHandler

        data = json.loads(FIXTURE_PATH.read_text())
        app = make_app(task_id=data["task"]["taskId"], data=data)
        handler = app.handler
        assert isinstance(handler, ArenaHandler)
        handler.toggle_response_width()
        assert handler.narrow_response is True
        assert app._exit_view_modes() is True
        assert handler.narrow_response is False
        assert app._exit_view_modes() is False

    def test_escape_clears_maximize(self, make_app, fixture_data):
        app = make_app(data=fixture_data)
        app._maximized = True
        assert app._exit_view_modes() is True
        assert app._maximized is False
