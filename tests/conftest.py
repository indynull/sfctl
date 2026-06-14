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
    from sfctl.diff import parse_content

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


def _proposal_history_entry(
    rubrics=None,
    solved="partial",
    duration="1h-2h",
    issues="",
    review_level=0,
):
    """Build a single proposal history entry for tests."""
    rubric_items = [
        {"nestedAnnotations": {"rubric": {"_sf_rich": True, "value": r}}} for r in (rubrics or [])
    ]
    return {
        "reviewLevel": review_level,
        "coding_question": {
            "sessions": [
                {
                    "githubLink": "https://github.com/example/repo",
                    "side": "A",
                    "languagePreset": "python-uv",
                }
            ],
            "rollouts": {
                "A": {
                    "turns": [
                        {
                            "prompt": {
                                "role": "user",
                                "content": [{"type": "text", "text": "Implement feature X"}],
                            },
                            "codePatch": (
                                "diff --git a/foo.py b/foo.py\n"
                                "--- a/foo.py\n+++ b/foo.py\n"
                                "@@ -1 +1 @@\n-old\n+new\n"
                            ),
                            "bashHistory": [
                                {"timestamp": "2026-05-28T14:11:19Z", "command": "uv sync"}
                            ],
                        }
                    ],
                    "finalFeedback": [
                        {"questionId": "prompt", "type": "text", "value": "Implement feature X"}
                    ],
                    "finalBashHistory": [
                        {"timestamp": "2026-05-28T14:11:19Z", "command": "uv sync"},
                        {"timestamp": "2026-05-28T14:13:10Z", "command": "uv run pytest"},
                    ],
                    "traceRef": "coding-question/worker/session/trace.json",
                    "finalSessionSummary": {
                        "current_model_id": "test-model-v1",
                        "agent_name": "grok-build",
                        "num_messages": 2,
                    },
                }
            },
        },
        "repo_description": {"_sf_rich": True, "value": "A test repo for testing"},
        "difficulty_explanation": {"_sf_rich": True, "value": "Medium difficulty task"},
        "familiarity_explanation": {"_sf_rich": True, "value": "Own project"},
        "domain": {"_sf_rich": True, "value": "other"},
        "opus_duration": {"_sf_rich": True, "value": duration},
        "opus_solved": {"_sf_rich": True, "value": solved},
        "opus_issues_partial": {
            "_sf_rich": True,
            "value": issues,
            "comments": [
                {"content": "test comment", "createdAt": 1779984064012, "createdBy": {}}
            ]
            if issues
            else [],
        },
        "rubrics": {"items": rubric_items},
        "feedback": {"entries": []},
    }


@pytest.fixture
def proposal_data():
    """Proposal task data for testing."""
    return {
        "task": {"taskId": "t-PROP001", "metadata": {"taskType": "labeling"}},
        "content": {
            "taskId": "t-PROP001",
            "content": {"items": [{"type": "text"}, {"type": "questions", "title": "Check list"}]},
            "questions": [
                {"questionId": "coding_question", "type": "coding"},
                {"questionId": "rubrics", "type": "dynamicList"},
                {"questionId": "repo_description", "type": "text"},
                {"questionId": "opus_solved", "type": "choices"},
                {"questionId": "opus_duration", "type": "choices"},
            ],
        },
        "history": [
            _proposal_history_entry(
                rubrics=["Rubric one", "Rubric two", "Rubric three"],
                solved="partial",
                issues="Model failed on edge case",
            ),
            _proposal_history_entry(
                rubrics=["Rubric one", "Rubric two", "Rubric three", "Rubric four"],
                solved="partial",
                issues="Model failed on edge case",
                review_level=0.5,
            ),
        ],
        "feedback": {
            "entries": [
                {"reviewLevel": 0, "email": "user1@example.com", "message": ""},
                {
                    "reviewLevel": 1,
                    "email": "user2@example.com",
                    "message": "Good task, add rubrics",
                    "score": 6,
                },
            ]
        },
    }
