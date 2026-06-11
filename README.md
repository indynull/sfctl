# sfctl

Starfleet Control -- task review and evaluation CLI. Built with [Textual](https://textual.textualize.io/).

Review model responses, traces, and code diffs with syntax highlighting and real line numbers. Tabbed per-model views (Response / Trace / Diffs), an Overview page with history entry diffs and inline justification editing, local A/B/C scoring, and snippet yanking.

## Install

```
pip install git+https://github.com/YOUR_USER/sfctl.git
```

Or with [uv](https://docs.astral.sh/uv/):

```
uv tool install git+https://github.com/YOUR_USER/sfctl.git
```

## Usage

```
sfctl t-abc123def
```

On first run, you'll be prompted to select a browser profile for Starfleet cookies. The selection is saved to your OS config directory (`~/.config/starfleet/` on Linux, `~/Library/Application Support/starfleet/` on macOS).

### Options

```
sfctl t-abc123def -c /path/to/Cookies   # explicit cookie file
sfctl t-abc123def -v                     # verbose output
sfctl -f tests/fixtures/task_sample.json # load from fixture (offline)
sfctl --show-config                      # print config
sfctl --set api_base https://staging...  # set config value
sfctl --clear-config cookie_file         # clear a config key
```

### Fixture mode

Load from a captured JSON file instead of hitting the API:

```
sfctl --fixture tests/fixtures/task_sample.json
```

This is useful for offline development or testing UI changes without auth.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `1` `2` `3` | Switch to model A/B/C |
| `]` `[` | Next/previous model |
| `f` | Overview (history, feedback, justification) |
| `+` `-` | Upvote/downvote (context-aware: overall/response/code) |
| `j` | Edit justification |
| `y` | Yank selected diff snippet into justification |
| `c` | Copy rankings and justification to clipboard |
| `ctrl+f` | Fuzzy file search in current model |
| `r` | Refresh data from API |
| `ctrl+r` | Reset local scores and justification |
| `?` | Help |
| `q` | Quit |

The command palette (`ctrl+\`) provides fuzzy search across views, diffs, actions, and themes.

## Architecture

```
sfctl/
  api.py        async httpx client, cookie discovery, auth error handling
  app.py        main Textual app, keybindings, model switching
  cli.py        CLI entry point, --fixture / --set / --clear-config
  commands.py   command palette provider
  config.py     platformdirs-based config/data persistence
  models.py     Pydantic models (API) + dataclasses (app state)
  parsing.py    content parsing, diff extraction, trace formatting
  ranking.py    pure ranking computation and model identification
  scoring.py    local score and justification persistence
  screens.py    modal screens (yank, file search)
  widgets.py    DiffDisplay (Pygments highlighting), LazyCollapsible, trace widgets
```

## Development

```
git clone https://github.com/YOUR_USER/sfctl.git
cd sfctl
uv sync --group dev
```

### Run tests

```
uv run pytest tests/ -v
```

### Lint and format

```
uv run ruff check sfctl/ tests/
uv run ruff format sfctl/ tests/
uv run mypy sfctl/
```

Requires Python 3.13+.