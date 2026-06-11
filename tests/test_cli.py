"""Tests for CLI entry point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "task_sample.json"
TASK_ID = "t-D2F1God7q8QRa48qVJpO1"


class TestCli:
    def test_show_config_empty(self, monkeypatch, capsys):
        from sfctl import cli, config

        config.save_config({})
        monkeypatch.setattr("sys.argv", ["sfctl", "--show-config"])
        cli.main()
        assert "(empty)" in capsys.readouterr().out

    def test_show_config_with_data(self, monkeypatch, capsys):
        from sfctl import cli, config

        config.save_config({"browser": "chrome", "theme": "dark"})
        monkeypatch.setattr("sys.argv", ["sfctl", "--show-config"])
        cli.main()
        out = capsys.readouterr().out
        assert "browser = chrome" in out
        assert "theme = dark" in out

    def test_clear_config_all(self, monkeypatch, capsys):
        from sfctl import cli, config

        config.save_config({"browser": "chrome"})
        monkeypatch.setattr("sys.argv", ["sfctl", "--clear-config"])
        cli.main()
        assert config.load_config() == {}
        assert "Config cleared" in capsys.readouterr().out

    def test_clear_config_keys(self, monkeypatch, capsys):
        from sfctl import cli, config

        config.save_config({"browser": "chrome", "theme": "dark"})
        monkeypatch.setattr("sys.argv", ["sfctl", "--clear-config", "theme"])
        cli.main()
        loaded = config.load_config()
        assert "theme" not in loaded
        assert loaded["browser"] == "chrome"

    def test_set_config(self, monkeypatch, capsys):
        from sfctl import cli, config

        monkeypatch.setattr("sys.argv", ["sfctl", "--set", "theme", "monokai"])
        cli.main()
        assert config.load_config()["theme"] == "monokai"

    def test_set_config_with_task(self, monkeypatch, capsys):
        from sfctl import cli, config
        from sfctl.app import StarfleetApp

        monkeypatch.setattr(StarfleetApp, "run", lambda self: None)
        monkeypatch.setattr(
            "sys.argv",
            ["sfctl", TASK_ID, "--set", "api_base", "https://test.example.com", "--fixture", str(FIXTURE_PATH)],
        )
        cli.main()
        assert config.load_config()["api_base"] == "https://test.example.com"

    def test_fixture_mode(self, monkeypatch):
        from sfctl import cli
        from sfctl.app import StarfleetApp

        run_called = {}
        monkeypatch.setattr(StarfleetApp, "run", lambda self: run_called.update(task=self.task_arg))
        monkeypatch.setattr("sys.argv", ["sfctl", "--fixture", str(FIXTURE_PATH)])
        cli.main()
        assert TASK_ID in run_called["task"]

    def test_fixture_verbose(self, monkeypatch, capsys):
        from sfctl import cli
        from sfctl.app import StarfleetApp

        monkeypatch.setattr(StarfleetApp, "run", lambda self: None)
        monkeypatch.setattr("sys.argv", ["sfctl", "--fixture", str(FIXTURE_PATH), "-v"])
        cli.main()
        assert "Loaded fixture" in capsys.readouterr().out

    def test_fixture_with_task_arg(self, monkeypatch):
        from sfctl import cli
        from sfctl.app import StarfleetApp

        run_called = {}
        monkeypatch.setattr(StarfleetApp, "run", lambda self: run_called.update(task=self.task_arg))
        monkeypatch.setattr("sys.argv", ["sfctl", "t-custom", "--fixture", str(FIXTURE_PATH)])
        cli.main()
        assert run_called["task"] == "t-custom"

    def test_fixture_not_found(self, monkeypatch):
        from sfctl import cli

        monkeypatch.setattr("sys.argv", ["sfctl", "--fixture", "/nonexistent/file.json"])
        with pytest.raises(SystemExit):
            cli.main()

    def test_no_task_errors(self, monkeypatch):
        from sfctl import cli

        monkeypatch.setattr("sys.argv", ["sfctl"])
        with pytest.raises(SystemExit):
            cli.main()

    def test_task_with_cookies(self, monkeypatch):
        from sfctl import api as api_mod
        from sfctl import cli
        from sfctl.app import StarfleetApp

        fixture_data = json.loads(FIXTURE_PATH.read_text())
        monkeypatch.setattr(api_mod, "resolve_cookies", lambda cf, v=False: {"c": "1"})
        monkeypatch.setattr(api_mod, "fetch_data", lambda t, c: fixture_data)
        monkeypatch.setattr(StarfleetApp, "run", lambda self: None)
        monkeypatch.setattr("sys.argv", ["sfctl", TASK_ID])
        cli.main()

    def test_task_verbose(self, monkeypatch, capsys):
        from sfctl import api as api_mod
        from sfctl import cli
        from sfctl.app import StarfleetApp

        fixture_data = json.loads(FIXTURE_PATH.read_text())
        monkeypatch.setattr(api_mod, "resolve_cookies", lambda cf, v=False: {"tok": "v"})
        monkeypatch.setattr(api_mod, "fetch_data", lambda t, c: fixture_data)
        monkeypatch.setattr(StarfleetApp, "run", lambda self: None)
        monkeypatch.setattr("sys.argv", ["sfctl", TASK_ID, "-v"])
        cli.main()
        assert "Launching TUI" in capsys.readouterr().out

    def test_auth_error_decline(self, monkeypatch):
        from sfctl import api as api_mod
        from sfctl import cli

        monkeypatch.setattr(api_mod, "resolve_cookies", lambda cf, v=False: {})
        monkeypatch.setattr(api_mod, "fetch_data", lambda *a, **kw: (_ for _ in ()).throw(api_mod.AuthError("expired")))
        monkeypatch.setattr("builtins.input", lambda prompt: "n")
        monkeypatch.setattr("sys.argv", ["sfctl", TASK_ID])
        with pytest.raises(SystemExit):
            cli.main()

    def test_auth_error_accept(self, monkeypatch):
        from sfctl import api as api_mod
        from sfctl import cli
        from sfctl.app import StarfleetApp
        from sfctl.models import CookieProfile

        monkeypatch.setattr(api_mod, "resolve_cookies", lambda cf, v=False: {})
        call_count = 0

        def fetch_or_fail(t, c):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise api_mod.AuthError("expired")
            return json.loads(FIXTURE_PATH.read_text())

        monkeypatch.setattr(api_mod, "fetch_data", fetch_or_fail)
        monkeypatch.setattr(
            api_mod, "interactive_cookie_setup",
            lambda: CookieProfile("/p", "Chrome", "chrome"),
        )
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"c": "v"})
        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        monkeypatch.setattr(StarfleetApp, "run", lambda self: None)
        monkeypatch.setattr("sys.argv", ["sfctl", TASK_ID])
        cli.main()


class TestMain:
    def test_main_calls_cli(self, monkeypatch):
        called = False

        def fake_main():
            nonlocal called
            called = True

        import sfctl.cli

        monkeypatch.setattr(sfctl.cli, "main", fake_main)

        import importlib

        import sfctl.__main__

        importlib.reload(sfctl.__main__)
        assert called
