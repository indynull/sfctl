"""Tests for configuration management."""

from __future__ import annotations

TASK_ID = "t-D2F1God7q8QRa48qVJpO1"


class TestConfig:
    def test_save_load(self, tmp_path, monkeypatch):
        from sfctl import config

        monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
        config.save_config({"api_base": "https://example.com", "browser": "chrome"})
        loaded = config.load_config()
        assert loaded == {"api_base": "https://example.com", "browser": "chrome"}

    def test_update_merges(self, tmp_path, monkeypatch):
        from sfctl import config

        monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
        config.save_config({"browser": "chrome"})
        config.update_config(cookie_file="/tmp/cookies")
        loaded = config.load_config()
        assert loaded["browser"] == "chrome"
        assert loaded["cookie_file"] == "/tmp/cookies"

    def test_empty_default(self):
        from sfctl import config

        config.save_config({})
        assert config.load_config() == {}

    def test_api_base_default(self):
        from sfctl import config

        assert config.get_api_base() == "https://starfleet-backend.teachx.ai"

    def test_web_url(self):
        from sfctl import config

        url = config.get_web_url(f"/tasks/{TASK_ID}")
        assert url == f"https://starfleet.teachx.ai/tasks/{TASK_ID}"


class TestConfigDirCreation:
    def test_config_dir_creates(self):
        from sfctl import config

        d = config.config_dir()
        assert d.exists()
        assert d.is_dir()

    def test_data_dir_creates(self):
        from sfctl import config

        d = config.data_dir()
        assert d.exists()
        assert d.is_dir()
