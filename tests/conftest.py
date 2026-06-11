"""Shared test fixtures for sfctl."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "task_sample.json"
TASK_ID = "t-EXAMPLE001"


@pytest.fixture
def fixture_data() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def parsed(fixture_data):
    from sfctl.parsing import parse_content

    return parse_content(fixture_data["content"])


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Redirect config and data dirs to tmp_path for every test."""
    from sfctl import config, scoring

    cfg = tmp_path / "config"
    cfg.mkdir()
    dat = tmp_path / "data"
    dat.mkdir()
    monkeypatch.setattr(config, "user_config_dir", lambda *a, **kw: str(cfg))
    monkeypatch.setattr(config, "user_data_dir", lambda *a, **kw: str(dat))
    monkeypatch.setattr(scoring, "data_dir", lambda: dat)
    config.update_config(tutorial_seen=True)


@pytest.fixture
def make_app(fixture_data):
    """Factory fixture for creating a StarfleetApp with test data."""
    from sfctl.app import StarfleetApp

    def _make(task_id=TASK_ID, data=None, cookies=None):
        return StarfleetApp(task_id, data or fixture_data, cookies=cookies)

    return _make


@pytest.fixture
def app(make_app):
    """Standard app instance for tests."""
    return make_app()


@pytest.fixture
def minimal_data():
    """Minimal valid data dict for edge-case tests (code review with no models)."""
    return {
        "task": {"taskId": "t-min"},
        "content": {
            "content": {
                "items": [
                    {"type": "collection", "title": "Model Traces", "items": []},
                ]
            }
        },
        "history": [],
        "feedback": {},
    }
