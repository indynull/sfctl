"""Tests for arena ranking (clarity checklist) task type."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "arena_sample.json"
REAL_PATH = Path(__file__).resolve().parents[1] / "new_format_arena.json"


@pytest.fixture
def arena_data() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


class TestDetectArena:
    def test_fixture_is_arena(self, arena_data):
        from sfctl.task_types import TaskType, detect_task_type

        assert detect_task_type(arena_data) == TaskType.ARENA_RANKING

    def test_classic_ranking_not_arena(self, fixture_data):
        from sfctl.task_types import TaskType, detect_task_type

        assert detect_task_type(fixture_data) == TaskType.CODE_REVIEW

    def test_real_file_if_present(self):
        if not REAL_PATH.exists():
            pytest.skip("new_format_arena.json not in tree")
        from sfctl.task_types import TaskType, detect_task_type

        data = json.loads(REAL_PATH.read_text())
        assert detect_task_type(data) == TaskType.ARENA_RANKING


class TestArenaParse:
    def test_meta_and_rules(self, arena_data):
        from sfctl.arena import parse_arena_meta

        meta = parse_arena_meta(arena_data)
        assert meta.label_map["A"] == "plasticcup"
        assert meta.label_map["B"] == "tahoma"
        assert meta.label_map["C"] == "opus48-xhigh"
        assert meta.anchor == "opus48-xhigh"
        # Labels come from the task question options, not a local rule catalog
        assert meta.rule_labels.get("o4_violated") == "No bloated body"

    def test_checklist_from_history(self, arena_data):
        from sfctl.arena import (
            checklist_from_entry,
            checklist_violation_summary,
            format_checklist_markup,
            parse_arena_meta,
        )

        meta = parse_arena_meta(arena_data)
        entry = arena_data["history"][0]
        cl = checklist_from_entry(entry, meta.rule_labels)
        assert cl is not None
        assert cl.col_headers == ["Model A", "Model B", "Model C"]
        # Model C Organisation has o4_violated -> title from option text
        assert "No bloated body" in cl.cells[0][2]
        summary = checklist_violation_summary(cl)
        assert "C:1" in summary
        markup = format_checklist_markup(cl)
        assert "No bloated body" in markup
        assert "Organisation" in markup or "Organization" in markup

    def test_justifications(self, arena_data):
        from sfctl.arena import combined_justification, justification_sections

        entry = arena_data["history"][0]
        sections = justification_sections(entry)
        labels = {lab for lab, _ in sections}
        assert "Prompt understanding" in labels
        assert "Response justification" in labels
        assert "Code justification" in labels
        combined = combined_justification(entry)
        assert "## Prompt understanding" in combined
        assert "## Code justification" in combined

    def test_has_changes_on_checklist(self, arena_data):
        from sfctl.arena import has_arena_changes

        prev = arena_data["history"][0]
        curr = json.loads(json.dumps(prev))
        assert not has_arena_changes(prev, curr)
        cells = curr["response_clarity_checklist"]["cells"]
        cells[1][0] = {"_sf_rich": True, "value": ["p14_violated"]}
        assert has_arena_changes(prev, curr)

    def test_has_changes_on_justification(self, arena_data):
        from sfctl.arena import has_arena_changes

        prev = arena_data["history"][0]
        curr = json.loads(json.dumps(prev))
        curr["code_quality_justification"] = {"value": "totally different"}
        assert has_arena_changes(prev, curr)


class TestArenaHandler:
    def test_handler_parse_and_headers(self, arena_data, make_app):
        from sfctl.handlers.arena import ArenaHandler
        from sfctl.task_types import TaskType

        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        assert app.task_type == TaskType.ARENA_RANKING
        assert isinstance(app.handler, ArenaHandler)
        assert len(app.models) == 3
        assert app.handler.model_header_label(0) == "[bold]A[/bold]"
        assert app.handler.model_header_label(1) == "[bold]B[/bold]"

    def test_scoreboard_includes_cq(self, arena_data, make_app):
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        parts = app.handler.scoreboard_parts()
        joined = " ".join(parts)
        assert "Checklist" in joined or "C:1" in joined

    def test_hidden_edit_justification(self, arena_data, make_app):
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        assert "edit_justification" in app.handler.hidden_actions()

    @pytest.mark.asyncio
    async def test_overview_mounts_readonly(self, arena_data, make_app):
        app = make_app(task_id=arena_data["task"]["taskId"], data=arena_data)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Overview should populate without justification editor
            from textual.widgets import TextArea

            editors = list(app.query(TextArea))
            # Arena overview is read-only: no justification editor widget
            just_editors = [e for e in editors if e.id == "justification-editor"]
            assert just_editors == []
