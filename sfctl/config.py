"""Configuration management for Starfleet TUI."""

from __future__ import annotations

import json
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

DEFAULT_API_BASE = "https://starfleet-backend.teachx.ai"

HEADERS = {
    "accept": "*/*",
    "origin": "https://starfleet.teachx.ai",
    "referer": "https://starfleet.teachx.ai/",
    "user-agent": "Mozilla/5.0 (compatible; sfctl/1.0)",
}

_APP_NAME = "starfleet"


def config_dir() -> Path:
    """OS-appropriate config directory (XDG on Linux, AppData on Windows, etc.)."""
    d = Path(user_config_dir(_APP_NAME))
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    """OS-appropriate data directory for scores and justifications."""
    d = Path(user_data_dir(_APP_NAME))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict:  # type: ignore[type-arg]
    path = _config_path()
    if path.exists():
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    return {}


def save_config(config: dict) -> None:
    _config_path().write_text(json.dumps(config, indent=2))


def update_config(**kwargs) -> dict:
    config = load_config()
    config.update(kwargs)
    save_config(config)
    return config


def get_api_base() -> str:
    return str(load_config().get("api_base", DEFAULT_API_BASE))


def get_web_url(path: str = "") -> str:
    """Build a frontend URL from the API base, e.g. /tasks/t-123."""
    api = get_api_base()
    base = api.replace("-backend", "").rstrip("/")
    return f"{base}/{path.lstrip('/')}" if path else base
